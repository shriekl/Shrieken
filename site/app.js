/* Shriek Luna — static reader. Signals are AES-GCM ciphertext until the password unlocks them. */
const TFS = ["15m", "30m", "1h", "4h", "12h", "1d", "3d", "1w", "1M"];
const PRIO = { RETEST_CONFIRMED_VOLUME: 0, RETESTING: 1, BREAKOUT_FRESH: 2, FAILED: 3 };
const STATE_NAME = {
  RETEST_CONFIRMED_VOLUME: "Retest confirmed + volume",
  RETESTING: "Retesting line",
  BREAKOUT_FRESH: "Fresh breakout",
  FAILED: "Failed breakout",
};
const PW_KEY = "shrieken.pw";
const IST = 19800;
const BINANCE = "https://data-api.binance.vision/api/v3";
const MEXC = "https://api.mexc.com/api/v3";
const MEXC_IV = { "15m": "15m", "30m": "30m", "1h": "60m", "4h": "4h", "1d": "1d", "1w": "1W", "1M": "1M" };
const RESAMPLE = {
  "12h": { base: "4h", span: 12 * 3600 * 1000, off: 0 },
  "3d": { base: "1d", span: 3 * 86400 * 1000, off: 86400 * 1000 },
};

const $ = (id) => document.getElementById(id);
let password = "";
let meta = { tfs: {} };
let tf = localStorage.getItem("shrieken.tf") || "4h";
let filter = localStorage.getItem("shrieken.filter") || "all";
let side = localStorage.getItem("shrieken.side") || "ALL";
let hits = [];
let view = [];
const cache = {};
const keyCache = new Map();
let binanceBlocked = false;
let binanceProbe = null;
let chart, candleSeries, volumeSeries, pollTimer, chartReq = 0, chartSym = "", chartSide = "";

if (!TFS.includes(tf)) tf = "4h";

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function fmt(v) {
  if (v == null || !Number.isFinite(+v)) return "–";
  const n = +v;
  const a = Math.abs(n);
  const d = a >= 100 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 8;
  return n.toLocaleString("en-US", { maximumFractionDigits: d });
}
function rr(x) {
  return x == null ? "" : ` <span class="rr">${esc(x)}R</span>`;
}
function b64(s) {
  const bin = atob(s);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}
async function deriveKey(pw, saltB64, iter) {
  const id = iter + ":" + saltB64 + ":" + pw;
  if (keyCache.has(id)) return keyCache.get(id);
  const mat = await crypto.subtle.importKey("raw", new TextEncoder().encode(pw), "PBKDF2", false, ["deriveKey"]);
  const key = await crypto.subtle.deriveKey(
    { name: "PBKDF2", salt: b64(saltB64), iterations: iter, hash: "SHA-256" },
    mat,
    { name: "AES-GCM", length: 256 },
    false,
    ["decrypt"]
  );
  keyCache.set(id, key);
  return key;
}
async function decrypt(payload, pw) {
  const key = await deriveKey(pw, payload.salt, payload.iter);
  const plain = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64(payload.iv) }, key, b64(payload.ct));
  return JSON.parse(new TextDecoder().decode(plain));
}
async function getJson(url) {
  const res = await fetch(url, { cache: "no-store", referrerPolicy: "no-referrer" });
  if (!res.ok) {
    const err = new Error("HTTP " + res.status);
    err.status = res.status;
    throw err;
  }
  return res.json();
}

function showGate(msg, pending) {
  $("app").hidden = true;
  $("modal").hidden = true;
  $("gate").hidden = false;
  const el = $("gate-msg");
  el.textContent = msg || "";
  el.classList.toggle("pending", !!pending);
}
function showApp() {
  $("gate").hidden = true;
  $("app").hidden = false;
}

async function refreshMeta() {
  try { meta = await getJson("data/meta.json?ts=" + Date.now()); }
  catch (e) { meta = meta || { tfs: {} }; }
}

function paintPills() {
  document.querySelectorAll(".pill").forEach((b) => b.classList.toggle("on", b.dataset.tf === tf));
}
function paintFilters() {
  document.querySelectorAll("[data-filter]").forEach((b) => b.classList.toggle("on", b.dataset.filter === filter));
  document.querySelectorAll("[data-side]").forEach((b) => b.classList.toggle("on", b.dataset.side === side));
  $("min-rank").value = localStorage.getItem("shrieken.rank") || "7";
}
function paintTimes() {
  const info = (meta.tfs || {})[tf] || {};
  const payload = cache[tf];
  const sources = payload && payload.sources && payload.sources.length ? " · via " + payload.sources.join(" + ") : "";
  const last = info.last_scan_ist || "";
  let next = info.next_scan_ist || "";
  if (info.next_scan_ts && info.next_scan_ts * 1000 <= Date.now()) next = "due now";
  $("when").textContent = last
    ? `${tf} · last ${last}${next ? " · next " + next : ""}${sources}`
    : `${tf} · no scan yet`;
}

function renderEmpty(text) {
  $("grid").innerHTML = "";
  $("empty").hidden = false;
  $("empty").textContent = text;
}
function render() {
  const minRank = +$("min-rank").value;
  view = hits.filter((h) => {
    if ((h.rank || 0) < minRank) return false;
    if (side !== "ALL" && h.side !== side) return false;
    if (filter === "prio") return h.state === "RETEST_CONFIRMED_VOLUME";
    if (filter === "zone") return !!h.in_zone;
    if (filter === "fresh") return h.state === "BREAKOUT_FRESH";
    return true;
  });
  view.sort((a, b) => (b.rank - a.rank) || ((PRIO[a.state] ?? 9) - (PRIO[b.state] ?? 9)));
  $("count").textContent = hits.length ? `${view.length} of ${hits.length}` : "";
  if (!view.length) {
    renderEmpty(hits.length ? "No setups match these filters." : "No setups on this timeframe.");
    paintTimes();
    return;
  }
  $("empty").hidden = true;
  $("grid").innerHTML = view.map((h, i) => cardHtml(h, i)).join("");
  $("grid").querySelectorAll(".card").forEach((el) => {
    const open = () => openChart(view[+el.dataset.i]);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(); }
    });
  });
  paintTimes();
}
function cardHtml(h, i) {
  const z = (h.pullback_zone || []).slice().sort((a, b) => a - b);
  const rk = h.rank >= 8 ? "high" : h.rank >= 6 ? "mid" : "";
  const tps = (h.tps || []).slice(0, 3).map((t, k) =>
    `<div class="row"><span>TP${k + 1}</span><span class="tp">${fmt(t)}${rr(h.rr_pullback && h.rr_pullback[k])}</span></div>`
  ).join("");
  const warns = (h.warnings || []).slice(0, 3);
  const zoneTag = h.in_zone ? `<span class="tag">in zone</span>` : "";
  return `<article class="card ${esc(h.side)}" data-i="${i}" role="button" tabindex="0">
    <div class="card-h"><span class="sym">${esc(String(h.symbol || "").replace(/USDT$/, ""))}</span><span class="side ${esc(h.side)}">${esc(h.side)}</span><span class="rank-badge ${rk}">${esc(h.rank)}/10</span></div>
    <div class="state"><b>${esc(STATE_NAME[h.state] || h.state)}</b> · vol ${esc(h.vol_ratio)}× · RSI ${esc(h.rsi14)}${zoneTag}</div>
    <div class="rows">
      <div class="row"><span>Price</span><span>${fmt(h.forming && h.forming.price)}</span></div>
      <div class="row"><span>Zone</span><span>${fmt(z[0])} – ${fmt(z[1])}</span></div>
      <div class="row"><span>SL</span><span class="sl">${fmt(h.sl)}</span></div>
      ${tps}
      <div class="row"><span>Breakout</span><span>${fmt(h.breakout_entry)} <span class="sl">SL ${fmt(h.breakout_sl)}</span></span></div>
    </div>
    ${warns.length ? `<p class="warn">${esc(warns.join(" · "))}</p>` : ""}
  </article>`;
}

async function loadTf(which, opts = {}) {
  tf = which;
  localStorage.setItem("shrieken.tf", tf);
  paintPills();
  $("when").textContent = `Loading ${which}…`;
  await refreshMeta();
  if (!meta.tfs || !meta.tfs[which]) {
    hits = [];
    delete cache[which];
    $("count").textContent = "";
    paintTimes();
    renderEmpty(`No scan for ${which} yet.`);
    return;
  }
  let enc;
  try {
    enc = await getJson(`data/${encodeURIComponent(which)}.json?ts=` + Date.now());
  } catch (e) {
    if (e.status === 404) {
      hits = [];
      delete cache[which];
      $("count").textContent = "";
      paintTimes();
      renderEmpty(`No scan for ${which} yet.`);
      return;
    }
    renderEmpty("Couldn't load this timeframe. Retrying shortly.");
    return;
  }
  let payload;
  try {
    payload = await decrypt(enc, password);
  } catch (e) {
    hits = [];
    renderEmpty(`${which} was saved with a different password. It refreshes on the next scan.`);
    return;
  }
  if (!opts.force && cache[which] && cache[which].generated_ts === payload.generated_ts) {
    paintTimes();
    return;
  }
  cache[which] = payload;
  hits = payload.hits || [];
  render();
}

async function unlock(pw, remember) {
  $("unlock").disabled = true;
  $("gate-msg").textContent = "Checking password…";
  $("gate-msg").classList.remove("pending");
  try {
    let payload;
    try {
      payload = await getJson("data/verify.json?ts=" + Date.now());
    } catch (e) {
      if (e.status === 404) {
        showGate("No scan has been published yet. Add the SITE_PASSWORD secret, then run the Scan signals workflow. This page unlocks with that same password.", true);
        return false;
      }
      throw e;
    }
    const obj = await decrypt(payload, pw);
    if (!obj || obj.ok !== true) throw new Error("bad");
    password = pw;
    localStorage.removeItem(PW_KEY);
    sessionStorage.removeItem(PW_KEY);
    (remember ? localStorage : sessionStorage).setItem(PW_KEY, pw);
    showApp();
    await loadTf(tf, { force: true });
    return true;
  } catch (e) {
    showGate("Wrong password.");
    return false;
  } finally {
    $("unlock").disabled = false;
  }
}
function lock() {
  password = "";
  keyCache.clear();
  hits = [];
  localStorage.removeItem(PW_KEY);
  sessionStorage.removeItem(PW_KEY);
  closeChart();
  showGate("");
  $("password").value = "";
  $("password").focus();
}

function norm(r) { return [+r[0], +r[1], +r[2], +r[3], +r[4], +r[5]]; }
function resample(rows, span, off) {
  const out = new Map();
  for (const r of rows) {
    const k = r[0] - ((r[0] - off) % span);
    const b = out.get(k);
    if (!b) out.set(k, [k, r[1], r[2], r[3], r[4], r[5]]);
    else {
      b[2] = Math.max(b[2], r[2]);
      b[3] = Math.min(b[3], r[3]);
      b[4] = r[4];
      b[5] += r[5];
    }
  }
  let res = [...out.keys()].sort((a, b) => a - b).map((k) => out.get(k));
  if (res.length && rows.length && rows[0][0] !== res[0][0]) res = res.slice(1);
  return res;
}
async function mexcGet(path) {
  const direct = MEXC + path;
  try {
    return await getJson(direct);
  } catch (e) {
    if (e.status) throw e;
    return await getJson("https://proxy.cors.dev/" + direct);
  }
}
async function mexcKlines(sym, tfName, limit) {
  const spec = RESAMPLE[tfName];
  const iv = spec ? MEXC_IV[spec.base] : MEXC_IV[tfName];
  const ask = spec ? (limit > 20 ? 500 : Math.min(500, limit * 3 + 3)) : Math.min(500, limit);
  const raw = await mexcGet(`/klines?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(iv)}&limit=${ask}`);
  if (!Array.isArray(raw)) throw new Error("bad mexc payload");
  const rows = raw.map(norm);
  return spec ? resample(rows, spec.span, spec.off).slice(-limit) : rows;
}
async function binanceKlines(sym, tfName, limit) {
  const raw = await getJson(`${BINANCE}/klines?symbol=${encodeURIComponent(sym)}&interval=${encodeURIComponent(tfName)}&limit=${limit}`);
  if (!Array.isArray(raw)) throw new Error("bad binance payload");
  return raw.map(norm);
}
// A missing Binance symbol returns HTTP 400 without CORS headers, which the
// browser reports as a network failure. Confirm with BTC before treating that
// as a block of the whole API.
async function binanceAlive() {
  if (binanceBlocked) return false;
  if (binanceProbe !== null) return binanceProbe;
  try {
    await getJson(`${BINANCE}/klines?symbol=BTCUSDT&interval=1h&limit=1`);
    binanceProbe = true;
    return true;
  } catch (e) {
    binanceProbe = false;
    binanceBlocked = true;
    return false;
  }
}
function sourceOrder(prefer) {
  if (binanceBlocked) return ["mexc"];
  if (prefer === "mexc") return ["mexc", "binance"];
  return ["binance", "mexc"];
}
async function loadCandles(sym, tfName, prefer, limit) {
  let last;
  for (const src of sourceOrder(prefer)) {
    try {
      if (src === "binance") return { source: "binance", candles: await binanceKlines(sym, tfName, limit) };
      return { source: "mexc", candles: await mexcKlines(sym, tfName, limit) };
    } catch (e) {
      last = e;
      if (src === "binance" && (!e.status || [403, 451, 418, 429].includes(e.status))) {
        if (sym === "BTCUSDT") {
          binanceBlocked = true;
          binanceProbe = false;
        } else if (!(await binanceAlive())) {
          binanceBlocked = true;
        }
      }
    }
  }
  throw last || new Error("no candles");
}
function preferFor(payload) {
  const src = (payload && payload.sources) || [];
  if (src.length && !src.includes("binance")) return "mexc";
  return "binance";
}
function ema(candles, n) {
  const k = 2 / (n + 1);
  let e = 0;
  const out = [];
  candles.forEach((c, i) => {
    e = i ? c[4] * k + e * (1 - k) : c[4];
    if (i >= n - 1) out.push({ time: c[0] / 1000 + IST, value: e });
  });
  return out;
}
function prec(p) {
  const a = Math.abs(p);
  return a >= 1000 ? 2 : a >= 1 ? 4 : a >= 0.01 ? 5 : 8;
}
function hitFor(sym, tfName, sideName) {
  const list = ((cache[tfName] && cache[tfName].hits) || []).filter((h) => h.symbol === sym);
  if (!list.length) return null;
  if (sideName) {
    const same = list.find((h) => h.side === sideName);
    if (same) return same;
  }
  return list.slice().sort((a, b) => b.rank - a.rank)[0];
}
function detailsHtml(h) {
  if (!h) return `<p class="muted">No signal on this timeframe. Chart only.</p>`;
  const z = (h.pullback_zone || []).slice().sort((a, b) => a - b);
  const tps = (h.tps || []).map((t, k) => `<div class="row"><span>TP${k + 1}</span><span class="tp">${fmt(t)}${rr(h.rr_pullback && h.rr_pullback[k])}</span></div>`).join("");
  const bo = (h.rr_breakout || []).map((x) => (x == null ? "–" : x + "R")).join(" / ");
  const warns = (h.warnings || []).slice(0, 3);
  return `<div class="card-h"><span class="side ${esc(h.side)}">${esc(h.side)}</span><span class="rank-badge">${esc(h.rank)}/10</span></div>
    <div class="state"><b>${esc(STATE_NAME[h.state] || h.state)}</b></div>
    <h3>Pullback</h3>
    <div class="rows"><div class="row"><span>Zone</span><span>${fmt(z[0])} – ${fmt(z[1])}</span></div>
    <div class="row"><span>Entry</span><span>${fmt(h.pullback_entry)}</span></div>
    <div class="row"><span>SL</span><span class="sl">${fmt(h.sl)}</span></div></div>
    <h3>Targets</h3><div class="rows">${tps}</div>
    <h3>Breakout</h3>
    <div class="rows"><div class="row"><span>Entry</span><span>${fmt(h.breakout_entry)}</span></div>
    <div class="row"><span>SL</span><span class="sl">${fmt(h.breakout_sl)}</span></div>
    <div class="row"><span>Level</span><span>${fmt(h.breakout_level)}</span></div>
    <div class="row"><span>R:R</span><span>${esc(bo)}</span></div></div>
    <h3>Context</h3>
    <div class="rows"><div class="row"><span>Line</span><span>${fmt(h.trendline_now)}</span></div>
    <div class="row"><span>Touches</span><span>${esc(h.touches)}</span></div>
    <div class="row"><span>Break</span><span>${esc(h.bars_since_break)} bars</span></div>
    <div class="row"><span>Volume</span><span>${esc(h.vol_ratio)}×</span></div>
    <div class="row"><span>RSI</span><span>${esc(h.rsi14)}</span></div>
    <div class="row"><span>EMA50</span><span>${fmt(h.ema50)}</span></div>
    <div class="row"><span>EMA200</span><span>${fmt(h.ema200)}</span></div></div>
    ${warns.length ? `<p class="warn">${esc(warns.join(" · "))}</p>` : ""}
    <p class="fine">Rank is setup quality, not a prediction. Not financial advice.</p>`;
}
function drawOverlays(h, candles) {
  if (!h) return;
  const i1 = candles.findIndex((c) => c[0] === h.anchor1_ms);
  const i2 = candles.findIndex((c) => c[0] === h.anchor2_ms);
  if (i1 >= 0 && i2 > i1 && h.anchor1_price && h.anchor2_price) {
    const a = Math.log(h.anchor1_price);
    const b = Math.log(h.anchor2_price);
    const m = (b - a) / (i2 - i1);
    const line = chart.addLineSeries({ color: "#e6b15c", lineWidth: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    line.setData(candles.slice(i1).map((c, k) => ({ time: c[0] / 1000 + IST, value: Math.exp(a + m * k) })));
  }
  const mark = (v, color, title, style) => {
    if (v == null || !Number.isFinite(+v)) return;
    candleSeries.createPriceLine({ price: +v, color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title });
  };
  const z = (h.pullback_zone || []).slice().sort((a, b) => a - b);
  mark(z[0], "#9aa8ff", "zone", 2);
  mark(z[1], "#9aa8ff", "zone", 2);
  mark(h.breakout_level, "#e6b15c", "BO", 2);
  mark(h.sl, "#ff7384", "SL", 0);
  (h.tps || []).slice(0, 3).forEach((t, k) => mark(t, "#3dcca0", "TP" + (k + 1), 0));
}
async function drawChart(sym, tfName, sideName) {
  const req = ++chartReq;
  clearInterval(pollTimer);
  if (chart) { chart.remove(); chart = null; }
  chartSym = sym;
  chartSide = sideName || "";
  $("chart").innerHTML = "";
  $("chart-title").textContent = sym.replace(/USDT$/, "") + " / USDT";
  $("chart-tf").value = tfName;
  if (!cache[tfName] && password) {
    try {
      const enc = await getJson(`data/${encodeURIComponent(tfName)}.json?ts=` + Date.now());
      cache[tfName] = await decrypt(enc, password);
    } catch (e) { /* chart still works without an overlay */ }
  }
  if (req !== chartReq) return;
  const h = hitFor(sym, tfName, sideName);
  $("details").innerHTML = detailsHtml(h);
  const prefer = preferFor(cache[tfName] || cache[tf]);
  let data;
  try {
    data = await loadCandles(sym, tfName, prefer, 500);
  } catch (e) {
    $("chart-note").textContent = "Couldn't load candles from Binance or MEXC.";
    return;
  }
  if (req !== chartReq) return;
  const candles = data.candles || [];
  if (!candles.length) {
    $("chart-note").textContent = "No candles returned.";
    return;
  }
  const tvEx = data.source === "mexc" ? "MEXC" : "BINANCE";
  $("tv").href = `https://www.tradingview.com/chart/?symbol=${tvEx}:${encodeURIComponent(sym)}`;
  chart = LightweightCharts.createChart($("chart"), {
    autoSize: true,
    layout: { background: { color: "#0d1219" }, textColor: "#aeb8c8" },
    grid: { vertLines: { color: "#1a2230" }, horzLines: { color: "#1a2230" } },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: "#2a3546" },
    rightPriceScale: { borderColor: "#2a3546" },
    crosshair: { mode: 0 },
  });
  const p = prec(candles[candles.length - 1][4]);
  candleSeries = chart.addCandlestickSeries({
    upColor: "#3dcca0", downColor: "#ff7384", wickUpColor: "#3dcca0", wickDownColor: "#ff7384", borderVisible: false,
    priceFormat: { type: "price", precision: p, minMove: Math.pow(10, -p) },
  });
  volumeSeries = chart.addHistogramSeries({ priceScaleId: "vol", priceFormat: { type: "volume" } });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  const up = (c) => c[4] >= c[1];
  candleSeries.setData(candles.map((c) => ({ time: c[0] / 1000 + IST, open: c[1], high: c[2], low: c[3], close: c[4] })));
  volumeSeries.setData(candles.map((c) => ({ time: c[0] / 1000 + IST, value: c[5], color: up(c) ? "rgba(61,204,160,.28)" : "rgba(255,115,132,.28)" })));
  if (candles.length > 50) {
    chart.addLineSeries({ color: "rgba(154,168,255,.7)", lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false })
      .setData(ema(candles, 50));
  }
  drawOverlays(h, candles);
  chart.timeScale().fitContent();
  $("chart-note").textContent = `${tfName} · ${data.source} · times in IST · EMA50`;
  const pollMs = data.source === "mexc" ? 15000 : 5000;
  pollTimer = setInterval(async () => {
    if (req !== chartReq) return;
    try {
      const live = await loadCandles(sym, tfName, data.source, 2);
      for (const c of live.candles) {
        candleSeries.update({ time: c[0] / 1000 + IST, open: c[1], high: c[2], low: c[3], close: c[4] });
        volumeSeries.update({ time: c[0] / 1000 + IST, value: c[5], color: up(c) ? "rgba(61,204,160,.28)" : "rgba(255,115,132,.28)" });
      }
    } catch (e) { /* keep the last good candle */ }
  }, pollMs);
}
function openChart(h) {
  if (!h) return;
  $("modal").hidden = false;
  document.body.style.overflow = "hidden";
  drawChart(h.symbol, h.tf || tf, h.side);
}
function closeChart() {
  chartReq++;
  clearInterval(pollTimer);
  if (chart) { chart.remove(); chart = null; }
  $("modal").hidden = true;
  document.body.style.overflow = "";
}

$("chart-tf").innerHTML = TFS.map((t) => `<option>${t}</option>`).join("");
$("gate-form").addEventListener("submit", (e) => {
  e.preventDefault();
  unlock($("password").value, $("remember").checked);
});
$("lock").addEventListener("click", lock);
document.querySelectorAll(".pill").forEach((b) => b.addEventListener("click", () => loadTf(b.dataset.tf, { force: true })));
document.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
  filter = b.dataset.filter;
  localStorage.setItem("shrieken.filter", filter);
  paintFilters();
  render();
}));
document.querySelectorAll("[data-side]").forEach((b) => b.addEventListener("click", () => {
  side = b.dataset.side;
  localStorage.setItem("shrieken.side", side);
  paintFilters();
  render();
}));
$("min-rank").addEventListener("change", () => {
  localStorage.setItem("shrieken.rank", $("min-rank").value);
  render();
});
$("chart-tf").addEventListener("change", () => drawChart(chartSym, $("chart-tf").value, chartSide));
$("close-chart").addEventListener("click", closeChart);
$("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeChart(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("modal").hidden) closeChart(); });

paintFilters();
paintPills();
(async function boot() {
  await refreshMeta();
  const saved = localStorage.getItem(PW_KEY) || sessionStorage.getItem(PW_KEY);
  if (saved) {
    const ok = await unlock(saved, !!localStorage.getItem(PW_KEY));
    if (ok) return;
  }
  try {
    await getJson("data/verify.json?ts=" + Date.now());
  } catch (e) {
    if (e.status === 404) {
      showGate("No scan has been published yet. Add the SITE_PASSWORD secret, then run the Scan signals workflow. This page unlocks with that same password.", true);
    }
  }
  $("password").focus();
})();
setInterval(() => { if (password) loadTf(tf); }, 60000);
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && password) loadTf(tf);
});
