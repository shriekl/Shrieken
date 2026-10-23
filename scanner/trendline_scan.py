#!/usr/bin/env python3
"""Trendline breakout/retest scanner (Binance spot, public data-api.binance.vision).
Usage: python3 trendline_scan.py [--all] [--top N] [--symbols BTCUSDT,ETHUSDT] [--tf 4h,1d]
Outputs latest.json + summary.txt; state.json tracks changes between runs."""
import json, sys, time, argparse, os, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import numpy as np, requests, re
import datasrc

BASE = "https://data-api.binance.vision/api/v3"
DIR = os.path.dirname(os.path.abspath(__file__))
TFS = ["4h", "12h", "1d", "3d", "1w", "1M"]
STABLES = {"USDC","FDUSD","TUSD","BUSD","DAI","USDP","USDD","PYUSD","EUR","EURI","AEUR","USDE","USD1","XUSD","RLUSD","BFUSD","U","USD0","USDS","FRAX","EURC","PAXG","WBTC","WBETH","GBP","TRY","BRL"}
LOOKBACK, PIV, MAX_BREAK_AGE = 150, 3, 20
PRIO = {"RETEST_CONFIRMED_VOLUME": 0, "RETESTING": 1, "BREAKOUT_FRESH": 2, "FAILED": 3}
S = requests.Session()

def get(path, **p):
    for a in range(4):
        r = S.get(BASE + path, params=p, timeout=20)
        if r.status_code == 200: return r.json()
        time.sleep(2 * (a + 1))
    r.raise_for_status()

STOCKS = {"SNDKB","SOXLB","SPCXB","CRCLB","TSLAB","MSTRB","SKHYB","INTCB","NVDAB","MUB","SNXXB","KORUB","AMDB","QQQB",
          "AAPLB","GOOGLB","METAB","AMZNB","MSFTB","COINB","HOODB","SPYB","PLTRB","NFLXB","ORCLB","AVGOB","TSMB","BABAB","XAUT","RWUSD"}
B_ALLOW = {"BNB","BOB","BOMB","KNOB","PROB","GLMB","LOOB"}
def is_excluded(base):
    if base in STABLES or base in STOCKS: return True
    if len(base) > 4 and base.endswith(("UP","DOWN","BULL","BEAR")): return True
    if base.endswith("B") and len(base) >= 4 and base not in B_ALLOW and base.isalpha(): return True  # tokenized stocks (xStock style 'B' suffix)
    return False

SOURCES = set()
def universe(n):
    t, src = datasrc.tickers(); SOURCES.add(src)
    out = []
    for sym, qv, _ in t:
        s = sym
        if not s.endswith("USDT"): continue
        b = s[:-4]
        if is_excluded(b): continue
        out.append((qv, s))
    return [s for _, s in sorted(out, reverse=True)[:n]]

def ema(x, n):
    a, o = 2 / (n + 1), np.empty_like(x); o[0] = x[0]
    for i in range(1, len(x)): o[i] = a * x[i] + (1 - a) * o[i-1]
    return o

def rsi(c, n=14):
    d = np.diff(c); g = np.clip(d, 0, None); l = np.clip(-d, 0, None)
    ag, al = g[:n].mean(), l[:n].mean()
    for i in range(n, len(d)): ag = (ag*(n-1)+g[i])/n; al = (al*(n-1)+l[i])/n
    return 100.0 if al == 0 else 100 - 100/(1+ag/al)

def atr(h, l, c, n=14):
    tr = np.maximum(h[1:]-l[1:], np.maximum(abs(h[1:]-c[:-1]), abs(l[1:]-c[:-1])))
    a = np.empty(len(c)); a[:n] = np.nan; a[n] = tr[:n].mean()
    for i in range(n+1, len(c)): a[i] = (a[i-1]*(n-1)+tr[i-1])/n
    return a

def pivots(x, k, high=True):
    out = []
    for i in range(k, len(x)-k):
        w = x[i-k:i+k+1]
        if (high and x[i] == w.max()) or (not high and x[i] == w.min()): out.append(i)
    return out

def bull_setup(o, h, l, c, v, A):
    """Find best descending resistance line broken by a close in last MAX_BREAK_AGE bars. Works on any series
    (bearish mirror is run on negated prices). Returns dict or None."""
    n = len(c); last = n - 1; start = max(0, n - LOOKBACK)
    ph = [i for i in pivots(h, PIV) if i >= start]
    best = None
    for a_ in range(len(ph)):
        for b_ in range(a_+1, len(ph)):
            i, j = ph[a_], ph[b_]
            if h[j] >= h[i] or j - i < 3: continue
            m = (h[j]-h[i])/(j-i); line = lambda t: h[i] + m*(t-i)
            # find first break after j
            brk = None; ok = True
            for t in range(i+1, n):
                lv = line(t); buf = max(0.3*A[t] if not np.isnan(A[t]) else 0, 0.005*abs(lv))
                if c[t] > lv + buf:
                    if t <= j: ok = False; break
                    brk = t; break
                if c[t] > lv and t < j: ok = False; break
            if not ok or brk is None or last - brk > MAX_BREAK_AGE: continue
            tol = 0.25*np.nanmean(A[i:brk+1])
            touches = sum(1 for p in ph if i <= p < brk and abs(h[p]-line(p)) <= tol)
            score = (touches, j - i, brk)
            if best is None or score > best["score"]:
                best = dict(score=score, i=i, j=j, m=m, brk=brk, touches=touches)
    if not best: return None
    i, m, brk = best["i"], best["m"], best["brk"]; line = lambda t: h[i] + m*(t-i)
    after = range(brk+1, n)
    failed_at = next((t for t in after if c[t] < line(t) - 0.1*A[t]), None)
    ret_bars = [t for t in after if l[t] <= max(line(t), line(brk)) + 0.5*A[t]]
    rb = min(ret_bars, key=lambda t: l[t]) if ret_bars else None
    vma = v[-21:-1].mean(); vr = v[last]/vma if vma else 0
    if failed_at is not None:
        if last - failed_at > 3: return None
        state = "FAILED"
    elif c[last] - line(last) > 3*A[last] and not (rb is not None and last - rb <= 3):
        return None  # extended, no actionable retest
    elif rb is not None and rb < last and last - rb <= 5 and c[last] > o[last] and vr > 1.5 and c[last] > h[rb]:
        state = "RETEST_CONFIRMED_VOLUME"
    elif rb is not None and last - rb <= 5:
        state = "RETESTING"
    elif last - brk <= 2 and rb is None:
        state = "BREAKOUT_FRESH"
    else:
        return None
    a = A[last]; lv = line(last); blev = line(brk)
    zone_lo, zone_hi = sorted([lv, (h[brk]+l[brk])/2])
    entry = c[last] if state == "RETEST_CONFIRMED_VOLUME" else (zone_lo+zone_hi)/2
    if rb is not None: sl = min(l[rb:].min(), lv) - 0.5*a; sl_basis = "retest swing low - 0.5ATR"
    else: sl = lv - 1.0*a; sl_basis = "line - 1ATR"
    bo_entry = c[last]; bo_sl = min(l[brk], lv) - 0.5*a
    height = h[i:brk+1].max() - l[i:brk+1].min()
    tps = sorted({h[p] for p in pivots(h, PIV) if h[p] > max(entry, c[last]) + max(0.2*a, 1.0*(entry-sl))})
    tps = [x for x in tps]
    # keep strictly increasing next swing highs (nearest first), dedupe close levels
    clean = []
    for x in tps:
        if not clean or x - clean[-1] > 0.5*a: clean.append(x)
    for k in (1.0, 1.618, 2.618):
        if len(clean) >= 3: break
        mm = blev + k*height
        if mm <= max(entry, c[last]) + (entry-sl): mm = entry + (len(clean)+1.5)*(entry-sl)
        if not clean or mm > clean[-1] + 0.5*a: clean.append(mm)
    tps = clean[:3]
    risk = entry - sl; bo_risk = bo_entry - bo_sl
    rr = [round((tp-entry)/risk, 2) if risk > 0 else None for tp in tps]
    bo_rr = [round((tp-bo_entry)/bo_risk, 2) if bo_risk > 0 and tp > bo_entry else None for tp in tps]
    return dict(state=state, i=i, j=best["j"], brk=brk, rb=rb, touches=best["touches"], line_now=lv,
                break_level=blev, zone=[zone_lo, zone_hi], entry=entry, sl=sl, sl_basis=sl_basis,
                bo_entry=bo_entry, bo_sl=bo_sl, tps=tps, rr=rr, bo_rr=bo_rr, vol_ratio=vr, atr=a,
                bars_since_break=last-brk, height=height)

def analyze(sym, tf):
    k, src = datasrc.klines(sym, tf, 500); SOURCES.add(src)
    if len(k) < 40: return []
    cl = [float(x[4]) for x in k[-30:]]
    if max(cl)/min(cl) < 1.02: return []  # pegged/stable asset
    forming = k[-1]; k = k[:-1]
    # MEXC/resampled fallback may return a closed last candle; detect by time
      # drop forming candle
    arr = np.array([[float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in k])
    o, h, l, c, v = arr.T; t = [x[0] for x in k]
    A = atr(h, l, c)
    lo_, lh_, ll_, lc_ = np.log(o), np.log(h), np.log(l), np.log(c); LA = atr(lh_, ll_, lc_)
    e20, e50, e200 = ema(c, 20)[-1], ema(c, 50)[-1], (ema(c, 200)[-1] if len(c) >= 200 else None)
    r = rsi(c)
    ts = lambda ms: dt.datetime.fromtimestamp(ms/1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    res = []
    for side in ("LONG", "SHORT"):
        if side == "LONG": s = bull_setup(lo_, lh_, ll_, lc_, v, LA)
        else:
            s = bull_setup(-lo_, -ll_, -lh_, -lc_, v, LA)
            if s:
                for key in ("line_now","break_level","entry","sl","bo_entry","bo_sl"): s[key] = -s[key]
                s["zone"] = [-x for x in s["zone"]]; s["tps"] = [-x for x in s["tps"]]
        if not s: continue
        if side == "LONG":
            for key in ("line_now","break_level","entry","sl","bo_entry","bo_sl"): s[key] = float(np.exp(s[key]))
            s["zone"] = [float(np.exp(x)) for x in s["zone"]]; s["tps"] = [float(np.exp(x)) for x in s["tps"]]
        else:
            for key in ("line_now","break_level","entry","sl","bo_entry","bo_sl"): s[key] = float(np.exp(s[key]))
            s["zone"] = sorted(float(np.exp(x)) for x in s["zone"]); s["tps"] = [float(np.exp(x)) for x in s["tps"]]
        s["atr"] = float(A[-1])
        if side == "SHORT": s["sl_basis"] = s["sl_basis"].replace("swing low - ", "swing high + ").replace("line - ", "line + ")
        # recompute R:R in price space
        def rrs_(entry, sl, tps):
            rk = abs(entry - sl)
            return [round(float(abs(tp-entry)/rk), 2) if rk > 0 and (tp-entry)*(entry-sl) > 0 else None for tp in tps]
        s["rr"] = rrs_(s["entry"], s["sl"], s["tps"]); s["bo_rr"] = rrs_(s["bo_entry"], s["bo_sl"], s["tps"])
        # rank
        st = 2 if s["touches"] >= 3 else 1 if s["touches"] >= 2 else 0
        dist = abs(c[-1]-s["line_now"])/s["atr"]
        loc = 2 if dist <= 1 else 1 if dist <= 2 else 0
        conf = {"RETEST_CONFIRMED_VOLUME": 2, "RETESTING": 1, "BREAKOUT_FRESH": 1, "FAILED": 0}[s["state"]]
        mom_ok = (50 <= r <= 72) if side == "LONG" else (28 <= r <= 50)
        mv = int(mom_ok) + int(s["vol_ratio"] > 1.5)
        tp2 = s["rr"][1] if len(s["rr"]) > 1 and s["rr"][1] else (s["rr"][0] or 0) if s["rr"] else 0
        rrs = 2 if tp2 >= 3 else 1 if tp2 >= 2 else 0
        rank = st + loc + conf + mv + rrs
        if s["state"] == "FAILED": rank = min(rank, 3)
        rd = lambda x: float(f"{x:.6g}") if x is not None else None
        res.append(dict(symbol=sym, tf=tf, side=side, state=s["state"], rank=rank,
            rank_parts=dict(structure=st, location=loc, confirmation=conf, momentum_volume=mv, rr=rrs),
            anchor1_ms=t[s["i"]], anchor2_ms=t[s["j"]],
            anchor1_price=rd(h[s["i"]] if side == "LONG" else l[s["i"]]), anchor2_price=rd(h[s["j"]] if side == "LONG" else l[s["j"]]),
            close=rd(c[-1]), last_closed=ts(t[-1]), line_from=ts(t[s["i"]]), line_to=ts(t[s["j"]]),
            break_candle=ts(t[s["brk"]]), bars_since_break=s["bars_since_break"], touches=s["touches"],
            retest_candle=ts(t[s["rb"]]) if s["rb"] is not None else None,
            trendline_now=rd(s["line_now"]), breakout_level=rd(s["break_level"]),
            pullback_zone=[rd(x) for x in s["zone"]], pullback_entry=rd(s["entry"]), sl=rd(s["sl"]), sl_basis=s["sl_basis"],
            breakout_entry=rd(s["bo_entry"]), breakout_sl=rd(s["bo_sl"]),
            tps=[rd(x) for x in s["tps"]], rr_pullback=s["rr"], rr_breakout=s["bo_rr"],
            ema20=rd(e20), ema50=rd(e50), ema200=rd(e200), rsi14=round(r, 1), atr14=rd(s["atr"]),
            vol_ratio=round(s["vol_ratio"], 2), last_open=rd(o[-1]), last_high=rd(h[-1]), last_low=rd(l[-1]),
            forming=dict(open=float(forming[1]), high=float(forming[2]), low=float(forming[3]), price=float(forming[4]),
                         opened=ts(forming[0]))))
    return res

def fmt(x):
    pr = lambda v: "-" if v is None else f"{v:g}"
    tp = " / ".join(f"{pr(t)} ({r}R)" for t, r in zip(x["tps"], x["rr_pullback"]))
    return (f"[{x['rank']}/10] {x['symbol']} {x['tf']} {x['side']} {x['state']}  close {pr(x['close'])} (forming {pr(x['forming']['price'])})\n"
            f"   line now {pr(x['trendline_now'])} | breakout lvl {pr(x['breakout_level'])} ({x['break_candle']}, {x['bars_since_break']} bars ago, {x['touches']} touches)\n"
            f"   pullback zone {pr(x['pullback_zone'][0])}-{pr(x['pullback_zone'][1])} entry {pr(x['pullback_entry'])} SL {pr(x['sl'])} [{x['sl_basis']}]\n"
            f"   breakout entry {pr(x['breakout_entry'])} SL {pr(x['breakout_sl'])} | TPs {tp} | breakout R:R {x['rr_breakout']}\n"
            f"   RSI {x['rsi14']} vol x{x['vol_ratio']} EMA20/50/200 {pr(x['ema20'])}/{pr(x['ema50'])}/{pr(x['ema200'])}")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--all", action="store_true")
    ap.add_argument("--top", type=int, default=60); ap.add_argument("--symbols"); ap.add_argument("--tf"); ap.add_argument("--state", default="state.json"); ap.add_argument("--universe", default="aster_kcex", choices=["aster_kcex","top"])
    a = ap.parse_args(); t0 = time.time()
    syms = a.symbols.split(",") if a.symbols else (__import__("universe").pairs() if a.universe == "aster_kcex" else universe(a.top))
    tfs = a.tf.split(",") if a.tf else TFS
    jobs = [(s, tf) for s in syms for tf in tfs]; hits, errs = [], []
    def run(j):
        try: return analyze(*j)
        except Exception as e: errs.append(f"{j}: {e}"); return []
    with ThreadPoolExecutor(4) as ex:
        for r in ex.map(run, jobs): hits += r
    for x in hits:
        w = []
        if x["side"] == "LONG" and x["rsi14"] > 75: w.append("RSI overbought")
        if x["side"] == "SHORT" and x["rsi14"] < 25: w.append("RSI oversold")
        ref = x["pullback_entry"]; 
        if ref and x["sl"] and abs(ref - x["sl"]) / ref > 0.15: w.append(f"wide SL {abs(ref-x['sl'])/ref*100:.0f}%")
        if x["atr14"] and abs(x["close"] - x["trendline_now"]) > 2 * x["atr14"]: w.append("extended from line")
        if x["rr_pullback"] and (x["rr_pullback"][0] or 0) < 1.5: w.append("TP1 R:R < 1.5")
        if x["touches"] < 3: w.append("only 2 touches")
        if x["state"] == "FAILED": w.append("false breakout")
        if x["tf"] in ("1w", "1M") and x["ema200"] is None: w.append("short history (no EMA200)")
        x["warnings"] = w
    hits.sort(key=lambda x: (PRIO[x["state"]], x["side"] != "LONG", -x["rank"]))
    sp = os.path.join(DIR, a.state)
    prev = json.load(open(sp)) if os.path.exists(sp) else {}
    cur = {}
    for x in hits:
        key = f"{x['symbol']}|{x['tf']}|{x['side']}"; sig = f"{x['state']}|{x['break_candle']}"
        cur[key] = sig; x["change"] = "NEW" if key not in prev else ("CHANGED" if prev[key] != sig else "SAME")
    json.dump(cur, open(sp, "w"))
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M IST")
    out = dict(generated_ts=int(time.time()), sources=sorted(SOURCES), generated=now, runtime_s=round(time.time()-t0, 1), symbols=len(syms), errors=errs, hits=hits)
    json.dump(out, open(os.path.join(DIR, "latest.json"), "w"), indent=1)
    show = hits if a.all else [x for x in hits if x["change"] != "SAME"]
    lines = [f"Trendline scan {now} | {len(syms)} symbols x {len(tfs)} TF | {len(hits)} setups, {len(show)} shown ({'all' if a.all else 'new/changed'}) | {out['runtime_s']}s"]
    cur_state = None
    for x in show:
        if x["state"] != cur_state: cur_state = x["state"]; lines.append(f"\n=== {cur_state} ===")
        lines.append(f"({x['change']}) " + fmt(x))
    if errs: lines.append(f"\nErrors: {len(errs)} e.g. {errs[:3]}")
    txt = "\n".join(lines); open(os.path.join(DIR, "summary.txt"), "w").write(txt); print(txt)

if __name__ == "__main__": main()
