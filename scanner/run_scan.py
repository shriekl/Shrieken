#!/usr/bin/env python3
"""Scan one timeframe and write the website payload (plaintext JSON).

The signal math is trendline_scan.analyze. This script adds the website's
warnings, in-zone flag, and rank sort, and throttles exchange requests.
"""
import os, sys, json, time, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import datasrc
import trendline_scan as TS
import site_rules

# ~4.5 requests/second. Under MEXC's public kline budget (about 5/s) and
# comfortable for Binance weight limits.
REQUEST_INTERVAL = 0.22
WORKERS = 8


class Limiter:
    def __init__(self, interval):
        self.interval = interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self):
        with self._lock:
            now = time.time()
            start = max(now, self._next)
            self._next = start + self.interval
            delay = start - now
        if delay > 0:
            time.sleep(delay)


def universe_pairs(symbols=None):
    if symbols:
        return [s.strip().upper() for s in symbols if s.strip()]
    path = os.path.join(HERE, "universe_aster_kcex.json")
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for coin in data["coins"]:
        if not (coin.get("binance") or coin.get("mexc")):
            continue
        pair = coin["pair"]
        base = pair[:-4] if pair.endswith("USDT") else coin["base"]
        if TS.is_excluded(base):
            continue
        out.append(pair)
    return out


def _load_binance_symbols():
    if not datasrc._binance_allowed():
        return set()
    try:
        info = datasrc._get(datasrc.BINANCE, "/exchangeInfo", {"permissions": "SPOT"})
        syms = {x["symbol"] for x in info["symbols"] if x.get("status") == "TRADING"}
        datasrc._mark("binance")
        return syms
    except Exception as e:
        datasrc._mark("mexc", str(e))
        return set()


def install_fast_path(binance_syms):
    """Pairs missing on Binance spot go straight to MEXC (same idea as web/worker.py)."""
    orig = datasrc.klines

    def _fast(sym, tf, limit=500, start_ms=None):
        if binance_syms and sym not in binance_syms:
            rows = datasrc._mexc_klines(sym, tf, limit)
            if start_ms:
                rows = [r for r in rows if r[0] >= start_ms]
            return rows, "mexc"
        return orig(sym, tf, limit, start_ms)

    datasrc.klines = _fast


def prepare_source():
    source = datasrc.probe_binance()
    print(f"data source probe: {source}" + (f" ({datasrc.status.get('last_error')})" if source == "mexc" else ""), flush=True)
    syms = _load_binance_symbols() if source == "binance" else set()
    install_fast_path(syms)
    if syms:
        print(f"binance spot symbols: {len(syms)}", flush=True)
    return source


def _annotate(hits, state, track_changes):
    kept = []
    for x in hits:
        if TS.is_excluded(x["symbol"][:-4]):
            continue
        x["warnings"] = site_rules.warnings(x)
        x["in_zone"] = site_rules.in_zone(x)
        key = f"{x['symbol']}|{x['tf']}|{x['side']}"
        sig = f"{x['state']}|{x['break_candle']}"
        if not track_changes:
            x["change"] = "SAME"
        elif key not in state:
            x["change"] = "NEW"
        elif state[key] != sig:
            x["change"] = "CHANGED"
        else:
            x["change"] = "SAME"
        state[key] = sig
        kept.append(x)
    kept.sort(key=lambda x: (-x["rank"], TS.PRIO[x["state"]]))
    return kept


def scan_tf(tf, symbols, state=None, progress=True, track_changes=None):
    if tf not in ("15m", "30m", "1h", "4h", "12h", "1d", "3d", "1w", "1M"):
        raise SystemExit(f"unsupported timeframe {tf}")
    t0 = time.time()
    TS.SOURCES.clear()
    state = {} if state is None else state
    if track_changes is None:
        track_changes = bool(state)
    limiter = Limiter(REQUEST_INTERVAL)
    hits, errs = [], []
    done = [0]
    lock = threading.Lock()

    def run(sym):
        limiter.wait()
        try:
            found = TS.analyze(sym, tf)
        except Exception as e:
            return sym, [], f"{sym}: {str(e)[:120]}"
        return sym, found, None

    with ThreadPoolExecutor(WORKERS) as pool:
        futs = [pool.submit(run, sym) for sym in symbols]
        for fut in as_completed(futs):
            sym, found, err = fut.result()
            with lock:
                done[0] += 1
                n = done[0]
            if err:
                errs.append(err)
            else:
                hits.extend(found)
            if progress and (n % 50 == 0 or n == len(symbols)):
                print(f"  {tf} {n}/{len(symbols)}  setups {len(hits)}  errors {len(errs)}", flush=True)

    hits = [h for h in hits if not TS.is_excluded(h["symbol"][:-4])]
    hits = _annotate(hits, state, track_changes)
    # Too many failures means the file would wipe a good previous scan.
    if symbols and len(errs) > len(symbols) * 0.5:
        raise RuntimeError(f"{tf}: {len(errs)}/{len(symbols)} symbols failed, e.g. {errs[:3]}")
    payload = {
        "tf": tf,
        "generated_ts": int(time.time()),
        "runtime_s": round(time.time() - t0, 1),
        "symbols": len(symbols),
        "errors": len(errs),
        "error_samples": errs[:5],
        "sources": sorted(TS.SOURCES),
        "hits": hits,
    }
    return payload, state


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tf", required=True)
    ap.add_argument("--symbols", help="Comma-separated override. Default: committed universe.")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument("--state", help="Optional state JSON path (plaintext, local runs only)")
    args = ap.parse_args()
    prepare_source()
    syms = universe_pairs(args.symbols.split(",") if args.symbols else None)
    state = {}
    if args.state and os.path.exists(args.state):
        state = json.load(open(args.state, encoding="utf-8"))
    payload, state = scan_tf(args.tf, syms, state)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    json.dump(payload, open(args.out, "w", encoding="utf-8"), indent=1)
    if args.state:
        json.dump(state, open(args.state, "w", encoding="utf-8"))
    print(f"{args.tf}: {len(payload['hits'])} setups, {payload['errors']} errors, {payload['runtime_s']}s", flush=True)


if __name__ == "__main__":
    main()
