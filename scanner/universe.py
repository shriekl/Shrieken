#!/usr/bin/env python3
"""Universe = coins with USDT perps on BOTH Aster and KCEX.
Aster: https://fapi.asterdex.com/fapi/v1/exchangeInfo (PERPETUAL, TRADING).
KCEX: https://www.kcex.com/fapi/v1/contract/detail (geo-blocked from this box -> 403),
      fallback: TradingView public symbol search (exchange=KCEX, perpetual swaps), then cache.
Cache: universe_aster_kcex.json, refreshed when older than 24h. Usage: python3 universe.py [--refresh]"""
import os, re, json, time, sys, requests
DIR = os.path.dirname(os.path.abspath(__file__)); CACHE = os.path.join(DIR, "universe_aster_kcex.json")
UA = {"User-Agent": "Mozilla/5.0"}
ALIAS = {"PUMPFUN": "PUMP", "LIGHTER": "LIT", "LUNA2": "LUNA2", "BEAMX": "BEAM"}
NONCRYPTO = {"XAU","XAG","XAUT","PAXG","CL","BZ","NG","HG","XPT","XPD","SPX500","NAS100","US30","AAPL","TSLA","NVDA","AMZN","META","MSFT",
             "GOOGL","GOOG","COIN","HOOD","MSTR","QQQ","SPY","NET","PLTR","NFLX","ORCL","AVGO","TSM","BABA","AMD","INTC","CRCL","MU","GME","IBM",
             "DIS","UBER","SNDK","USD1","USDC","USDE","FDUSD","RLUSD","EUR","GBP","JPY"}

def norm(b):
    b = b.upper().replace("_", "")
    m = re.match(r"^(1000000|100000|10000|1000|1M)([A-Z].*)$", b)
    if m: b = m.group(2)
    return ALIAS.get(b, b)

def aster():
    d = requests.get("https://fapi.asterdex.com/fapi/v1/exchangeInfo", timeout=20, headers=UA).json()
    return {norm(s["baseAsset"]): s["symbol"] for s in d["symbols"]
            if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT" and s["baseAsset"].isascii()}

def kcex_direct():
    r = requests.get("https://www.kcex.com/fapi/v1/contract/detail", timeout=20, headers=UA); r.raise_for_status()
    return {norm(x["baseCoin"]): x["symbol"] for x in r.json()["data"] if x.get("quoteCoin") == "USDT" and x.get("state") == 0 and not x.get("isHidden")}

def kcex_tradingview():
    out, start = {}, 0
    while True:
        r = requests.get("https://symbol-search.tradingview.com/symbol_search/v3/", timeout=20,
            headers={**UA, "Origin": "https://www.tradingview.com", "Referer": "https://www.tradingview.com/"},
            params={"text": "", "exchange": "KCEX", "search_type": "crypto", "start": start, "hl": 0, "lang": "en", "domain": "production"})
        r.raise_for_status(); d = r.json(); syms = d.get("symbols", [])
        for s in syms:
            sym = s["symbol"]
            if "perpetual" in s.get("typespecs", []) and sym.endswith("USDT.P"): out[norm(sym[:-6])] = sym
        start += len(syms)
        if not syms or d.get("symbols_remaining", 0) <= 0: break
        time.sleep(0.3)
    if len(out) < 50: raise RuntimeError(f"TradingView KCEX list too small ({len(out)})")
    return out

def chart_sources():
    bn = mx = set()
    try: bn = {s["symbol"] for s in requests.get("https://data-api.binance.vision/api/v3/exchangeInfo", timeout=30).json()["symbols"] if s["status"] == "TRADING"}
    except Exception: pass
    try: mx = {s["symbol"] for s in requests.get("https://api.mexc.com/api/v3/exchangeInfo", timeout=30).json()["symbols"]}
    except Exception: pass
    return bn, mx

def build():
    a = aster(); kc_src = "kcex_api"
    try: k = kcex_direct()
    except Exception as e:
        kc_src = f"tradingview (kcex api failed: {str(e)[:80]})"; k = kcex_tradingview()
    inter = sorted((set(a) & set(k)) - NONCRYPTO)
    bn, mx = chart_sources()
    coins = []
    for b in inter:
        pair = b + "USDT"
        coins.append(dict(base=b, pair=pair, aster=a[b], kcex=k[b], binance=pair in bn, mexc=pair in mx))
    return dict(updated_ts=int(time.time()), updated=time.strftime("%Y-%m-%d %H:%M IST"), aster_count=len(a), kcex_count=len(k),
                kcex_source=kc_src, excluded_noncrypto=sorted((set(a) & set(k)) & NONCRYPTO), coins=coins,
                missing_data=[c["base"] for c in coins if not (c["binance"] or c["mexc"])])

def load(max_age=86400, refresh=False):
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else None
    if cache and not refresh and time.time() - cache["updated_ts"] < max_age: return cache
    try:
        u = build(); json.dump(u, open(CACHE, "w"), indent=1); return u
    except Exception as e:
        if cache: cache["stale_reason"] = str(e)[:200]; return cache
        raise

def pairs(source=None):
    """USDT spot pairs for the intersection that have chart data. source='mexc' -> only MEXC-listed."""
    u = load()
    return [c["pair"] for c in u["coins"] if (c["mexc"] if source == "mexc" else (c["binance"] or c["mexc"]))]

if __name__ == "__main__":
    u = load(refresh="--refresh" in sys.argv)
    print(f"Aster {u['aster_count']} | KCEX {u['kcex_count']} via {u['kcex_source']} | intersection {len(u['coins'])} (updated {u['updated']})")
    print("excluded non-crypto:", u["excluded_noncrypto"]); print("no MEXC/Binance spot data:", u["missing_data"])
    print("MEXC-only missing:", [c["base"] for c in u["coins"] if not c["mexc"] and c["binance"]])
    print(" ".join(c["base"] for c in u["coins"]))
