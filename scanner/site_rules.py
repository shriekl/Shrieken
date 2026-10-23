"""Card flags from the previous website worker (web/worker.py).

Signal math stays in trendline_scan.py. This file only copies the website's
warning list and in-zone test.
"""


def warnings(x):
    w = []
    if x["side"] == "LONG" and x["rsi14"] > 75:
        w.append("RSI overbought")
    if x["side"] == "SHORT" and x["rsi14"] < 25:
        w.append("RSI oversold")
    ref = x["pullback_entry"]
    if ref and x["sl"] and abs(ref - x["sl"]) / ref > 0.15:
        w.append(f"wide SL {abs(ref-x['sl'])/ref*100:.0f}%")
    if x["atr14"] and abs(x["close"] - x["trendline_now"]) > 2 * x["atr14"]:
        w.append("extended")
    if x["rr_pullback"] and (x["rr_pullback"][0] or 0) < 1.5:
        w.append("TP1 R:R<1.5")
    if x["touches"] < 3:
        w.append("2 touches")
    if x["state"] == "FAILED":
        w.append("false breakout")
    if x["vol_ratio"] < 1:
        w.append("weak volume")
    return w


def in_zone(x):
    lo, hi = sorted(x["pullback_zone"])
    return lo <= x["forming"]["price"] <= hi
