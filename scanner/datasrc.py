"""Market data with automatic fallback: Binance (data-api.binance.vision) -> MEXC.

Env FORCE_SOURCE=mexc skips Binance; BINANCE_BASE overrides the Binance URL.

A Binance block (403/451, network error, or repeated rate-limit) is remembered for
the rest of the process. Later calls in the same run go straight to MEXC.
HTTP 400 stays per-symbol: that pair is missing on Binance and only that call
falls back.
"""
import os, time, threading, requests

BINANCE = os.environ.get("BINANCE_BASE", "https://data-api.binance.vision/api/v3")
MEXC = "https://api.mexc.com/api/v3"
UA = "Mozilla/5.0 (compatible; ShriekenScanner/1.0)"
_tls = threading.local()
_lock = threading.Lock()
status = {"last_source": None, "binance_ok": None, "last_error": None, "binance_locked": False}
MEXC_IV = {"1h": "60m", "4h": "4h", "1d": "1d", "1w": "1W", "1M": "1M", "15m": "15m", "30m": "30m", "1m": "1m", "5m": "5m"}
RESAMPLE = {"12h": ("4h", 3, 12 * 3600_000, 0), "3d": ("1d", 3, 3 * 86400_000, 86400_000)}  # Binance 3d buckets are offset by 1 day
# Once Binance is blocked, stay on MEXC until this process exits.
REST_OF_RUN = 10 ** 12
_down_until = [0.0]


def _session():
    s = getattr(_tls, "s", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = UA
        _tls.s = s
    return s


def _get(base, path, params, tries=2):
    last = None
    for a in range(tries):
        try:
            r = _session().get(base + path, params=params, timeout=12)
            if r.status_code == 200:
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:120]}"
            if r.status_code in (403, 451):
                break
            if r.status_code in (418, 429):
                ra = r.headers.get("Retry-After", "")
                delay = float(ra) if str(ra).isdigit() else 1.5 * (a + 1)
                time.sleep(min(delay, 15))
                continue
        except Exception as e:
            last = str(e)[:150]
        time.sleep(1.5 * (a + 1))
    raise RuntimeError(last or "request failed")


def _binance_allowed():
    if os.environ.get("FORCE_SOURCE", "").lower() == "mexc":
        return False
    with _lock:
        return time.time() > _down_until[0]


def _mark(src, err=None):
    with _lock:
        status["last_source"] = src
        if src == "binance":
            status["binance_ok"] = True
            return
        if not err:
            return
        status["binance_ok"] = False
        status["last_error"] = err
        # A bad symbol (HTTP 400) must not take the whole run off Binance.
        if err != "forced mexc" and not str(err).startswith("HTTP 400"):
            _down_until[0] = time.time() + REST_OF_RUN
            status["binance_locked"] = True


def probe_binance():
    """One Binance request. On failure, the rest of this process uses MEXC only."""
    if not _binance_allowed():
        return "mexc"
    try:
        _get(BINANCE, "/klines", {"symbol": "BTCUSDT", "interval": "1m", "limit": 1})
        _mark("binance")
        return "binance"
    except Exception as e:
        _mark("mexc", str(e))
        return "mexc"


def _norm(rows):  # -> [open_ms, o, h, l, c, v]
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])] for r in rows]


def _resample(rows, n, span, off=0):
    out = {}
    for r in rows:
        k = r[0] - (r[0] - off) % span
        if k not in out:
            out[k] = [k, r[1], r[2], r[3], r[4], r[5]]
        else:
            b = out[k]
            b[2] = max(b[2], r[2])
            b[3] = min(b[3], r[3])
            b[4] = r[4]
            b[5] += r[5]
    res = [out[k] for k in sorted(out)]
    # drop leading partial bucket
    if res and rows[0][0] != res[0][0]:
        res = res[1:]
    return res


def _mexc_klines(sym, tf, limit):
    if tf in RESAMPLE:
        base, n, span, off = RESAMPLE[tf]
        rows = _norm(_get(MEXC, "/klines", {"symbol": sym, "interval": MEXC_IV[base], "limit": min(1000, limit * n + n)}))
        return _resample(rows, n, span, off)[-limit:]
    return _norm(_get(MEXC, "/klines", {"symbol": sym, "interval": MEXC_IV[tf], "limit": min(1000, limit)}))


def klines(sym, tf, limit=500, start_ms=None):
    """Returns (rows, source). Last row is the still-forming candle."""
    err = "forced mexc"
    if _binance_allowed():
        try:
            p = {"symbol": sym, "interval": tf, "limit": limit}
            if start_ms:
                p["startTime"] = start_ms
            rows = _norm(_get(BINANCE, "/klines", p))
            _mark("binance")
            return rows, "binance"
        except Exception as e:
            err = str(e)
            if not str(err).startswith("HTTP 400"):
                _mark("mexc", err)
    rows = _mexc_klines(sym, tf, limit)
    if start_ms:
        rows = [r for r in rows if r[0] >= start_ms]
    if not status.get("binance_locked"):
        _mark("mexc", err)
    return rows, "mexc"


def tickers():
    """Returns ([(symbol, quoteVolume, lastPrice)], source)."""
    err = "forced mexc"
    if _binance_allowed():
        try:
            t = _get(BINANCE, "/ticker/24hr", {"type": "MINI"})
            _mark("binance")
            return [(x["symbol"], float(x["quoteVolume"]), float(x["lastPrice"])) for x in t], "binance"
        except Exception as e:
            err = str(e)
            if not str(err).startswith("HTTP 400"):
                _mark("mexc", err)
    t = _get(MEXC, "/ticker/24hr", {})
    if not status.get("binance_locked"):
        _mark("mexc", err)
    return [(x["symbol"], float(x.get("quoteVolume") or 0), float(x["lastPrice"])) for x in t], "mexc"
