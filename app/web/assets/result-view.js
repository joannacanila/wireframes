/** S18: ONE result view for every route that shows a screening result: the citizen Field Screener, the admin
 *  inspection sheet and the quick-review drawer. It renders what the server decided (r.screening, r.overview,
 *  w.finding) and computes no threshold, count or classification of its own.
 *
 *    A  What we know        wire count, the one finding, problem wires with the standard sag meter, photo tips
 *    B  What to do next     supplied by the host page (citizen actions and providers, or the admin review panel)
 *    C  How did we know?    the evidence viewer: six layers over the same photo, synchronised with the wire list
 */
import { esc } from "./lib.js?v=2.23";
import { OverlayViewer, wireColor, classLabel, classVar } from "./viewer.js?v=2.35";

const DT = { day: "numeric", month: "short", year: "numeric", hour: "numeric", minute: "2-digit" };
const dt = (iso) => (iso ? new Date(iso).toLocaleString(undefined, DT) : null);
const n = (k, one, many = `${one}s`) => `${k} ${k === 1 ? one : many}`;

export const LAYERS = [
  ["photo", "Original"], ["poles", "Pole detection"], ["mask", "Segmentation mask"],
  ["traces", "Drawn wires"], ["problems", "Problem wires"], ["combined", "Combined"],
];
const LAYER_HELP = {
  photo: "The photo exactly as it was analysed.",
  poles: "Utility poles the detector found, outlined. Poles give context only; they never create a wire.",
  mask: "The segmentation model's pixels, coloured by the class the model assigned (see the legend).",
  traces: "The traced centre line and the fitted curve of each wire, with the dip used for the sag estimate.",
  problems: "Only the wires with a supported observation (sag beyond its reference or range, a possible loose end, vegetation contact, or an apparently abandoned cable).",
  combined: "The photo with every overlay, aligned to the same pixels.",
};
const PRESET = {
  photo: {},
  poles: { poles: 1 },
  mask: { masks: 1, inferred: 1, labels: 1, vegetation: 1 },
  traces: { skeleton: 1, catenary: 1, sag: 1, labels: 1 },
  problems: { masks: 1, inferred: 1, catenary: 1, sag: 1, labels: 1 },
  combined: { masks: 1, inferred: 1, catenary: 1, sag: 1, labels: 1, poles: 1, vegetation: 1 },
};
const VLAYERS = ["masks", "inferred", "vegetation", "catenary", "skeleton", "sag", "ends", "labels", "wiremask", "poles"];

/** The standard sag meter (S18 Part 2.2): green low / yellow moderate / red high, drawn from the server's comparison
 *  boundaries; the band shown is the server's ``finding.meter_level``. A wire without a reference gets a neutral bar. */
export function sagMeter(w, scaleMax = 0.1, { compact = false } = {}) {
  const f = w.finding || {}, c = w.comparison || {}, sg = f.sag;
  if (!sg) return compact ? "" : `<p class="sm2-none">${esc(f.span_reason ? `Not measured: ${f.span_reason}.` : "Not measured.")}</p>`;
  const max = Math.max(scaleMax, (c.upper_boundary || 0) * 1.6, w.sag_ratio || 0);
  const at = (q) => Math.max(0, Math.min(100, (q / max) * 100));
  const lo = c.lower_boundary, hi = c.upper_boundary;
  const banded = f.meter_level && lo != null && hi != null;
  const track = banded
    ? `<i class="sm2-z low" style="left:0;width:${at(lo)}%"></i><i class="sm2-z mod" style="left:${at(lo)}%;width:${at(hi) - at(lo)}%"></i><i class="sm2-z high" style="left:${at(hi)}%;right:0"></i>
       <i class="sm2-tick" style="left:${at(lo)}%"><span>${(lo * 100).toFixed(0)}%</span></i><i class="sm2-tick" style="left:${at(hi)}%"><span>${(hi * 100).toFixed(0)}%</span></i>`
    : `<i class="sm2-z none" style="left:0;right:0"></i>`;
  const lvl = f.meter_level || "none";
  return `<figure class="sm2 lv-${esc(lvl)}${compact ? " compact" : ""}" role="img" aria-label="Estimated relative sag ${esc(sg.text)}${f.meter_level_label ? `, ${esc(f.meter_level_label)}` : ", no applicable reference"}">
    <div class="sm2-read"><b class="num">${esc(sg.text)}</b>${f.meter_level_label ? `<span class="sm2-lv">${esc(f.meter_level_label)}</span>` : `<span class="sm2-lv none">No reference</span>`}${sg.partial ? `<small>visible part</small>` : ""}</div>
    <div class="sm2-track">${track}<i class="sm2-dot" style="left:${at(w.sag_ratio)}%"></i></div>
    ${compact ? "" : `<figcaption>${esc(f.reference_short || "No applicable reference for this wire type")}${sg.range_text ? ` · range ${esc(sg.range_text)}` : ""}</figcaption>`}</figure>`;
}

/** S18: wire numbers in server-written text ("wires 3 and 5") become links to that wire in the photo. Only numbers
 *  of wires that exist in this result are linked. ``html`` must already be escaped. */
export function wireRefs(html, wires) {
  const ok = new Set((wires || []).map((w) => w.index));
  return String(html).replace(/\b([Ww]ires?) ((?:\d+)(?:(?:, | and )\d+)*)/g, (m, word, nums) => `${word} ${nums.replace(/\d+/g,
    (d) => (ok.has(Number(d)) ? `<button type="button" class="fx-ref" data-w="${d}" aria-label="Show wire ${d} in the photo">${d}</button>` : d))}`);
}

/** S18: an information button with a small panel (click or tap opens it; hover previews it on a mouse), the same
 *  interaction as the notice chip. ``rows`` = [[term, text], ...]. */
export function infoTip(id, label, rows) {
  return `<span class="ix"><button type="button" class="ix-b" aria-expanded="false" aria-controls="${esc(id)}" aria-label="${esc(label)}"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/></svg></button>
    <span class="ix-p" id="${esc(id)}" role="note"><b>${esc(label.replace(/^About /, ""))}</b><dl>${rows.filter(([, t]) => t).map(([k, t]) => `<div><dt>${esc(k)}</dt><dd>${esc(t)}</dd></div>`).join("")}</dl></span></span>`;
}
if (typeof document !== "undefined" && !window.__wfIx) {
  window.__wfIx = true;
  const closeAll = (except) => document.querySelectorAll(".ix-b[aria-expanded=true]").forEach((b) => { if (b !== except) b.setAttribute("aria-expanded", "false"); });
  document.addEventListener("click", (e) => {
    const b = e.target.closest?.(".ix-b");
    if (b) { const open = b.getAttribute("aria-expanded") !== "true"; closeAll(b); b.setAttribute("aria-expanded", String(open)); return; }
    if (!e.target.closest?.(".ix-p")) closeAll();
  });
  // Escape also hides a panel shown only by hover (WCAG 1.4.13: dismissible without moving the pointer); hover works
  // again once the pointer leaves that button
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    closeAll(); document.querySelectorAll(".ix-b:hover").forEach((b) => { b.dataset.quiet = ""; });
  });
  document.addEventListener("mouseout", (e) => { const b = e.target.closest?.(".ix-b"); if (b && !b.contains(e.relatedTarget)) delete b.dataset.quiet; });
}

/** S18: a value on its scale with the bands or reference marks drawn and labelled (never colour alone). */
function metricMeter(m) {
  const max = m.scale_max || 1, at = (v) => Math.max(0, Math.min(100, (v / max) * 100));
  let track = "", ticks = "", lbl = "";
  if (m.bands?.length) {
    track = m.bands.map((b, i) => `<i class="mm-z s${i}" style="left:${at(b.from)}%;width:${at(b.to) - at(b.from)}%"><span>${esc(b.label)}</span></i>`).join("");
    ticks = m.bands.slice(0, -1).map((b) => `<i class="mm-t" style="left:${at(b.to)}%"><span>${b.to}</span></i>`).join("");
    lbl = m.band || "";
  } else if (m.marks?.length === 2) {
    const [a, b] = m.marks;
    track = `<i class="mm-z lv-low" style="left:0;width:${at(a)}%"></i><i class="mm-z lv-mod" style="left:${at(a)}%;width:${at(b) - at(a)}%"></i><i class="mm-z lv-high" style="left:${at(b)}%;right:0"></i>`;
    ticks = m.marks.map((v, i) => `<i class="mm-t" style="left:${at(v)}%"><span>${v} · ${esc(m.mark_labels?.[i] || "")}</span></i>`).join("");
  }
  const v = m.value;
  return `<figure class="mm" role="img" aria-label="${esc(m.name)} ${v == null ? "not computed" : v}${lbl ? `, ${esc(lbl)}` : ""}, scale 0 to ${max}">
    <div class="mm-read"><b class="num">${v == null ? "—" : v}</b><span>${esc(m.unit)}${lbl ? ` · ${esc(lbl)}` : ""}${m.wire != null ? ` · highest: <button type="button" class="fx-ref" data-w="${m.wire}">wire ${m.wire}</button>` : ""}${m.provisional ? " · provisional bands" : ""}</span></div>
    <div class="mm-track">${track}${ticks}${v == null ? "" : `<i class="mm-dot" style="left:${at(v)}%"></i>`}</div>
    <figcaption class="mm-scale"><span>0</span><span>${max}</span></figcaption></figure>`;
}

/** S18: "Other": VNI and the CRS index, each with its scale and an explanation from the server (r.metrics). */
export function otherMetricsHtml(r) {
  const ms = r.metrics || [];
  if (!ms.length) return "";
  return `<h3>Other</h3><div class="mm-list">${ms.map((m) => `<section class="mm-item" aria-label="${esc(m.name)}">
    <header><b>${esc(m.name)}</b>${infoTip(`ix-${m.key}-${esc(r.id || "")}`, `About ${m.name}`, [["What it represents", m.what], ["Inputs", m.inputs],
      ["How it is derived", m.how], ["What the value means", m.meaning], ["Threshold or reference", m.reference], ["What it cannot establish", m.cannot]])}</header>
    ${metricMeter(m)}${m.n === 0 ? `<p class="muted">No power line was measured in this photo, so there is no CRS value.</p>` : ""}</section>`).join("")}</div>`;
}

export function riskChip(rk) {
  if (!rk) return "";
  return `<span class="rk rk-${esc(rk.level)}" title="${esc(rk.critical_override ? "A confirmed critical hazard sets High" : "Operational triage rubric v" + (rk.rubric_version ?? ""))}"><i></i>${esc(rk.short)}</span>`;
}

export function reviewChip(st, label) {
  if (!st) return "";
  return `<span class="rs rs-${esc(st.toLowerCase())}"><i></i>${esc(label || st)}</span>`;
}

const PROB_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 2 20h20zM12 10v4M12 17v.5"/></svg>`;

function wireRow(w, probs, active) {
  const f = w.finding || {}, p = probs.get(w.index);
  return `<li><button type="button" class="rv-w${p ? " is-prob" : ""}" data-w="${w.index}" aria-pressed="${active === w.index}">
    <span class="rv-num" style="--wc:${wireColor(w, "class")}">${w.index}</span>
    <span class="rv-wt"><b>${esc(f.type || classLabel(w, true))}</b><small>${esc(p ? p.labels.join(" · ") : f.classification_label || f.citizen_label || f.label || "")}</small></span>
    <span class="rv-wv num">${f.sag ? esc(f.sag.text) : "—"}</span>
    <i class="rv-lv lv-${esc(f.meter_level || "none")}" aria-hidden="true"></i></button></li>`;
}

/** Section A. */
function knowHtml(r, opts) {
  const sc = r.screening || {}, ov = r.overview || {}, tl = r.timeline || {};
  const byIdx = new Map((r.wires || []).map((w) => [w.index, w]));
  const probs = (sc.problem_wires || []).map((p) => ({ ...p, w: byIdx.get(p.index) })).filter((p) => p.w);
  const failed = r.status === "error";
  const total = sc.total_wires ?? (r.wires || []).length;
  const scale = r.comparison?.meter_scale_max || 0.1;
  const tips = sc.photo_tips || [];
  const stats = failed ? [] : [
    [sc.problem_count ?? 0, sc.problem_count === 1 ? "problem wire" : "problem wires", (sc.problem_count ? "warn" : "ok")],
    [sc.measured_spans ?? 0, "measured against a reference", ""],
    [sc.exceeded_spans ?? 0, sc.exceeded_spans === 1 ? "exceeds its reference" : "exceed their reference", sc.exceeded_spans ? "crit" : ""],
  ];
  return `<section class="rv-know" aria-labelledby="rv-know-h">
    <p class="rv-eyebrow" id="rv-know-h">${failed ? "Photo received." : "Photo received. Here's what we found."}</p>
    <div class="rv-hero">
      <div class="rv-count${failed ? " is-failed" : ""}"><b class="num">${failed ? "—" : total}</b><span>${failed ? "Not processed" : total === 1 ? "Wire detected" : "Wires detected"}</span></div>
      <div class="rv-finding">
        <p class="rv-line">${esc(sc.finding || ov.headline || "")}</p>
        ${stats.length ? `<ul class="rv-stats">${stats.map(([k, t, tone]) => `<li class="${tone}"><b class="num">${k}</b> ${esc(t)}</li>`).join("")}</ul>` : ""}
        ${opts.mode !== "citizen" && sc.risk ? `<p class="rv-risk"><span class="rv-risk-k" title="Operational triage from the number of problem wires (configurable rubric v${esc(sc.risk.rubric_version ?? "")}); not a statutory classification. The meter on each wire grades that wire's sag.">Inspection triage</span> ${riskChip(sc.risk)}</p>` : ""}
      </div>
      <dl class="rv-times">
        <div><dt>Photo captured</dt><dd>${tl.captured_at ? esc(dt(tl.captured_at)) : `<span class="muted">Not embedded in the photo</span>`}</dd></div>
        <div><dt>Photo received</dt><dd>${esc(dt(tl.received_at || r.created_at) || "—")}</dd></div>
        <div><dt>Screening completed</dt><dd>${tl.completed_at ? esc(dt(tl.completed_at)) : `<span class="muted">${r.status === "processing" ? "In progress" : "Not completed"}</span>`}</dd></div>
      </dl>
    </div>
    ${failed ? `<p class="rv-fail">${esc(r.error?.message || "Image processing could not be completed for this photo.")}</p>` : ""}
    ${probs.length ? `<div class="rv-probs"><h3>${PROB_ICON}Problem wires <span class="num">${probs.length}</span></h3>
      <ol class="rv-plist">${probs.map((p) => `<li><button type="button" class="rv-pcard" data-w="${p.index}">
        <header><span class="rv-num" style="--wc:${wireColor(p.w, "class")}">${p.index}</span><b>${esc(p.w.finding?.type || classLabel(p.w, true))}</b>
        <span class="rv-tags">${p.labels.map((l) => `<em>${esc(l)}</em>`).join("")}</span></header>
        ${sagMeter(p.w, scale, { compact: opts.mode === "compact" })}</button></li>`).join("")}</ol></div>`
      : !failed && total ? `<p class="rv-calm">No wire has a problem observation. ${sc.unmeasured_spans ? esc(`${n(sc.unmeasured_spans, "span")} could not be measured; see How did we know?`) : ""}</p>` : ""}
    ${tips.length && opts.mode !== "admin" ? `<aside class="rv-tip" aria-label="Photo tip"><h3>Want a more complete result?</h3>
      ${tips.slice(0, opts.mode === "compact" ? 1 : 2).map((t) => `<p><span>${esc(t.observation)}</span> <b>${esc(t.action)}</b></p>`).join("")}</aside>` : ""}
  </section>`;
}

/** S18: the mask legend, from the server's list of the classes the model produced in this photo. */
function legendHtml(r) {
  const items = r.mask_legend || [];
  if (!items.length) return "";
  return `<ul class="rv-legend" id="rv-legend" aria-label="Segmentation mask legend" hidden>${items.map((c) => `<li title="${esc(c.text)}">
    <i style="--sw:var(${classVar(c.key)})" aria-hidden="true"></i><b>${esc(c.label)}</b><span>${esc(c.text)}</span></li>`).join("")}</ul>`;
}

const norm = (s) => String(s ?? "").trim().toLowerCase().replace(/\s+/g, " ");
const hay = (w) => norm([w.wire_id, `wire ${w.index}`, w.finding?.type, classLabel(w, true), classLabel(w), w.finding?.label,
  w.finding?.classification_label, w.finding?.citizen_label, ...(w._labels || [])].filter(Boolean).join(" | "));

/** S18: one wire lookup for the photo search, the list search and typed references. Accepts "3", "03", "#3",
 *  "w3", "Wire 3", the wire ID ("WF-415C41C6-W03"), or words of the type / finding. Never guesses between matches. */
export function findWire(q, wires) {
  const t = norm(q);
  if (!t) return { kind: "empty" };
  const byId = wires.find((w) => norm(w.wire_id) === t);
  if (byId) return { kind: "one", w: byId };
  if (/^wf-/.test(t)) return { kind: "none", msg: `No wire with the ID “${q.trim()}” in this inspection.` };
  const m = t.match(/^(?:wire|w|no\.?|#)?\s*#?\s*0*(\d{1,4})$/);
  if (m) {
    const i = Number(m[1]), w = wires.find((x) => x.index === i);
    if (w) return { kind: "one", w };
    const ix = wires.map((x) => x.index).sort((a, b) => a - b);
    return { kind: "none", msg: `There is no wire ${i} in this photo${ix.length ? ` (wires ${ix[0]}–${ix[ix.length - 1]})` : ""}.` };
  }
  const hits = wires.filter((w) => hay(w).includes(t));
  if (hits.length === 1) return { kind: "one", w: hits[0] };
  if (hits.length) return { kind: "many", ws: hits };
  return { kind: "none", msg: `No wire matches “${q.trim()}”.` };
}

/** Section C. */
function howHtml(r, opts) {
  const wires = r.wires || [];
  const sc = r.screening || {};
  const probs = new Map((sc.problem_wires || []).map((p) => [p.index, p]));
  const poles = (r.scene?.poles_detected || []).length;
  const ranked = [...wires].sort((a, b) => (probs.has(b.index) - probs.has(a.index)) || a.index - b.index);
  const dis = { poles: !poles, mask: !wires.length, traces: !wires.length, problems: !probs.size };
  return `<section class="rv-how" aria-labelledby="rv-how-h">
    <header class="rv-how-h"><h2 id="rv-how-h">How did we know?</h2>
      <p>${esc(`${n(poles, "pole")} detected · ${n(wires.length, "wire")} segmented · ${n(sc.measured_spans ?? 0, "span")} measured against a reference`)}</p></header>
    <div class="rv-ev">
      <div class="rv-stage">
        <div class="rv-bar" role="toolbar" aria-label="Evidence layers">
          <div class="rv-seg" role="radiogroup" aria-label="Evidence layer">${LAYERS.map(([k, t]) => `<button type="button" role="radio" data-layer="${k}" aria-checked="false" ${dis[k] ? `disabled title="${k === "poles" ? "No pole was detected" : k === "problems" ? "No problem wire" : "No wire was detected"}"` : ""}>${t}${k === "problems" && probs.size ? ` <span class="num">${probs.size}</span>` : ""}</button>`).join("")}</div>
          <div class="rv-zoom">${wires.length ? `<form class="rv-find" id="rv-find" role="search" aria-label="Find a wire in the photo"><input id="rv-find-q" name="q" type="search" autocomplete="off" spellcheck="false" placeholder="Find wire, e.g. 3" aria-describedby="rv-find-msg"><button class="btn sm" type="submit">Find</button></form>` : ""}
            <button class="btn icon sm" type="button" data-z="out" aria-label="Zoom out">−</button><output class="num" id="rv-zpct">100%</output><button class="btn icon sm" type="button" data-z="in" aria-label="Zoom in">+</button><button class="btn sm" type="button" data-z="fit" title="Fit the photo and clear the selected wire">Reset</button></div>
        </div>
        <p class="rv-find-msg" id="rv-find-msg" aria-live="polite" hidden></p>
        <div class="rv-canvas" style="--ar:${r.image?.width || 4} / ${r.image?.height || 3}"><div class="canvas-wrap" id="rv-canvas"></div></div>
        ${legendHtml(r)}
        <p class="rv-lhelp" id="rv-lhelp" aria-live="polite"></p>
      </div>
      <aside class="rv-side" aria-label="Wires">
        <div class="rv-detail" id="rv-detail" aria-live="polite"></div>
        ${wires.length ? `<h3 class="rv-lh">All wires <span class="num">${wires.length}</span></h3>
          <input class="rv-wl-q" id="rv-wl-q" type="search" autocomplete="off" spellcheck="false" placeholder="Search wires: number, ID or type" aria-label="Search the wire list" aria-controls="rv-wl">
          <p class="rv-wl-none" id="rv-wl-none" hidden>No wire matches.</p>
          <ul class="rv-wl" id="rv-wl">${ranked.map((w) => wireRow(w, probs, null)).join("")}</ul>` : `<p class="muted">No wire was detected in this photo.</p>`}
      </aside>
    </div>
    ${opts.methodHtml || ""}
  </section>`;
}

export function resultViewHtml(r, opts = {}) {
  const o = { mode: "citizen", ...opts };
  return `<div class="rv rv-${esc(o.mode)}">${knowHtml(r, o)}${o.nextHtml || ""}${r.status === "error" && !r.image?.url ? "" : howHtml(r, o)}</div>`;
}

/** Bind the evidence viewer and the wire list. Returns { viewer, select, destroy }. */
export function bindResultView(root, r, { onSelect, initialLayer, scrollFriendly = true } = {}) {
  const wires = r.wires || [];
  const byIdx = new Map(wires.map((w) => [w.index, w]));
  const probs = new Map((r.screening?.problem_wires || []).map((p) => [p.index, p]));
  for (const w of wires) w._labels = probs.get(w.index)?.labels || [];
  const host = root.querySelector("#rv-canvas");
  if (!host) return { viewer: null, select() {}, destroy() {} };
  const viewer = new OverlayViewer(host, { onSelect: (i) => select(i, "photo"), onHover: (i) => mark(i, "hover"), scrollFriendly });
  window.wfViewer = viewer; // read-only handle for automated UI checks
  const scale = r.comparison?.meter_scale_max || 0.1;
  let layer = null, selected = null;
  const detail = root.querySelector("#rv-detail");
  const showDetail = (i) => {
    const w = byIdx.get(i);
    if (!detail) return;
    if (!w) { detail.innerHTML = wires.length ? `<p class="rv-dhint">Select a wire in the photo or the list to see its measurement.</p>` : ""; return; }
    const f = w.finding || {}, p = probs.get(i);
    detail.innerHTML = `<header><span class="rv-num" style="--wc:${wireColor(w, "class")}">${w.index}</span><div><b>Wire ${w.index}</b><span>${esc(f.type || classLabel(w, true))}${w.wire_id ? ` · <span class="mono">${esc(w.wire_id)}</span>` : ""}</span></div><button type="button" class="linkish" data-clear>Clear</button></header>
      ${p ? `<p class="rv-dtags">${p.labels.map((l) => `<em>${esc(l)}</em>`).join("")}</p>` : ""}
      ${sagMeter(w, scale)}
      ${f.plain ? `<p class="rv-dplain">${esc(f.plain)}</p>` : ""}`;
  };
  const mark = (i, why) => {
    root.querySelectorAll("[data-w]").forEach((b) => {
      const on = Number(b.dataset.w) === i;
      if (why === "hover") b.classList.toggle("is-hover", on);
      else if (b.classList.contains("fx-ref")) b.classList.toggle("is-sel", on);
      else b.setAttribute("aria-pressed", String(on));
    });
    if (why !== "hover") root.querySelectorAll("tr[data-wire]").forEach((t) => t.classList.toggle("is-sel", Number(t.dataset.wire) === i));
  };
  const legend = root.querySelector("#rv-legend");
  const apply = () => {
    const P = PRESET[layer] || {};
    for (const k of VLAYERS) viewer.layers[k] = !!P[k];
    viewer.layers.overlays = layer !== "photo";
    viewer.mode = "class"; // S18: the mask is coloured by model class, as its legend says
    if (legend) legend.hidden = !P.masks;
    // the selected wire stays visible on every layer that draws wires
    viewer.setVisible(layer === "problems" ? [...new Set([...probs.keys(), ...(selected != null ? [selected] : [])])] : null);
    viewer.selected = selected;
    viewer.draw();
  };
  const setLayer = (k) => {
    const b = root.querySelector(`[data-layer="${k}"]`);
    if (!b || b.disabled) return;
    layer = k;
    root.querySelectorAll("[data-layer]").forEach((x) => x.setAttribute("aria-checked", String(x.dataset.layer === k)));
    const h = root.querySelector("#rv-lhelp"); if (h) h.textContent = LAYER_HELP[k];
    apply();
  };
  /** The one selection state: every route (photo, list, search, a wire number in any text) ends here, by wire
   *  number (the stored detections.wire_index, never a list position). */
  function select(i, from) {
    if (from === "list" && i === selected) i = null; // a second click on the same wire clears it
    if (i != null && !byIdx.has(i)) return;
    selected = i ?? null;
    // a wire hidden by the current layer (Problem wires) stays visible once selected; Original shows no wires
    if (selected != null && layer === "photo") setLayer(probs.has(selected) ? "problems" : "combined");
    mark(selected, "select"); showDetail(selected);
    apply();
    if (selected != null && from !== "photo") viewer.focus(selected);
    if (selected != null && from !== "list") {
      const row = root.querySelector(`#rv-wl [data-w="${selected}"]`);
      if (row?.closest("li")?.hidden) clearListSearch();
      row?.scrollIntoView({ block: "nearest" });
    }
    onSelect?.(selected);
  }
  const wlq = root.querySelector("#rv-wl-q"), wlNone = root.querySelector("#rv-wl-none");
  const filterList = () => {
    const t = wlq?.value || "";
    let shown = 0;
    root.querySelectorAll("#rv-wl > li").forEach((li) => {
      const w = byIdx.get(Number(li.querySelector("[data-w]")?.dataset.w));
      const res = t.trim() ? findWire(t, [w]) : null;
      li.hidden = !!res && res.kind !== "one";
      if (!li.hidden) shown++;
    });
    if (wlNone) wlNone.hidden = shown > 0;
    return shown;
  };
  function clearListSearch() { if (wlq && wlq.value) { wlq.value = ""; filterList(); } }
  wlq?.addEventListener("input", filterList);
  wlq?.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && wlq.value) { e.preventDefault(); clearListSearch(); return; }
    if (e.key !== "Enter") return;
    e.preventDefault();
    const res = findWire(wlq.value, wires);
    if (res.kind === "one") select(res.w.index, "search");
    else root.querySelector("#rv-wl > li:not([hidden]) [data-w]")?.focus();
  });
  const findMsg = root.querySelector("#rv-find-msg");
  const say = (html) => { if (!findMsg) return; findMsg.innerHTML = html || ""; findMsg.hidden = !html; };
  root.querySelector("#rv-find")?.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = e.target.q.value, res = findWire(q, wires);
    if (res.kind === "one") { say(""); select(res.w.index, "search"); return; }
    if (res.kind === "many") {
      say(`${res.ws.length} wires match “${esc(q.trim())}”. Choose one: ${res.ws.map((w) => `<button type="button" class="fx-ref" data-w="${w.index}">${w.index}</button>`).join(" ")}`);
      return;
    }
    say(res.kind === "empty" ? "Type a wire number, a wire ID or a wire type." : esc(res.msg));
  });
  host.addEventListener("zoom", (e) => { const z = root.querySelector("#rv-zpct"); if (z) z.textContent = `${Math.round(e.detail * 100)}%`; });
  root.addEventListener("click", (e) => {
    const lb = e.target.closest("[data-layer]"); if (lb) { setLayer(lb.dataset.layer); return; }
    const z = e.target.closest("[data-z]");
    if (z) {
      if (z.dataset.z === "fit") { select(null, "reset"); say(""); viewer.reset(); return; }
      ({ in: () => viewer.zoomIn(), out: () => viewer.zoomOut() })[z.dataset.z](); return;
    }
    if (e.target.closest("[data-clear]")) { select(null, "list"); return; }
    const w = e.target.closest("[data-w]");
    if (w) {
      const i = Number(w.dataset.w);
      if (w.closest("#rv-wl")) { select(i, "list"); return; }
      select(i, "ref");
      // a wire number outside the evidence panel (what we know, technical details, recommendations) brings the photo into view
      if (!w.closest(".rv-ev")) root.querySelector(".rv-how")?.scrollIntoView({ block: "start", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
  });
  root.addEventListener("pointerover", (e) => { const w = e.target.closest?.("[data-w]"); viewer.highlight(w ? Number(w.dataset.w) : null); });
  root.addEventListener("focusin", (e) => { const w = e.target.closest?.("[data-w]"); if (w) viewer.highlight(Number(w.dataset.w)); });
  root.addEventListener("pointerleave", () => viewer.highlight(null));
  // keyboard: arrows move through the wire list; Escape clears the selection
  root.querySelector("#rv-wl")?.addEventListener("keydown", (e) => {
    const b = e.target.closest("[data-w]"); if (!b) return;
    const all = [...root.querySelectorAll("#rv-wl [data-w]")], k = all.indexOf(b);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const nx = all[k + (e.key === "ArrowDown" ? 1 : -1)]; if (nx) { nx.focus(); select(Number(nx.dataset.w), "key"); } }
    if (e.key === "Escape") select(null, "key");
  });
  const first = initialLayer || (probs.size ? "problems" : wires.length ? "combined" : "photo");
  showDetail(null);
  const ready = r.image?.url ? viewer.load(r.image.url, wires, r.vegetation || []).then(() => {
    viewer.setPoles(r.scene?.poles_detected || []);
    setLayer(first);
  }) : Promise.resolve();
  return { viewer, select, setLayer, ready, find: (q) => findWire(q, wires), get selected() { return selected; },
    destroy: () => viewer.destroy() };
}
