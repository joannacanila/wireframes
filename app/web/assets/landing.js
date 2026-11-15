// Landing page. Every number shown is computed: the hero from the pipeline's own output on the
// synthetic sample, the method panels by spectral.js (tested equal to the Python pipeline),
// the evaluation table from /api/evaluation/metrics. Nothing is typed in by hand.

import { OverlayViewer, CLASS_LABEL, wireColor } from "./viewer.js";
import * as M from "./spectral.js";

const $ = (s, r = document) => r.querySelector(s);
const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const LV = { low: "--risk-low", medium: "--risk-med", high: "--risk-high" };
const getJSON = (u) => fetch(u).then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${u}: ${r.status}`))));

// ------------------------------------------------------------------ chrome
const nav = $("#nav");
const onScroll = () => nav.toggleAttribute("data-scrolled", scrollY > 8);
addEventListener("scroll", onScroll, { passive: true }); onScroll();
requestAnimationFrame(() => $("[data-enter]").classList.add("in"));

function countUp(el, to, { digits = 2, suffix = "" } = {}) {
  const t0 = performance.now(), dur = reduce ? 0 : 900;
  const step = (now) => {
    const t = dur ? Math.min(1, (now - t0) / dur) : 1;
    const e = 1 - Math.pow(1 - t, 3);
    el.textContent = (to * e).toFixed(digits) + suffix;
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

// ------------------------------------------------------------------ data
const [sample, cfg] = await Promise.all([
  getJSON("/assets/samples/synthetic-pole.result.json"),
  getJSON("/api/config").catch(() => null),
]);

// ------------------------------------------------------------------ hero: console viewer on real output
{
  const viewer = new OverlayViewer($("#hero-canvas"), { onSelect: (i) => mark(i) });
  viewer.layers.skeleton = false;
  await viewer.load(sample.image.url, sample.wires, sample.vegetation || []).catch(() => {});
  const list = $("#hero-wires");
  list.innerHTML = sample.wires.map((w) => `<li data-i="${w.index}" style="--wc:${wireColor(w)}" aria-selected="false">
      <span class="n">${w.index}</span><span class="t">${CLASS_LABEL[w.class]}</span><span class="c">${w.crs == null ? "—" : w.crs.toFixed(1)}</span></li>`).join("");
  const mark = (i) => list.querySelectorAll("li").forEach((li) => li.setAttribute("aria-selected", String(Number(li.dataset.i) === i)));
  list.addEventListener("click", (e) => {
    const li = e.target.closest("li"); if (!li) return;
    const i = Number(li.dataset.i); const next = viewer.selected === i ? null : i;
    viewer.select(next); mark(next);
  });
  // open on the worst wire: one callout tells the story; click another to compare
  const worst = sample.wires.filter((w) => w.crs != null).sort((x, y) => y.crs - x.crs)[0];
  if (worst) { viewer.selected = worst.index; viewer.draw(); mark(worst.index); }
  const lvl = $("#hero-level");
  lvl.className = `chip ${sample.summary.level}`;
  lvl.textContent = `max CRS ${sample.summary.max_crs.toFixed(1)} · ${sample.summary.level}`;

  countUp($('[data-count="vni"]'), sample.vni.value, { digits: 2 });
  $("[data-band]").textContent = sample.vni.band;
  countUp($('[data-count="sag"]'), sample.summary.max_sag_ratio * 100, { digits: 2, suffix: "%" });
  countUp($('[data-count="mspi"]'), sample.mspi.value, { digits: 3 });

  $('[data-sample="crs"]').textContent = `Max CRS ${sample.summary.max_crs.toFixed(1)} · ${sample.summary.level}`;
  $('[data-sample="crs"]').style.color = css(LV[sample.summary.level]);
}

// ------------------------------------------------------------------ texture path: live 2D FFT
const vcfg = cfg?.vni ?? { radius_factor: 0.15, remove_mean: false, q01: 0.004, q99: 0.095,
  bands: [{ label: "Low", max: 0.25 }, { label: "Moderate", max: 0.5 }, { label: "High", max: 0.75 }, { label: "Severe", max: 1 }] };
const N = 256;

function rng(seed) { let s = seed >>> 0; return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 4294967296); }

function drawScene(ctx, clutter) {
  const r = rng(7 + clutter * 31);
  const g = ctx.createLinearGradient(0, 0, 0, N);
  g.addColorStop(0, "#b9cde3"); g.addColorStop(1, "#e9ecef");
  ctx.fillStyle = g; ctx.fillRect(0, 0, N, N);
  // buildings and windows: the high-frequency content that clutter adds
  for (let i = 0; i < clutter * 2; i++) {
    const w = 26 + r() * 50, h = 50 + r() * 110, x = r() * (N - 20), s = 120 + r() * 60;
    ctx.fillStyle = `rgb(${s},${s - 6},${s - 14})`; ctx.fillRect(x, N - h, w, h);
    ctx.fillStyle = "#465464";
    for (let y = N - h + 8; y < N - 8; y += 11) for (let xx = x + 4; xx < x + w - 6; xx += 9) ctx.fillRect(xx, y, 4, 5);
  }
  ctx.fillStyle = "#5c5852"; ctx.fillRect(22, 30, 6, N); ctx.fillRect(N - 28, 30, 6, N);
  ctx.strokeStyle = "#1c1c1e"; ctx.lineCap = "round";
  const wires = 2 + Math.round(clutter * 0.9);
  for (let k = 0; k < wires; k++) {
    const y0 = 38 + k * (90 / wires) + r() * 4, sag = 6 + r() * 20 + clutter;
    ctx.lineWidth = 1 + r() * 1.6;
    ctx.beginPath(); ctx.moveTo(25, y0); ctx.quadraticCurveTo(N / 2, y0 + sag * 2, N - 25, y0 + r() * 6 - 3); ctx.stroke();
  }
}

function heat(t) { // dark -> gold -> white, the brand ramp
  const stops = [[12, 13, 15], [74, 52, 18], [212, 169, 74], [255, 246, 220]];
  const x = Math.min(0.9999, Math.max(0, t)) * (stops.length - 1), i = Math.floor(x), f = x - i;
  return stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
}

function runFFT() {
  const clutter = Number($("#clutter").value);
  $("#clutter-o").textContent = String(clutter);
  const sc = $("#scene").getContext("2d", { willReadFrequently: true });
  drawScene(sc, clutter);
  const px = sc.getImageData(0, 0, N, N).data;
  const gray = new Float64Array(N * N);
  for (let i = 0; i < N * N; i++) gray[i] = 0.299 * px[i * 4] + 0.587 * px[i * 4 + 1] + 0.114 * px[i * 4 + 2];
  const p = M.powerSpectrum(gray, N, N, vcfg.remove_mean);
  const raw = M.vniRawFromSpectrum(p, N, N, vcfg.radius_factor);
  const { value, band } = M.normalizeVni(raw, vcfg.q01, vcfg.q99, vcfg.bands);
  // log spectrum heat map
  let lo = Infinity, hi = -Infinity;
  const lp = new Float64Array(p.length);
  for (let i = 0; i < p.length; i++) { lp[i] = Math.log1p(p[i]); lo = Math.min(lo, lp[i]); hi = Math.max(hi, lp[i]); }
  const sp = $("#spectrum").getContext("2d");
  const img = sp.createImageData(N, N);
  for (let i = 0; i < p.length; i++) {
    const [R, G, B] = heat(Math.pow((lp[i] - lo) / (hi - lo || 1), 1.6));
    img.data.set([R, G, B, 255], i * 4);
  }
  sp.putImageData(img, 0, 0);
  sp.strokeStyle = "rgba(255,255,255,0.85)"; sp.setLineDash([4, 3]); sp.lineWidth = 1;
  sp.beginPath(); sp.arc(N / 2, N / 2, (vcfg.radius_factor * N) / 2, 0, Math.PI * 2); sp.stroke(); sp.setLineDash([]);
  $("#vni-raw").textContent = raw.toFixed(4);
  $("#vni-val").textContent = value.toFixed(2);
  $("#vni-band").textContent = band;
}
$("#clutter").addEventListener("input", runFFT);
runFFT();

// ------------------------------------------------------------------ geometry path: live catenary
const dlim0 = cfg?.crs?.delta_lim0?.["power-distribution-lines"] ?? 0.04;
const cuts = cfg?.crs?.levels ?? { low_below: 3, high_above: 6 };
const SVG = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}) {
  const n = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
}

function runCatenary() {
  const sagPct = Number($("#sag").value), tilt = Number($("#tilt").value);
  $("#sag-o").textContent = `${sagPct.toFixed(1)}%`; $("#tilt-o").textContent = `${tilt} px`;
  const xa = 60, xb = 460, span = xb - xa;
  // a for a level span with the chosen midspan sag; the support offset then shifts b so the
  // curve passes through both supports (y-up frame), as a real span between unequal poles.
  const a = M.aForSag(span, (sagPct / 100) * span);
  const dy = -tilt; // image y down -> y up
  // solve a cosh((xb-b)/a) - a cosh((xa-b)/a) = dy for b (bisection)
  let lo = xa - 4 * span, hi = xb + 4 * span;
  const f = (b) => a * Math.cosh((xb - b) / a) - a * Math.cosh((xa - b) / a) - dy;
  for (let i = 0; i < 200; i++) { const mid = (lo + hi) / 2; if (f(mid) > 0) lo = mid; else hi = mid; }
  const b = (lo + hi) / 2;
  const top = 70, c = -a * Math.cosh((xa - b) / a); // left support at y_up = 0
  const toY = (yu) => top - yu;
  const s = M.chordSag(a, b, c, xa, xb);
  const ratio = s.delta / s.L, crs = M.crs(ratio, dlim0), lvl = M.level(crs, cuts.low_below, cuts.high_above);
  const color = css(LV[lvl]);
  const svg = $("#span"); svg.replaceChildren();
  const pts = Array.from({ length: 97 }, (_, i) => { const x = xa + (span * i) / 96; return `${x.toFixed(1)},${toY(M.catenary(x, a, b, c)).toFixed(1)}`; });
  const ya = toY(s.ya), yb = toY(s.yb);
  svg.append(
    el("line", { class: "pole", x1: xa, y1: ya - 14, x2: xa, y2: 250 }), el("line", { class: "pole", x1: xb, y1: yb - 14, x2: xb, y2: 250 }),
    el("line", { class: "arm", x1: xa - 22, y1: ya, x2: xa + 22, y2: ya }), el("line", { class: "arm", x1: xb - 22, y1: yb, x2: xb + 22, y2: yb }),
    el("line", { class: "chord", x1: xa, y1: ya, x2: xb, y2: yb }),
    el("polyline", { class: "curve", points: pts.join(" "), stroke: color }),
  );
  if (s.delta > 0) {
    const fx = xa + s.t * (xb - xa), fy = ya + s.t * (yb - ya), sx = s.xs, sy = toY(s.ys);
    svg.append(el("line", { class: "perp", x1: sx, y1: sy, x2: fx, y2: fy }), el("circle", { cx: sx, cy: sy, r: 4.5, fill: color, stroke: "#fff", "stroke-width": 1.5 }));
    const t = el("text", { x: sx + 10, y: sy + 22 }); t.textContent = `δ ${s.delta.toFixed(1)} px`; svg.append(t);
  }
  const L = el("text", { x: (xa + xb) / 2 - 30, y: Math.min(ya, yb) - 12 }); L.textContent = `L ${s.L.toFixed(0)} px`; svg.append(L);
  $("#c-ratio").textContent = `${(ratio * 100).toFixed(2)}%`;
  $("#c-lim").textContent = `${(dlim0 * 100).toFixed(1)}%`;
  const cEl = $("#c-crs"); cEl.textContent = `${crs.toFixed(1)} ${lvl}`; cEl.dataset.level = lvl;
}
$("#sag").addEventListener("input", runCatenary); $("#tilt").addEventListener("input", runCatenary);
runCatenary();

// ------------------------------------------------------------------ Equation 9: allowable sag vs temperature
function changeOfState(h1, w1, t1, w2, t2, L, E, A, alpha) {
  const ea = E * A, k = h1 - (ea * w1 * w1 * L * L) / (24 * h1 * h1) - ea * alpha * (t2 - t1), rhs = (ea * w2 * w2 * L * L) / 24;
  let lo = Math.max(k, 0), hi = Math.max(lo, 1);
  while (hi * hi * (hi - k) < rhs) hi *= 2;
  for (let i = 0; i < 200; i++) { const m = (lo + hi) / 2; if (m * m * (m - k) < rhs) lo = m; else hi = m; }
  return (lo + hi) / 2;
}
{
  const eng = cfg?.engineering, cond = eng?.conductors?.["power-distribution-lines"];
  const svg = $("#cos");
  if (cond) {
    const L = 40, tr = eng.reference_temperature_c;
    const ratio = (T) => { const h = changeOfState(cond.reference_tension_n, cond.weight_n_per_m, tr, cond.weight_n_per_m, T, L, cond.elastic_modulus_pa, cond.area_m2, cond.thermal_expansion_per_c);
      const w = cond.weight_n_per_m; return (h / w) * (Math.cosh((w * L) / (2 * h)) - 1) / L; };
    const T = Array.from({ length: 41 }, (_, i) => 15 + i * 0.75);
    const lim = T.map((t) => dlim0 * ratio(t) / ratio(tr));
    const y0 = Math.min(...lim), y1 = Math.max(...lim);
    const X = (t) => 30 + ((t - 15) / 30) * 260, Y = (v) => 100 - ((v - y0) / (y1 - y0 || 1)) * 80;
    svg.append(el("path", { class: "g", d: "M30 100H290M30 20V100" }),
      el("path", { class: "l", d: "M" + T.map((t, i) => `${X(t).toFixed(1)} ${Y(lim[i]).toFixed(1)}`).join("L") }));
    for (const [x, y, s] of [[30, 114, "15 °C"], [262, 114, "45 °C"], [34, 16, `δlim ${(y1 * 100).toFixed(2)}%`], [34, 96, `${(y0 * 100).toFixed(2)}%`]]) {
      const t = el("text", { x, y }); t.textContent = s; svg.append(t);
    }
  }
}

// ------------------------------------------------------------------ mapping: real GeoJSON from the API
const hl = (json) => esc(json)
  .replace(/&quot;([^&]+?)&quot;(?=:)/g, '<span class="k">"$1"</span>')
  .replace(/: &quot;(.*?)&quot;/g, ': <span class="s">"$1"</span>')
  .replace(/: (-?\d+\.?\d*)/g, ': <span class="n">$1</span>');
function esc(s) { return s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
{
  // The live multi-pole map is admin-only (pole locations are restricted, Section 3.14.4), so the
  // public page shows the GeoJSON schema with the synthetic sample's real values.
  // RFC 7946 allows a null geometry for a feature that has no location yet.
  const feature = { type: "Feature", id: "SYN-SAMPLE", geometry: null, properties: {
    pole_code: "SYN-SAMPLE", rank: 1, mspi: +sample.mspi.value.toFixed(4), level: sample.summary.level,
    vni: +sample.vni.value.toFixed(4), vni_band: sample.vni.band, max_crs: sample.summary.max_crs,
    n_wires: sample.summary.wire_count } };
  $("#geo-json").innerHTML = hl(JSON.stringify(feature, null, 2));
  const p = feature.properties;
  const g = $(".geo-map .pins");
  const spots = [[200, 60], [120, 150], [270, 40], [80, 165], [300, 95], [160, 105], [60, 60], [240, 150], [330, 30]];
  const list = [p];
  list.slice(0, spots.length).forEach((q, i) => {
    const [x, y] = spots[i]; const col = css(LV[q.level] || "--risk-none");
    const grp = el("g", { class: "pin", transform: `translate(${x} ${y})` });
    if (i === 0) grp.append(el("circle", { class: "halo", r: 10, stroke: col }));
    grp.append(el("circle", { r: 10, fill: col }));
    const t = el("text", {}); t.textContent = q.rank ?? "–"; grp.append(t);
    g.append(grp);
  });
}

// ------------------------------------------------------------------ evaluation: Table 3.10, live
{
  const names = { map50: "Mask mAP@50", precision_per_class: "Precision, per class", recall_per_class: "Recall, per class",
    f1_per_class: "F1-score, per class", h1_pearson_r: "Pearson r · VNI vs clutter (H1)", h3_rmse: "RMSE · CRS vs severity (H3)",
    h4_spearman_rho: "Spearman ρ · MSPI vs reference rank (H4)" };
  const tbody = $("#eval-table tbody");
  try {
    const m = await getJSON("/api/evaluation/metrics");
    const seg = m.sections.segmentation_test, ref = m.sections.reference;
    const val = (k) => {
      if (k === "map50" && seg.status !== "not_run") return seg.map50?.toFixed(3);
      if (ref.status !== "not_run") {
        if (k === "h1_pearson_r") return ref.h1?.pearson_r?.value?.toFixed(3);
        if (k === "h3_rmse") return ref.h3?.rmse?.value?.toFixed(3);
        if (k === "h4_spearman_rho") return ref.h4?.spearman_rho?.value?.toFixed(3);
      }
      return null;
    };
    tbody.innerHTML = Object.entries(m.targets).map(([k, t]) => {
      const v = val(k);
      return `<tr><td>${names[k] || k}</td><td class="num">${t.op === ">=" ? "≥" : "≤"} ${t.value.toFixed(2)}</td><td>${t.applies_to}</td>
        <td class="${v ? "num" : "muted"}">${v ?? "Not yet measured"}</td></tr>`;
    }).join("");
    const geo = m.sections.geometry_synthetic;
    if (geo.status !== "not_run") {
      $("#eval-geo").innerHTML = `<div class="head"><span>Geometric layer · synthetic benchmark, ${geo.wires} rendered wires with known parameters</span><span class="mono muted">${geo.fit_failures} fit failures</span></div>
        <div class="tile"><div class="k">Curve RMSE</div><div class="v">${geo.curve_rmse_px.median.toFixed(2)} px</div><div class="s">median · p95 ${geo.curve_rmse_px.p95.toFixed(2)} px</div></div>
        <div class="tile"><div class="k">Sag-ratio error</div><div class="v">${(geo.relative_sag_ratio_error.median * 100).toFixed(1)}%</div><div class="s">median · p95 ${(geo.relative_sag_ratio_error.p95 * 100).toFixed(1)}%</div></div>
        <div class="tile"><div class="k">Catenary a error</div><div class="v">${(geo.relative_a_error.median * 100).toFixed(1)}%</div><div class="s">median · p95 ${(geo.relative_a_error.p95 * 100).toFixed(1)}%</div></div>`;
    }
  } catch {
    tbody.innerHTML = `<tr><td colspan="4" class="muted">The evaluation service could not be reached.</td></tr>`;
  }
}
