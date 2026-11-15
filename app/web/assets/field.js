// Field screener (v2.13): the landing page's product, built for a lineman's 2-second read. Result screen:
//   1. hero verdict card: status pill, worst CRS, measured vs total spans, one plain-English next action
//   2. the photo with Original / Detected spans / Full wire mask and an overlay-opacity slider
//   3. every span, grouped by urgency, each unmeasured one with its reason and what to do; linked to the canvas
//   4. model and CV diagnostics (entropy, flip disagreement, noise, timings, suppressed ground traces), collapsed
// Screenings are remembered on this device only; the server never lists them publicly.

import { api, esc, fmt, pct, ago, waitForAnalysis } from "./lib.js";
import { OverlayViewer, wireColor } from "./viewer.js";
import { onStatus, startStatus } from "./status.js";
import { toast } from "./toast.js";

const $ = (s, r = document) => r.querySelector(s);
const form = $("#form");
const stages = { idle: $("#idle"), working: $("#working"), result: $("#result") };
const RECENT_KEY = "wf-field-recent";
let viewer = null, map = null, coords = null, previewUrl = null, mode = "checking";

const CLASS_SHORT = { "power-distribution-lines": "Power line", "telecom-cable": "Telecom cable", "abandoned-wire": "Abandoned wire" };
const CLASS_VAR = { "power-distribution-lines": "--cls-power", "telecom-cable": "--cls-telecom", "abandoned-wire": "--cls-dead" };
const GROUPS = [["high", "High risk"], ["medium", "Moderate"], ["low", "Low risk"], ["none", "Not measured"]];
const VERDICT_CLASS = { "AT RISK - HIGH": "high", "AT RISK - MODERATE": "medium", "NOT AT RISK": "low", "INCONCLUSIVE - RETAKE PHOTO": "none" };
// Field labels for the screener's verdicts (display only; the API verdicts are unchanged). A retake is its own
// state: an unreadable photo must never look like a clean pole.
const PILL = { "AT RISK - HIGH": "Hazard detected", "AT RISK - MODERATE": "Review required", "NOT AT RISK": "Not at risk",
  "INCONCLUSIVE - RETAKE PHOTO": "Retake photo" };
const ICON_NEXT = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>`;

function show(name) {
  for (const [k, el] of Object.entries(stages)) el.hidden = k !== name;
  $("#main").focus({ preventScroll: true });
  scrollTo({ top: 0 });
}

// ------------------------------------------------------------------ status
startStatus({ retryFailed: false });
onStatus((s) => {
  mode = s.mode;
  const pill = $("#mode-pill");
  pill.dataset.mode = s.mode;
  pill.querySelector("span").textContent = { field: "Detection online", validation: "Validation mode", offline: "Server offline", checking: "Checking system…" }[s.mode];
  pill.title = s.mode === "validation" ? "No detection model on this server: reference polygons are needed" : "";
  $("#validation").hidden = s.mode !== "validation";
  refreshSubmit();
});

/** In validation mode a photo alone cannot be screened, so the button says so before upload. */
function refreshSubmit() {
  const hasPhoto = !!form.elements.file.files[0];
  const needsPolygons = mode === "validation" && !form.elements.labels.files[0];
  $("#submit").disabled = !hasPhoto || needsPolygons;
  $("#submit-why").hidden = !(hasPhoto && needsPolygons);
}

// ------------------------------------------------------------------ theme (landing page tokens; light for direct sun)
function syncThemeButton() {
  const dark = document.documentElement.dataset.theme !== "light";
  $("#theme").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0c0d0f" : "#f3f2ef";
}
$("#theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("wf-field-theme", next); } catch { /* private mode */ }
  syncThemeButton();
  if (viewer) viewer.draw();
});
syncThemeButton();

// ------------------------------------------------------------------ recent (this device only)
function recent() { try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]"); } catch { return []; } }
function remember(item) {
  const list = [item, ...recent().filter((r) => r.id !== item.id)].slice(0, 12);
  try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* private mode */ }
  renderRecent();
}
function renderRecent() {
  const list = recent();
  $("#recent").hidden = !list.length;
  $("#recent-list").innerHTML = list.map((r) => `<li><a href="/workspace/result/${r.id}" data-id="${r.id}">
    <img src="/api/analysis/${r.id}/image" alt="" loading="lazy" onerror="this.closest('li').remove()">
    <span><b>${esc(r.pole)}</b><small>${ago(r.at)}</small></span>
    <span class="fx-tag ${VERDICT_CLASS[r.verdict] || "none"}">${esc(PILL[r.verdict] || r.status || "")}</span></a></li>`).join("");
}
$("#recent-list").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]"); if (!a) return;
  e.preventDefault(); openResult(a.dataset.id, true);
});
renderRecent();

// ------------------------------------------------------------------ form
const fileInput = form.elements.file;
function setFile(file) {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = file ? URL.createObjectURL(file) : null;
  const img = $("#preview");
  img.hidden = !file;
  if (file) img.src = previewUrl;
  $("#drop").toggleAttribute("data-filled", !!file);
  refreshSubmit();
}
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
form.elements.labels.addEventListener("change", refreshSubmit);
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.setAttribute("data-over", ""); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.removeAttribute("data-over")));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = [...e.dataTransfer.files].find((x) => x.type.startsWith("image/"));
  if (!f) { toast.error("That is not an image"); return; }
  const dt = new DataTransfer(); dt.items.add(f); fileInput.files = dt.files; setFile(f);
});

$("#locate").addEventListener("click", () => {
  const b = $("#locate");
  if (coords) { coords = null; b.setAttribute("aria-pressed", "false"); b.querySelector("span").textContent = "Use my location"; return; }
  if (!navigator.geolocation) { toast.error("This browser cannot share a location"); return; }
  b.querySelector("span").textContent = "Locating…";
  navigator.geolocation.getCurrentPosition((p) => {
    coords = { lat: p.coords.latitude, lon: p.coords.longitude };
    b.setAttribute("aria-pressed", "true");
    b.querySelector("span").textContent = `Location added (±${Math.round(p.coords.accuracy)} m)`;
  }, (err) => {
    b.querySelector("span").textContent = "Use my location";
    toast.error("Location not available", { description: err.code === 1 ? "Permission was denied." : "Try again outdoors." });
  }, { enableHighAccuracy: true, timeout: 15000 });
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (!fileInput.files[0]) return;
  const fd = new FormData();
  fd.append("file", fileInput.files[0]);
  for (const k of ["pole_code", "barangay"]) { const v = form.elements[k].value.trim(); if (v) fd.append(k, v); }
  const labels = form.elements.labels.files[0];
  if (labels) fd.append("labels", labels);
  if (coords) { fd.append("latitude", coords.lat.toFixed(7)); fd.append("longitude", coords.lon.toFixed(7)); }
  run(fd, previewUrl);
});

$("#sample").addEventListener("click", async () => {
  const [img, lab] = await Promise.all([fetch("/assets/samples/synthetic-pole.jpg").then((r) => r.blob()),
    fetch("/assets/samples/synthetic-pole.txt").then((r) => r.blob())]);
  const fd = new FormData();
  fd.append("file", new File([img], "synthetic-pole.jpg", { type: "image/jpeg" }));
  fd.append("labels", new File([lab], "synthetic-pole.txt", { type: "text/plain" }));
  fd.append("pole_code", "SYN-SAMPLE");
  if (coords) { fd.append("latitude", coords.lat.toFixed(7)); fd.append("longitude", coords.lon.toFixed(7)); }
  run(fd, "/assets/samples/synthetic-pole.jpg");
});

// ------------------------------------------------------------------ run
function step(name, state) { $(`[data-step="${name}"]`).dataset.state = state; }

async function run(fd, imgUrl) {
  $("#work-img").src = imgUrl || "";
  ["upload", "gps", "analyze"].forEach((s) => step(s, "todo"));
  step("upload", "doing");
  show("working");
  let id;
  try {
    ({ id } = await api("/api/analysis/upload", { method: "POST", body: fd }));
  } catch (err) {
    show("idle");
    if (err.code === "model_unavailable") {
      $("#validation").hidden = false;
      mode = "validation"; refreshSubmit();
      toast.error("Screening isn't available yet", { duration: 9000,
        description: "This server has no detection model installed. Ask your administrator, or load the sample." });
    } else if (err.status === 429) {
      toast.error("Upload limit reached", { description: err.message });
    } else {
      toast.error("Photo not accepted", { description: err.message });
    }
    return;
  }
  step("upload", "done"); step("gps", "done"); step("analyze", "doing");
  history.pushState({}, "", `/workspace/result/${id}`);
  try {
    await waitForAnalysis(id);
    step("analyze", "done");
    await openResult(id, false);
  } catch (err) {
    show("idle");
    toast.error("Screening did not finish", { description: err.message });
  }
}

async function openResult(id, push) {
  if (push) history.pushState({}, "", `/workspace/result/${id}`);
  let r;
  try {
    const s = await api(`/api/analysis/${id}`);
    if (s.status === "processing") {
      step("upload", "done"); step("gps", "done"); step("analyze", "doing"); show("working");
      $("#work-img").src = `/api/analysis/${id}/image`;
      await waitForAnalysis(id);
    }
    r = await api(`/api/analysis/${id}/result`);
  } catch (err) {
    toast.error("Screening not found", { description: err.message });
    history.replaceState({}, "", "/workspace"); show("idle"); return;
  }
  remember({ id, pole: r.pole.code, verdict: r.hazard?.verdict, status: r.status, at: r.created_at });
  show("result");  // visible first: Leaflet and the canvas measure their containers on creation
  renderResult(r);
}

// ------------------------------------------------------------------ result model
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const group = (w) => w.level || "none";
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
function sagFactors(r) { return r.hazard?.factors?.sag || {}; }

/** The verdict sentence: what drove it, in field language, no repetition of the hazard list. */
function verdictText(r) {
  const hz = r.hazard;
  const scored = r.wires.filter((w) => w.crs != null);
  const worst = scored.reduce((a, b) => (!a || b.crs > a.crs ? b : a), null);
  if (r.status === "error") return { cls: "none", pill: "Screening failed", title: "This photo could not be screened.", body: "Retake it, or contact your administrator if it happens again." };
  if (!hz) {
    if (!worst) return { cls: "none", pill: "Retake photo", title: "No wire could be measured.", body: "No span was traced end to end in this photo." };
    return { cls: worst.level, pill: worst.level.toUpperCase(), title: `Worst span: CRS ${fmt(worst.crs, 1)}/10.`, body: "" };
  }
  const cls = VERDICT_CLASS[hz.verdict] || "none";
  const sag = sagFactors(r);
  const veg = hz.factors?.vegetation || [];
  const n = sag.scored_spans ?? scored.length;
  if (hz.verdict.startsWith("INCONCLUSIVE")) {
    return { cls, pill: PILL[hz.verdict], title: "The photo can't confirm this pole is safe.",
      body: `${r.quality?.reason ? `${r.quality.reason}.` : "The photo cannot be read reliably."}${scored.length ? ` ${plural(scored.length, "span")} measured, but a clear result needs a better photo.` : ""}` };
  }
  const nHigh = (sag.high || []).length, nMed = (sag.medium || []).length;
  const contact = veg.filter((v) => v.gap_px === 0);
  if (cls === "high" || cls === "medium") {
    const bits = [];
    if (worst && (nHigh >= (sag.k_high ?? 1) || nHigh + nMed >= (sag.k_medium ?? 1)))
      bits.push(nHigh >= (sag.k_high ?? 1) ? `${nHigh} of ${n} measured spans sag beyond the high band` : `${nHigh + nMed} of ${n} measured spans sag into the medium band or beyond`);
    if (contact.length) bits.push(`vegetation touches ${plural(contact.length, "span")}`);
    return { cls, pill: PILL[hz.verdict], title: cls === "high" ? "Hazard on this pole: schedule a site visit." : "Review this pole at the next inspection round.",
      body: bits.length ? `${bits.join("; ").replace(/^./, (c) => c.toUpperCase())}.` : "See the spans below." };
  }
  return { cls, pill: PILL[hz.verdict], title: "No hazard found on this pole.",
    body: n ? `No excess sag is corroborated across ${plural(n, "measured span")}${veg.length ? `; closest vegetation is ${veg[0].gap_px.toFixed(0)} px from span ${veg[0].span}` : ""}.` : "No span was long enough to measure sag." };
}

const spanList = (ids) => `${ids.length > 1 ? "spans" : "span"} ${ids.map((i) => `<button type="button" data-goto="${i}">${i}</button>`).join(", ")}`;

/** One line per hazard kind (not per span), most severe first. */
function physicalHazards(r) {
  const out = [];
  const veg = r.hazard?.factors?.vegetation || [];
  const byClass = (cls, pred) => veg.filter((v) => v.class === cls && pred(v)).map((v) => v.span).sort((a, b) => a - b);
  const powerContact = byClass("power-distribution-lines", (v) => v.gap_px === 0);
  const powerNear = veg.filter((v) => v.class === "power-distribution-lines" && v.gap_px > 0 && v.gap_px <= 15);
  const telecomContact = byClass("telecom-cable", (v) => v.gap_px === 0);
  const sag = sagFactors(r);
  if ((sag.high || []).length && sag.high.length >= (sag.k_high ?? 1)) {
    const worst = r.wires.filter((w) => w.level === "high").sort((a, b) => b.crs - a.crs)[0];
    out.push({ sev: "high", html: `<b>Critical sag</b> on ${spanList([...sag.high].sort((a, b) => a - b))}; worst is span ${worst.index} (CRS ${fmt(worst.crs, 1)}).` });
  }
  if (powerContact.length) out.push({ sev: "high", html: `<b>Vegetation touching power</b> ${spanList(powerContact)}.` });
  if (powerNear.length) out.push({ sev: "moderate", html: `<b>Vegetation close to power</b> ${spanList(powerNear.map((v) => v.span).sort((a, b) => a - b))} (within ${Math.max(...powerNear.map((v) => v.gap_px)).toFixed(0)} px).` });
  if (telecomContact.length) out.push({ sev: "moderate", html: `<b>Vegetation touching telecom</b> ${spanList(telecomContact)}.` });
  return out;
}

/** The single next step for the operator. */
function nextAction(r, v) {
  const q = r.quality;
  if (v.cls === "none") return r.retake_guidance || q?.advice || "Retake the photo: frame the pole and both spans against the sky, in even light.";
  if (r.retake_guidance) return r.retake_guidance;
  if (v.cls === "high") return "Flag this pole for a site inspection. Download the PDF report for the crew.";
  if (v.cls === "medium") return "Add this pole to the next inspection round. Download the PDF report for the record.";
  if (q && q.grade !== "A") return `Result usable (photo grade ${q.grade}). ${q.reason ? `${q.reason}.` : ""}`;
  return "No action needed for this pole.";
}

function clearanceCell(w) {
  const c = w.veg_clearance;
  if (!c) return "—";
  if (c.gap_px === 0) return `<span class="fx-contact">Contact</span>`;
  const m = c.gap_m_approx ? `<span class="fx-reason">≈ ${((c.gap_m_approx[0] + c.gap_m_approx[1]) / 2).toFixed(2)} m</span>` : "";
  return `${c.gap_px.toFixed(0)} px${m}`;
}

function statusCell(w) {
  const notes = (w.notes || []).map((n) => `<span class="fx-reason">${esc(n)}</span>`).join("");
  const act = w.action ? `<span class="fx-do">${esc(w.action)}</span>` : "";
  if (w.level) return `<span class="fx-tag ${w.level}">${{ high: "High", medium: "Moderate", low: "Low" }[w.level]}</span>${notes}${act}`;
  return `<span class="fx-tag none">${w.status === "service_drop" ? "Drop wire" : "Not measured"}</span><span class="fx-reason">${esc(w.reason || "Not measured")}</span>${notes}${act}`;
}

function spansTable(r) {
  const rank = { high: 0, medium: 1, low: 2, none: 3 };
  const rows = [...r.wires].sort((a, b) => rank[group(a)] - rank[group(b)] || (b.crs ?? -1) - (a.crs ?? -1) || a.index - b.index);
  const counts = Object.fromEntries(GROUPS.map(([g]) => [g, rows.filter((w) => group(w) === g).length]));
  let body = "";
  for (const [g, label] of GROUPS) {
    const ws = rows.filter((w) => group(w) === g);
    if (!ws.length) continue;
    body += `<tr class="grp" data-group="${g}"><td colspan="5">${label} · ${ws.length}</td></tr>`;
    body += ws.map((w) => `<tr data-wire="${w.index}" data-group="${g}" tabindex="0">
      <td><span class="fx-id" style="--wc:${wireColor(w)}">${w.index}</span></td>
      <td><span class="fx-cls" style="--c:${css(CLASS_VAR[w.class])}"><i></i>${esc(CLASS_SHORT[w.class] || w.class)}</span></td>
      <td class="n">${w.crs == null ? "—" : `${fmt(w.crs, 1)}<span class="fx-reason" style="text-align:right">sag ${pct(w.sag_ratio, 2)}</span>`}</td>
      <td class="n opt">${clearanceCell(w)}</td>
      <td>${statusCell(w)}</td></tr>`).join("");
  }
  return { counts, html: rows.length ? `<table class="fx-spans"><thead><tr><th>#</th><th>Conductor</th><th style="text-align:right">CRS</th>
      <th class="opt" style="text-align:right">Vegetation</th><th>Status and next step</th></tr></thead><tbody>${body}</tbody></table>`
    : `<p class="fx-empty">No wire was detected in this photo.</p>` };
}

/** Everything technical, for engineers and thesis reviewers: collapsed by default. */
function diagnostics(r) {
  const sag = sagFactors(r);
  const rule = r.parameters?.hazard_rule || {};
  const q = r.quality || {};
  const iq = r.segmentation_input?.quality || {};
  const wm = r.wire_mask;
  const unc = q.uncertainty || {};
  const tel = r.telemetry || {};
  const sup = r.suppressed_spans || [];
  const kv = (pairs) => `<dl class="fx-kv">${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  const notes = (r.hazard?.reasons || []).filter((x) => x.startsWith("NOTE") || x.startsWith("INFO"));
  const ms = (k) => (r.timings_ms?.[k] != null ? `${fmt(r.timings_ms[k], 0)} ms` : null);
  return `
    <section><h3>Sag corroboration</h3>${rule.sag_rule === "corroborated" && sag.scored_spans != null ? `
      <p>High band: <b class="fx-num">${(sag.high || []).length}</b> of <b class="fx-num">${sag.scored_spans}</b> measured spans; <b class="fx-num">${sag.k_high}</b> needed above the ${(rule.p0_high * 100).toFixed(1)}% per-span noise floor (one-sided binomial, α = ${rule.alpha}).</p>
      <p>Medium or above: <b class="fx-num">${(sag.high || []).length + (sag.medium || []).length}</b>; <b class="fx-num">${sag.k_medium}</b> needed (noise floor ${(rule.p0_medium * 100).toFixed(1)}%).</p>`
      : `<p>Sag rule: ${esc(rule.sag_rule || "—")}.</p>`}${notes.length ? `<ul class="fx-notes">${notes.map((x) => `<li>${esc(x.replace(/^(NOTE|INFO): /, ""))}</li>`).join("")}</ul>` : ""}</section>
    <section><h3>Photo reliability</h3>${kv([["Grade", esc(q.grade)], ["Reliability R", fmt(q.reliability, 2)],
      ["Entropy U", fmt(unc.entropy, 3)], ["Flip disagreement D", fmt(unc.disagreement, 3)], ["Mean L*", fmt(iq.mean_l, 1)], ["Contrast", fmt(iq.contrast, 1)],
      ["Near-white share", iq.bright_share == null ? null : pct(iq.bright_share, 0)], ["Thin-line contrast", fmt(iq.line_contrast, 1)],
      ["Sharpness", fmt(iq.sharpness, 0)], ["Noise σ", fmt(iq.noise_sigma, 1)], ["Motion coherence", fmt(iq.motion_coherence, 2)]])}</section>
    ${wm ? `<section><h3>WireNet mask</h3>${kv([["Wire pixels", wm.wire_pixels?.toLocaleString()], ["Inside extracted spans", wm.span_coverage == null ? "—" : pct(wm.span_coverage, 0)],
      ["Power px", wm.pixels?.["power-distribution-lines"]?.toLocaleString()], ["Telecom px", wm.pixels?.["telecom-cable"]?.toLocaleString()],
      ["Vegetation px", wm.pixels?.Vegetation?.toLocaleString()]])}</section>` : ""}
    <section><h3>Ground structures ignored</h3>${sup.length ? `<ul class="fx-suppressed">${sup.map((s) => `<li><span class="fx-tag none">${esc(CLASS_SHORT[s.wire_class] || s.wire_class)}</span> ${pct(s.ground_share, 0)} on road / pavement · x ${Math.round(s.bbox[0])}–${Math.round(s.bbox[2])}, y ${Math.round(s.bbox[1])}–${Math.round(s.bbox[3])}</li>`).join("")}</ul>
      <p class="fx-reason">${esc(sup[0].reason)}. These traces are never scored.</p>` : `<p>None: no wire trace lay on the road or pavement.</p>`}</section>
    <section><h3>Processing</h3>${kv([["Model", esc(tel.model || r.segmentation_source)], ["Inference", tel.inference_s != null ? `${fmt(tel.inference_s, 2)} s` : null],
      ["Preprocess", ms("preprocess")], ["Segmentation", ms("segmentation")], ["Geometry and risk", ms("geometry_and_risk")],
      ["Resolution", `${r.image.width} × ${r.image.height}`], ["Enhancement", esc((r.segmentation_input?.enhancement || []).join(", ") || "none")],
      ["Polarity", r.flags.includes("polarity_inverted") ? "inverted (night)" : "as photographed"], ["Chord gate", `${fmt(r.parameters?.min_chord_px, 0)} px`],
      ["Weights", esc(tel.wirenet_weights)], ["Config", esc(r.parameters?.fingerprint)]])}</section>`;
}

// ------------------------------------------------------------------ result view
function renderResult(r) {
  const v = verdictText(r);
  const hz = physicalHazards(r);
  const loc = r.pole.location;
  const tbl = spansTable(r);
  const scored = r.wires.filter((w) => w.crs != null);
  const worst = scored.reduce((a, b) => (!a || b.crs > a.crs ? b : a), null);
  const total = r.wires.length;
  const hasMask = !!r.wire_mask;
  const hasVeg = (r.vegetation || []).length > 0;
  const sup = (r.suppressed_spans || []).length;

  stages.result.innerHTML = `
    <header class="fx-res-head">
      <div><p class="eyebrow">Pole inspection${r.pole.barangay ? ` · ${esc(r.pole.barangay)}` : ""}</p>
        <h1>Pole ${esc(r.pole.code)}</h1>
        <span class="fx-sub">${esc(new Date(r.created_at).toLocaleString())} · ${esc(r.image.original_filename || "")}</span></div>
      <div class="fx-res-actions">
        ${r.status !== "error" ? `<a class="btn" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener" id="pdf">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7zM14 3v5h5M12 11v6M9 14l3 3 3-3"/></svg>PDF report</a>` : ""}
        <button class="btn primary" id="again" type="button">New inspection</button>
      </div>
    </header>

    <section class="glass fx-hero ${v.cls}" aria-label="Field verdict">
      <div>
        <span class="fx-pill ${v.cls}" id="verdict-pill"><i></i>${esc(v.pill)}</span>
        <h2>${esc(v.title)}</h2>
        <p>${esc(v.body)}</p>
        ${hz.length ? `<ul class="fx-hazards">${hz.map((h) => `<li class="${h.sev}"><span>${h.html}</span></li>`).join("")}</ul>` : ""}
        <div class="fx-action" id="next-action">${ICON_NEXT}<span>${esc(nextAction(r, v))}</span></div>
      </div>
      <div class="fx-kpis">
        <div class="fx-kpi"><div class="k">Worst CRS</div>
          <div class="v ${worst?.level || ""}">${worst ? fmt(worst.crs, 1) : "—"}<small> /10</small></div>
          <div class="s">${worst ? `Span ${worst.index} · ${{ high: "high", medium: "moderate", low: "low" }[worst.level]} sag` : "No measurable span"}</div></div>
        <div class="fx-kpi"><div class="k">Spans measured</div>
          <div class="v">${scored.length}<small> / ${total}</small></div>
          <div class="fx-meter"><i style="--w:${total ? Math.round((100 * scored.length) / total) : 0}%"></i></div>
          <div class="s">${total - scored.length ? `${total - scored.length} not measured: <button class="fx-link" type="button" data-filter-go="none">see why</button>` : "Every span measured"}${sup ? ` · ${plural(sup, "ground trace")} ignored` : ""}</div></div>
      </div>
    </section>

    <div class="fx-res-grid">
      <section class="glass fx-viewer" aria-label="Photo and detections">
        <div class="fx-toolbar" role="toolbar" aria-label="View">
          <div class="fx-seg" role="radiogroup" aria-label="Layer">
            <span class="thumb" aria-hidden="true"></span>
            <button type="button" role="radio" data-view="photo" aria-checked="false">Original</button>
            <button type="button" role="radio" data-view="spans" aria-checked="true">Detected spans</button>
            <button type="button" role="radio" data-view="mask" aria-checked="false" ${hasMask ? "" : "disabled title=\"No wire mask for this analysis\""}>Full wire mask</button>
          </div>
          <label class="fx-opacity" id="opacity-wrap"><span>Overlay</span><input type="range" id="opacity" min="0" max="100" value="100" aria-label="Overlay opacity"><output for="opacity">100%</output></label>
          <span class="fx-spacer"></span>
          <button class="btn sm" data-layer="vegetation" aria-pressed="${hasVeg}" ${hasVeg ? "" : "disabled title=\"No vegetation detected\""}>Vegetation</button>
          <span class="fx-zoom"><button class="btn sm" data-zoom="out" aria-label="Zoom out">−</button><button class="btn sm" data-zoom="in" aria-label="Zoom in">+</button><button class="btn sm" data-zoom="fit">Fit</button></span>
        </div>
        <div class="fx-canvas"><div class="canvas-wrap" id="fs-canvas"></div></div>
        <div class="fx-legend" aria-label="Legend">
          <span data-legend="spans"><i style="--c:${css("--risk-high")}"></i>High</span><span data-legend="spans"><i style="--c:${css("--risk-med")}"></i>Moderate</span>
          <span data-legend="spans"><i style="--c:${css("--risk-low")}"></i>Low</span><span data-legend="spans"><i style="--c:${css("--risk-none")}"></i>Not measured</span>
          <span data-legend="mask" hidden><i style="--c:${css("--cls-power")}"></i>Power px</span><span data-legend="mask" hidden><i style="--c:${css("--cls-telecom")}"></i>Telecom px</span>
          ${hasVeg ? `<span data-legend="veg"><i style="--c:${css("--cls-veg")}"></i>Vegetation</span>` : ""}
        </div>
      </section>

      <section class="glass fx-spans-panel" aria-label="Inspected spans">
        <div class="fx-table-head"><h2>Spans</h2>
          <div class="fx-filters" role="group" aria-label="Filter spans">
            <button type="button" data-filter="all" aria-pressed="true">All<b>${total}</b></button>
            ${GROUPS.map(([g, label]) => `<button type="button" data-filter="${g}" aria-pressed="false" ${tbl.counts[g] ? "" : "disabled"}>${label}<b>${tbl.counts[g]}</b></button>`).join("")}
          </div></div>
        <div class="fx-scroll" id="fx-scroll">${tbl.html}</div>
      </section>
    </div>

    <section class="glass fx-drawer" id="diag">
      <button class="fx-drawer-btn" type="button" aria-expanded="false" aria-controls="diag-body">
        <b>Model and CV diagnostics</b><span>For engineers and reviewers: uncertainty, photo measurements, timings</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="fx-drawer-body" id="diag-body" role="region" aria-label="Diagnostics"><div><div class="fx-drawer-inner">${diagnostics(r)}</div></div></div>
    </section>

    ${loc ? `<section class="glass fx-map"><div id="fs-map" role="region" aria-label="Pole location"></div></section>`
      : `<p class="fx-muted">No location recorded: the photo had no GPS and none was added.</p>`}
    ${r.reviews?.length ? `<p class="fx-muted">Reviewed by ${esc(r.reviews.at(-1).by)}${r.reviews.at(-1).override_level ? ` · level set to ${r.reviews.at(-1).override_level}` : ""}</p>` : ""}`;

  // ---------------- viewer
  viewer?.destroy();
  viewer = new OverlayViewer($("#fs-canvas"), { onSelect: (i) => mark(i, true) });
  window.wfViewer = viewer; // read-only handle for automated UI checks (scripts/ui_check.py)
  viewer.layers.sag = true;
  viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => {
    if (worst) { viewer.selected = worst.index; viewer.draw(); mark(worst.index, false); }
    if (hasMask) viewer.setWireMask(r.wire_mask.url);
  }).catch(() => toast.error("Photo could not be displayed"));

  const root = stages.result;
  const table = root.querySelector(".fx-spans");
  const mark = (i, scroll) => {
    table?.querySelectorAll("tr[data-wire]").forEach((tr) => {
      const on = Number(tr.dataset.wire) === i;
      tr.setAttribute("aria-selected", String(on));
      if (on && scroll) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
  };
  const select = (i) => { const next = viewer.selected === i ? null : i; viewer.select(next); mark(next, true); };
  table?.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-wire]"); if (tr) select(Number(tr.dataset.wire)); });
  table?.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-wire]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(Number(tr.dataset.wire)); }
  });
  table?.addEventListener("pointerover", (e) => { const tr = e.target.closest("tr[data-wire]"); viewer.hover = tr ? Number(tr.dataset.wire) : null; viewer.draw(); });
  table?.addEventListener("pointerleave", () => { viewer.hover = null; viewer.draw(); });
  root.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-goto]");
    if (b) { const i = Number(b.dataset.goto); viewer.select(i); mark(i, true); }
  });

  // ---------------- view: segmented control (Original | Detected spans | Full wire mask) + opacity
  const seg = root.querySelector(".fx-seg"), thumb = seg.querySelector(".thumb");
  const opacity = root.querySelector("#opacity"), opWrap = root.querySelector("#opacity-wrap");
  const moveThumb = () => {
    const on = seg.querySelector('[aria-checked="true"]');
    thumb.style.width = `${on.offsetWidth}px`;
    thumb.style.transform = `translateX(${on.offsetLeft}px)`;
  };
  const legend = (k, on) => root.querySelectorAll(`[data-legend="${k}"]`).forEach((x) => { x.hidden = !on; });
  const setView = (name) => {
    seg.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.view === name)));
    moveThumb();
    viewer.set("overlays", name !== "photo");
    const spans = name === "spans";
    viewer.layers.masks = spans; viewer.layers.catenary = spans; viewer.layers.sag = spans;
    viewer.set("wiremask", name === "mask");
    legend("spans", spans); legend("mask", name === "mask");
    opWrap.toggleAttribute("data-off", name === "photo");
  };
  seg.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b && !b.disabled) setView(b.dataset.view); });
  seg.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const opts = [...seg.querySelectorAll("[data-view]:not(:disabled)")];
    const i = opts.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const next = opts[(i + (e.key === "ArrowRight" ? 1 : opts.length - 1)) % opts.length];
    setView(next.dataset.view); next.focus();
  });
  requestAnimationFrame(moveThumb);
  addEventListener("resize", moveThumb);
  opacity.addEventListener("input", () => {
    viewer.setOpacity(opacity.value / 100);
    opWrap.querySelector("output").textContent = `${opacity.value}%`;
  });
  root.querySelector('[data-layer="vegetation"]')?.addEventListener("click", (e) => {
    const b = e.currentTarget, on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on)); viewer.set("vegetation", on); legend("veg", on);
  });
  root.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
    ({ in: () => viewer.zoomIn(), out: () => viewer.zoomOut(), fit: () => viewer.reset() })[b.dataset.zoom]();
  }));

  // ---------------- span filters
  const filter = (f) => {
    root.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.filter === f)));
    table?.querySelectorAll("tr[data-group]").forEach((tr) => { tr.hidden = f !== "all" && tr.dataset.group !== f; });
  };
  root.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => filter(b.dataset.filter)));
  root.querySelector("[data-filter-go]")?.addEventListener("click", (e) => {
    filter(e.currentTarget.dataset.filterGo);
    root.querySelector(".fx-spans-panel").scrollIntoView({ behavior: "smooth", block: "start" });
  });

  // ---------------- diagnostics drawer
  const drawer = root.querySelector("#diag"), dbtn = drawer.querySelector(".fx-drawer-btn");
  dbtn.addEventListener("click", () => {
    const open = !drawer.hasAttribute("data-open");
    drawer.toggleAttribute("data-open", open);
    dbtn.setAttribute("aria-expanded", String(open));
  });
  $("#again").addEventListener("click", reset);

  // ---------------- map
  map?.remove(); map = null;
  if (loc && window.L) {
    map = L.map("fs-map", { zoomControl: false, attributionControl: true, scrollWheelZoom: false }).setView([loc.lat, loc.lon], 17);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
    L.marker([loc.lat, loc.lon], { icon: L.divIcon({ className: "", iconSize: [20, 20], iconAnchor: [10, 10],
      html: `<div class="pin" style="--pc:${css({ low: "--risk-low", medium: "--risk-med", high: "--risk-high" }[VERDICT_CLASS[r.hazard?.verdict]] || "--risk-none")}"></div>` }),
      title: r.pole.code }).addTo(map);
  }
}

function reset() {
  form.reset(); setFile(null); coords = null;
  $("#locate").setAttribute("aria-pressed", "false"); $("#locate span").textContent = "Use my location";
  history.pushState({}, "", "/workspace");
  show("idle");
}

// ------------------------------------------------------------------ routing
function route() {
  const m = location.pathname.match(/^\/workspace\/result\/([0-9a-f]{32})$/);
  if (m) openResult(m[1], false); else show("idle");
}
addEventListener("popstate", route);
route();
