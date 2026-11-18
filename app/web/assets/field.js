// Field screener (v2.14). Result screen, in reading order:
//   1. inspection header: pole, time, file, PDF report, new inspection
//   2. outcome strip: verdict, physical hazards, next step; counts per risk level and the worst span (click to filter / select)
//   3. the photo (largest element): Original / Spans / Mask / Overlay presets, independent layers (segmentation pixels,
//      span masks, inferred gaps, centerlines, catenary, chord and sag, span ends), opacity, vegetation, zoom, fit, reset
//   4. beside it: the selected span in full, and the span table with risk filters (row <-> photo selection kept in sync)
//   5. inspection quality for the field; technical diagnostics for engineers, collapsed
// Screenings are remembered on this device only; the server never lists them publicly.

import { api, esc, fmt, pct, ago, waitForAnalysis } from "./lib.js?v=2.23";
import { OverlayViewer, wireColor, classLabel, isMeasured } from "./viewer.js?v=2.23";
import { onStatus, startStatus } from "./status.js?v=2.23";
import { toast } from "./toast.js?v=2.23";

const $ = (s, r = document) => r.querySelector(s);
const form = $("#form");
const stages = { idle: $("#idle"), working: $("#working"), result: $("#result") };
const RECENT_KEY = "wf-field-recent";
let viewer = null, map = null, coords = null, previewUrl = null, mode = "checking", chosen = null, maxMb = 64;
let retakeOf = null; // v2.17: id of the analysis the next upload replaces (set by "Retake photo")

const CLASS_SHORT = { "power-distribution-lines": "Power line", "telecom-cable": "Telecom cable", "abandoned-wire": "Abandoned wire" };
const CLASS_VAR = { "power-distribution-lines": "--cls-power", "telecom-cable": "--cls-telecom", "abandoned-wire": "--cls-dead" };
const VERDICT_CLASS = { "AT RISK - HIGH": "high", "AT RISK - MODERATE": "medium", "NOT AT RISK": "low", "INCONCLUSIVE - RETAKE PHOTO": "none" };
// Field labels for the screener's verdicts (display only; the API verdicts are unchanged). A retake is its own
// state: an unreadable photo must never look like a clean pole.
const PILL = { "AT RISK - HIGH": "High risk found", "AT RISK - MODERATE": "Moderate risk found", "NOT AT RISK": "No high risk found",
  "INCONCLUSIVE - RETAKE PHOTO": "Assessment incomplete" };  // v2.23: also low coverage or partial spans, not only clarity

function show(name) {
  for (const [k, el] of Object.entries(stages)) el.hidden = k !== name;
  $("#main").focus({ preventScroll: true });
  scrollTo({ top: 0 });
}

// ------------------------------------------------------------------ server state (not shown; it only gates the form)
startStatus();
onStatus((s) => {
  mode = s.mode;
  document.documentElement.dataset.server = s.mode; // read by scripts/e2e_check.py; never displayed
  $("#validation").hidden = s.mode !== "validation";
  refreshSubmit();
});

/** In validation mode a photo alone cannot be screened, so the button says so before upload. */
function refreshSubmit() {
  const hasPhoto = !!chosen;
  const needsPolygons = mode === "validation" && !form.elements.labels.files[0];
  $("#submit").disabled = !hasPhoto || needsPolygons;
  $("#submit-why").hidden = !(hasPhoto && needsPolygons);
}

// ------------------------------------------------------------------ theme (dark by default, as the landing page)
function syncThemeButton() {
  const dark = document.documentElement.dataset.theme !== "light";
  $("#theme").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0c0d0f" : "#f6f5f2";
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
    <span><b>${esc(r.name || r.file || r.pole)}</b><small>${esc([r.place, ago(r.at)].filter(Boolean).join(" · "))}</small></span>
    <span class="fx-tag ${VERDICT_CLASS[r.verdict] || "none"}">${esc(PILL[r.verdict] || r.status || "")}</span></a></li>`).join("");
}
$("#recent-list").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]"); if (!a) return;
  e.preventDefault(); openResult(a.dataset.id, true);
});
renderRecent();

// ------------------------------------------------------------------ form
// The same limits as the server (app/pipeline/acquisition.py, Settings.max_upload_mb); the server re-checks everything.
const TYPES = ["image/jpeg", "image/png", "image/webp", "image/tiff", "image/bmp", "image/gif"];
fetch("/api/config").then((r) => (r.ok ? r.json() : null)).then((c) => {
  // the analysis config does not carry the upload limit; keep the server default unless it is ever exposed
  if (c?.max_upload_mb) { maxMb = c.max_upload_mb; $("#max-mb").textContent = maxMb; }
}).catch(() => {});
const fileInput = form.elements.file;
const fmtSize = (b) => (b >= 2 ** 20 ? `${(b / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);
function fileError(msg) { const e = $("#file-error"); e.textContent = msg || ""; e.hidden = !msg; }
function setFile(file) {
  fileError("");
  if (file && !TYPES.includes(file.type) && !/\.(jpe?g|png|webp|tiff?|bmp|gif)$/i.test(file.name)) {
    fileError(`“${file.name}” is not a supported image. Use a JPEG, PNG, WebP, TIFF, BMP or GIF photo.`);
    file = null;
  } else if (file && file.size > maxMb * 2 ** 20) {
    fileError(`“${file.name}” is ${fmtSize(file.size)}; the limit is ${maxMb} MB.`);
    file = null;
  }
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = file ? URL.createObjectURL(file) : null;
  chosen = file;
  $("#filled").hidden = !file;
  $("#drop").toggleAttribute("data-filled", !!file);
  if (file) {
    const img = $("#preview");
    img.src = previewUrl;
    $("#file-name").textContent = file.name;
    $("#file-meta").textContent = fmtSize(file.size);
    img.onload = () => { $("#file-meta").textContent = `${img.naturalWidth} × ${img.naturalHeight} px · ${fmtSize(file.size)}`; };
    img.onerror = () => { $("#file-meta").textContent = `${fmtSize(file.size)} · no preview in this browser (TIFF); it can still be screened`; };
  } else {
    fileInput.value = "";
  }
  refreshSubmit();
}
fileInput.addEventListener("change", () => setFile(fileInput.files[0] || null));
$("#clear-file").addEventListener("click", () => { setFile(null); fileInput.focus(); });
form.elements.labels.addEventListener("change", refreshSubmit);
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.setAttribute("data-over", ""); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, () => drop.removeAttribute("data-over")));
drop.addEventListener("drop", (e) => {
  e.preventDefault();
  const f = e.dataTransfer.files[0];
  if (f) setFile(f);
});

// location: asked for only when the button is pressed; failure never blocks the upload; nothing is guessed
function locateStatus(text, cls = "") { const s = $("#locate-status"); s.textContent = text; s.className = `fx-locate-status ${cls}`; }
$("#locate").addEventListener("click", () => {
  const b = $("#locate");
  if (coords) {
    coords = null; b.setAttribute("aria-pressed", "false"); b.querySelector("span").textContent = "Use my location";
    locateStatus(""); return;
  }
  if (!navigator.geolocation) { locateStatus("This browser cannot share a location. You can still screen the photo.", "err"); return; }
  b.disabled = true; locateStatus("Waiting for your browser…");
  navigator.geolocation.getCurrentPosition((p) => {
    coords = { lat: p.coords.latitude, lon: p.coords.longitude };
    b.disabled = false; b.setAttribute("aria-pressed", "true"); b.querySelector("span").textContent = "Remove location";
    locateStatus(`${coords.lat.toFixed(5)}, ${coords.lon.toFixed(5)} (±${Math.round(p.coords.accuracy)} m) will be saved with this inspection.`, "ok");
  }, (err) => {
    b.disabled = false;
    locateStatus(err.code === 1 ? "Location permission was not given. You can still screen the photo."
      : "Your location could not be found. You can still screen the photo.", "err");
  }, { enableHighAccuracy: true, timeout: 15000 });
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (!chosen) { fileError("Choose a photo first."); return; }
  const fd = new FormData();
  fd.append("file", chosen);
  for (const k of ["inspection_name", "city", "barangay"]) { const v = form.elements[k].value.trim(); if (v) fd.append(k, v); }
  const labels = form.elements.labels.files[0];
  if (labels) fd.append("labels", labels);
  if (coords) { fd.append("latitude", coords.lat.toFixed(7)); fd.append("longitude", coords.lon.toFixed(7)); }
  if (retakeOf) fd.append("retake_of", retakeOf);
  retakeOf = null; $("#retake-note")?.remove();
  run(fd, previewUrl);
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
        description: "This server has no detection model installed. Ask your administrator." });
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
  remember({ id, pole: r.pole.code, name: r.inspection?.name, file: r.image.original_filename,
    place: [r.inspection?.barangay, r.inspection?.city].filter(Boolean).join(", "), verdict: r.hazard?.verdict, status: r.status, at: r.created_at });
  if (r.guardrail?.requires_retake && !r.guardrail.choice) {  // v2.17: ask before showing a doubtful result
    const choice = await retakePrompt(r);
    if (choice === "retake") { startRetake(r); return; }
  }
  show("result");  // visible first: Leaflet and the canvas measure their containers on creation
  renderResult(r);
}

// ------------------------------------------------------------------ v2.17 retake guardrail
const GUARD_TITLE = { retake_recommended: "This photo may not give a reliable assessment",
  highly_uncertain: "This photo is very unlikely to give a reliable assessment" };

/** Modal shown before the results when the backend sets requires_retake. Resolves "retake" or "proceed"; the
 *  choice is recorded on the analysis (it never changes the result). Escape / backdrop = proceed with the warning. */
function retakePrompt(r) {
  const g = r.guardrail;
  const dlg = document.createElement("dialog");
  dlg.className = "fx-guard";
  dlg.setAttribute("aria-labelledby", "guard-title");
  dlg.innerHTML = `
    <h2 id="guard-title">${esc(GUARD_TITLE[g.quality_status] || GUARD_TITLE.retake_recommended)}</h2>
    <p>Some wires may be hard to tell apart, or the system found conflicting wire types or problems with the photo.
      For a more reliable result, take another photo.</p>
    ${g.quality_reasons?.length ? `<ul>${g.quality_reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    <div class="fx-guard-actions">
      <button class="btn" type="button" value="proceed">Proceed anyway</button>
      <button class="btn primary" type="button" value="retake" autofocus>Retake photo</button>
    </div>`;
  document.body.append(dlg);
  return new Promise((resolve) => {
    const done = async (action) => {
      dlg.close(); dlg.remove();
      try { await api(`/api/analysis/${r.id}/guardrail`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }) }); } catch { /* recording the choice must never block the user */ }
      r.guardrail.choice = { action };
      resolve(action);
    };
    dlg.querySelectorAll("button").forEach((b) => b.addEventListener("click", () => done(b.value)));
    dlg.addEventListener("cancel", (e) => { e.preventDefault(); done("proceed"); });
    dlg.showModal();
  });
}

/** Back to the upload form, keeping the inspection's name and place; the next upload is linked as its retake. */
function startRetake(r) {
  reset();
  retakeOf = r.id;
  const insp = r.inspection || {};
  for (const k of ["inspection_name", "city", "barangay"]) {
    const v = k === "inspection_name" ? insp.name : insp[k];
    if (v && form.elements[k]) form.elements[k].value = v;
  }
  const note = document.createElement("p");
  note.id = "retake-note"; note.className = "fx-retake-note";
  note.textContent = "Retaking the photo. The earlier result is kept and will be linked to the new one.";
  form.prepend(note);
}

function guardBanner(r) {
  const g = r.guardrail;
  if (!g) return "";
  if (g.requires_retake) {
    return `<section class="fx-guard-banner" role="alert" aria-label="Assessment warning">
      <div><h2>Assessment may be inaccurate</h2>
      <p>Some wires could not be identified reliably from this photo. Review the findings carefully. This automated
        screening is not a safety certification.</p>
      ${g.quality_reasons?.length ? `<ul>${g.quality_reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
      ${g.uncertain_strand_count ? `<p>Wires whose type could not be told apart are shown in grey.</p>` : ""}</div>
      <button class="btn" type="button" id="guard-retake">${ICON.plus}Retake photo</button></section>`;
  }
  if (g.quality_status === "needs_attention" && g.quality_reasons?.length) {
    return `<p class="fx-guard-note"><b>Check before relying on this result.</b> ${g.quality_reasons.map(esc).join(" ")}</p>`;
  }
  return "";
}

// ------------------------------------------------------------------ result model (v2.15: written for non-specialists)
import { css, group, pc, SECTION, SEVERITY, SCORE_HELP, sagFactors, finding, limitations, assessment, recommendations, measuredText } from "./findings.js?v=2.23";
// ------------------------------------------------------------------ result view
// S6 recovery (register D-24): the v2.18 result page (commit f74250e), restored. Photo and wire table side by side,
// Original / Wires / Mask / Both, overlay opacity, Vegetation, Layers, zoom with Reset, every wire section in a
// filterable table, the selected section with its measurement details, specific photo limitations and next steps,
// and the technical diagnostics drawer for signed-in staff. Engineering corrections kept: no severity without a
// validated reference (S4), measurement states and the per-wire diagnosis (S5).
const ICON = {
  pdf: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7zM14 3v5h5M12 11v6M9 14l3 3 3-3"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
  zin: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2M11 8v6M8 11h6"/></svg>`,
  zout: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2M8 11h6"/></svg>`,
  fit: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>`,
  reset: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7M4 4v4h4"/></svg>`,
  layers: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 4 8 4-8 4-8-4zM4 12l8 4 8-4M4 16l8 4 8-4"/></svg>`,
  leaf: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19c0-8 5-13 14-14-1 9-6 14-14 14zM5 19l7-7"/></svg>`,
};
// view presets; every layer can also be switched on its own in the Layers menu
const PRESETS = {
  photo: {},
  spans: { masks: 1, inferred: 1, catenary: 1, sag: 1, ends: 1, labels: 1, poles: 1 },
  mask: { wiremask: 1 },
  overlay: { wiremask: 1, masks: 1, inferred: 1, catenary: 1, sag: 1, ends: 1, labels: 1, poles: 1 },
};
const LAYERS = [
  ["wiremask", "Model mask", "Every pixel the model marked as wire, including small pieces that are not part of any wire section"],
  ["masks", "Wire sections", "The detected pixels of each wire section, coloured by wire type (or by condition where a section was assessed)"],
  ["inferred", "Estimated gaps", "Short gaps where a wire was hidden or faint and was joined by estimation (dashed); not detected"],
  ["skeleton", "Centre lines", "The traced centre line of each wire section"],
  ["catenary", "Fitted curve", "The hanging curve fitted to each measured wire section"],
  ["sag", "Sag measurement", "For the selected section: the straight line between its ends and how far the wire hangs below it"],
  ["ends", "Section ends", "Where each traced wire section starts and ends in the photo (not where it is attached to a pole)"],
  ["labels", "Section numbers", ""],
  ["poles", "Utility poles", "Poles found by the pole detector (dashed boxes); context only, they never create or join a wire"],
];

/** Where a wire section stands: a severity only when it was assessed against a validated reference (S4); otherwise
 *  what was measured. Never a severity inferred from a measurement. */
function mstate(w) {
  if (w.status === "scored" && w.level) return w.level;
  if (w.status === "service_drop") return "drop";
  if (!isMeasured(w)) return "unmeasured";
  return w.reason_code === "endpoint_unavailable" ? "partial" : "measured";
}
const STATE_LABEL = { high: SEVERITY.high, medium: SEVERITY.medium, low: SEVERITY.low, measured: "Measured",
  partial: "Visible part", unmeasured: "Not measured", drop: "Drop wire" };
const STATE_ORDER = ["high", "medium", "low", "measured", "partial", "unmeasured", "drop"];
const conditionCell = (w) => { const g = mstate(w); return `<span class="fx-risk ${g}"><i></i>${STATE_LABEL[g]}</span>`; };
const vegText = (veg) => (veg ? (veg.gap_px === 0 ? "Overlaps it in the photo" : `${veg.gap_px.toFixed(0)} px away in the photo`) : "None detected nearby");

function spansTable(r, mode) {
  const rank = Object.fromEntries(STATE_ORDER.map((k, i) => [k, i]));
  // assessed sections by score; measured sections by image-derived sag ratio (a measurement, stated as such)
  const rows = [...r.wires].sort((a, b) => rank[mstate(a)] - rank[mstate(b)] || (b.crs ?? -1) - (a.crs ?? -1)
    || (b.sag_ratio ?? -1) - (a.sag_ratio ?? -1) || a.index - b.index);
  const counts = Object.fromEntries(STATE_ORDER.map((g) => [g, rows.filter((w) => mstate(w) === g).length]));
  const body = rows.map((w) => `<tr data-wire="${w.index}" data-group="${mstate(w)}" tabindex="0" aria-selected="false">
      <td class="id"><span class="fx-id" style="--wc:${wireColor(w, mode)}">${w.index}</span></td>
      <td class="cls">${esc(classLabel(w, true))}</td>
      <td class="risk">${conditionCell(w)}</td>
      <td class="cmp">${w.comparison?.position ? `<span class="fx-cmp ${w.comparison.position}" title="${esc(POS[w.comparison.position])}: ${esc(measuredText(w))} vs ${pp(w.comparison.reference)}">${{ below: "Below", at: "At", above: "Above" }[w.comparison.position]}</span>` : "—"}</td>
      <td class="find" title="${esc(finding(w))}">${esc(finding(w, true))}</td></tr>`).join("");
  return { rows, counts, html: rows.length ? `<table class="fx-spans"><thead><tr><th class="id">Wire</th><th>Type</th><th>Condition</th><th title="Image sag ratio compared with the configured reference (provisional)">vs ${r.comparison ? pp(r.comparison.sag_ratio) : "ref."}</th><th>Finding</th></tr></thead>
      <tbody>${body}</tbody></table>` : `<p class="fx-empty">No wire was detected in this photo.</p>` };
}

/* ------------------------------------------------------------------ the three-zone sag meter
 * v2.21 (7c5a716, last shown at 0916fd2; S7 register D-25, S8 register D-26). meter() is the original, unchanged, for
 * a wire assessed against a validated reference (server sag_bands). sagMeter() draws the same track, zones, reference
 * tick, dot, key and legend for the configured comparison reference (server w.comparison: the provisional 3 %), with
 * zones from the reference and the wire's own ±1 px resolution, never from invented cut-offs. */
const pp = (x) => `${(x * 100).toFixed(1)}%`;
const keyAt = (v) => (v < 12 ? "at-start" : v > 88 ? "at-end" : "");
const POS = { below: "Below reference", at: "At reference (within resolution)", above: "Above reference" };

/** Sag meter with the zones where this wire's level changes (from the configuration, via the server). */
function meter(w, g) {
  const lim = w.delta_lim, b = w.sag_bands;
  const max = Math.max(w.sag_ratio, lim, b?.critical_from || 0) * 1.3;
  const x = (v) => `${Math.min(100, (v / max) * 100)}%`;
  return `<div class="rx-meter" role="img" aria-label="Sag ${pp(w.sag_ratio)}${b ? `; minor sag from ${pp(b.minor_from)}, critical from ${pp(b.critical_from)}` : ""}; reference ${pp(lim)}">
    <div class="rx-meter-track">${b ? `<i class="z z-low" style="left:0;width:${x(b.minor_from)}"></i><i class="z z-medium" style="left:${x(b.minor_from)};width:calc(${x(b.critical_from)} - ${x(b.minor_from)})"></i><i class="z z-high" style="left:${x(b.critical_from)};right:0"></i>` : ""}
      <i class="rx-meter-ref" style="left:${x(lim)}"></i><i class="rx-meter-dot ${g}" style="left:${x(w.sag_ratio)}" title="This wire: ${pp(w.sag_ratio)}"></i></div>
    <div class="rx-meter-key">${b ? `<span style="left:${x(b.minor_from)}">${pp(b.minor_from)}</span><span style="left:${x(b.critical_from)}">${pp(b.critical_from)}</span>` : ""}<span class="ref" style="left:${x(lim)}">Reference ${pp(lim)}</span></div>
    ${b ? `<p class="rx-zones"><span><i class="z-low"></i>Within reference</span><span><i class="z-medium"></i>Minor sag</span><span><i class="z-high"></i>Critical</span></p>` : ""}</div>`;
}

/** The meter for the configured comparison reference. Measured: three zones (below / at within resolution / above),
 * the reference tick and the wire's dot with its resolution band. Not measured: the reference split only, no dot. */
function sagMeter(w) {
  if (w.status === "scored" && w.delta_lim) return meter(w, group(w));
  const c = w.comparison;
  if (!c) return "";
  const ref = c.reference, part = c.state === "measured_partial";
  const note = `<p class="rx-meter-note">${esc(c.label)}: owner-selected, not a validated limit; compared with the image sag ratio, which is not physical sag. Amber: the reference lies within this wire's ±1 px measurement resolution. A comparison, not an assessment.</p>`;
  if (!c.position) {
    const max = ref * 2, x = (v) => `${Math.min(100, (v / max) * 100)}%`;
    return `<div class="rx-meter cmp empty" role="img" aria-label="Not measured: no image sag ratio to compare with the ${esc(c.label)}">
      <div class="rx-meter-track"><i class="z z-low" style="left:0;width:${x(ref)}"></i><i class="z z-high" style="left:${x(ref)};right:0"></i><i class="rx-meter-ref" style="left:${x(ref)}"></i></div>
      <div class="rx-meter-key"><span class="ref" style="left:${x(ref)}">Reference ${pp(ref)} · not measured</span></div>
      <p class="rx-zones"><span><i class="z-low"></i>Below reference</span><span><i class="z-high"></i>Above reference</span></p>${note}</div>`;
  }
  const res = w.sag_ratio_resolution || 0, z = c.zones;
  const max = Math.max(w.sag_ratio + res, z.above_from) * 1.3;
  const v = (x) => Math.min(100, Math.max(0, (x / max) * 100)), x = (q) => `${v(q)}%`;
  const at = v(w.sag_ratio);
  return `<div class="rx-meter cmp ${c.position}" role="img" aria-label="Image sag ratio ${measuredText(w)}${part ? ", visible part only" : ""}; ${esc(c.label)} ${pp(ref)}; ${POS[c.position]}, difference ${c.difference_pp > 0 ? "+" : ""}${c.difference_pp.toFixed(1)} percentage points">
    <div class="rx-meter-track"><i class="z z-low" style="left:0;width:${x(z.at_from)}"></i><i class="z z-medium" style="left:${x(z.at_from)};width:calc(${x(z.above_from)} - ${x(z.at_from)})"></i><i class="z z-high" style="left:${x(z.above_from)};right:0"></i>
      ${res ? `<i class="rx-meter-res" style="left:${v(Math.max(0, w.sag_ratio - res))}%;width:${Math.max(v(w.sag_ratio + res) - v(Math.max(0, w.sag_ratio - res)), 0.6)}%"></i>` : ""}
      <i class="rx-meter-ref" style="left:${x(ref)}"></i><i class="rx-meter-dot ${c.position}${part ? " partial" : ""}" style="left:${at}%" title="This wire: image sag ratio ${measuredText(w)}"></i></div>
    <div class="rx-meter-key"><span class="ref ${keyAt(v(ref))}" style="left:${x(ref)}">Reference ${pp(ref)}</span><span class="val ${keyAt(at)}" style="left:${at}%">This wire ${measuredText(w)}${part ? " (visible part)" : ""}</span></div>
    <p class="rx-zones"><span><i class="z-low"></i>Below reference</span><span><i class="z-medium"></i>At reference (±resolution)</span><span><i class="z-high"></i>Above reference</span></p>${note}</div>`;
}

/** The selected wire section: the plain reading first, its diagnosis, the measurement detail folded underneath. */
function spanDetail(r, w, mode) {
  if (!w) return `<p class="fx-detail-empty">Select a wire section in the list or on the photo to see it highlighted and read its details.</p>`;
  const f = w.fit || {};
  const p = w.provenance;
  const dg = w.diagnosis;
  const kv = (pairs) => `<dl class="fx-dl">${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  const scored = w.status === "scored";
  const measured = isMeasured(w);
  return `<header><span class="fx-id" style="--wc:${wireColor(w, mode)}">${w.index}</span>
      <b>${esc(classLabel(w))}</b>${conditionCell(w)}</header>
    <p class="fx-finding">${esc(finding(w))}.</p>
    ${w.action && !scored ? `<p class="fx-do"><b>What would help:</b> ${esc(w.action)}.</p>` : ""}
    ${w.class_status === "uncertain" ? `<p class="fx-note">The photo does not show clearly whether this is a power or a telecom line, so no type is claimed (grey on the photo).</p>` : ""}
    ${p && p.inferred_px ? `<p class="fx-note">About ${pc(p.centerline_inferred_share, 0)} of this wire's traced length was hidden or too faint and was filled by estimation (dashed on the photo).</p>` : ""}
    ${scored ? kv([
      ["Sag", `${pc(w.sag_ratio)} of its length`],
      ["Allowed sag", w.delta_lim ? pc(w.delta_lim) : null],
      [`<abbr title="${esc(SCORE_HELP)}">Sag score</abbr>`, w.crs == null ? null : `${fmt(w.crs, 1)} of 10`],
      ["Vegetation", vegText(w.veg_clearance)],
    ]) : measured ? kv([
      [`Image sag ratio${w.reason_code === "endpoint_unavailable" ? " (visible part)" : ""}`, esc(measuredText(w))],
      ["Reference", w.comparison ? `${pp(w.comparison.reference)} <small>(${esc(w.comparison.status)})</small>` : "None validated"],
      ["Difference", w.comparison?.position ? `${w.comparison.difference_pp > 0 ? "+" : w.comparison.difference_pp < 0 ? "−" : ""}${Math.abs(w.comparison.difference_pp).toFixed(1)} points` : null],
      ["Compared", w.comparison?.position ? `<span class="fx-cmp ${w.comparison.position}" title="${esc(POS[w.comparison.position])}">${{ below: "Below", at: "At", above: "Above" }[w.comparison.position]} reference</span>` : null],
      ["Vegetation", vegText(w.veg_clearance)],
    ]) : kv([["Reference", w.comparison ? `${pp(w.comparison.reference)} <small>(${esc(w.comparison.status)}); not compared: not measured</small>` : null], ["Vegetation", vegText(w.veg_clearance)]])}
    ${sagMeter(w)}
    ${dg ? `<ul class="fx-diag-wire" aria-label="What the analysis found for this wire">${dg.lines.filter((l) => l.key !== "detection").map((l) => `<li data-k="${esc(l.key)}">${esc(l.text)}.</li>`).join("")}</ul>
      ${dg.needed.length ? `<p class="fx-complete"><b>To complete the assessment:</b> ${esc(dg.needed.join(". "))}.</p>` : ""}` : ""}
    <details class="fx-more"><summary>Measurement details</summary>${kv([
      ["Sag score (CRS)", w.crs == null ? null : fmt(w.crs, 3)],
      ["Chord L", f.chord_px == null ? null : `${fmt(f.chord_px, 1)} px`],
      ["Sag δ", w.sag_px == null || !measured ? null : `${fmt(w.sag_px, 2)} px`],
      ["δ / L (image)", w.sag_ratio == null ? null : pc(w.sag_ratio, 3)],
      ["Resolution (1 px)", w.sag_ratio_resolution == null ? null : `± ${pc(w.sag_ratio_resolution, 3)}${w.sag_ratio_resolution_derived ? " (derived on read: record predates S4)" : ""}`],
      ["δ_lim", w.delta_lim == null ? null : pc(w.delta_lim, 2)],
      ["Fit residual", f.rms_residual_px == null ? null : `${fmt(f.rms_residual_px, 2)} px`],
      ["Inclination", f.inclination_deg == null ? null : `${fmt(f.inclination_deg, 1)}°`],
      ["Mean wire probability", w.confidence == null ? null : fmt(w.confidence, 3)],
      ["Type evidence", w.class_evidence ? Object.entries(w.class_evidence).filter(([, v]) => v >= 0.005).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${esc(CLASS_SHORT[k] || k)} ${pc(v, 0)}`).join(" · ") : null],
      ["Ends at a pole", f.pole_ends == null ? null : `${f.pole_ends} of 2`],
      ["Inferred share", p ? pc(p.centerline_inferred_share, 1) : "n/a (reference polygons)"],
      ["Status code", esc(w.reason_code ? `${w.status} · ${w.reason_code}` : w.status)],
      ["Reason (technical)", w.reason ? esc(w.reason) : null],
    ])}</details>`;
}

/** S5: what the analysis found for this photo (app/services/diagnostics.py). */
function diagHtml(r) {
  const d = r.diagnosis;
  if (!d || r.status === "error" || !r.wires.length) return "";
  const li = (xs) => `<ul>${xs.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>`;
  const reasons = Object.entries(d.not_measured_reasons || {});
  return `<section class="rx-diag fx-after" aria-labelledby="rx-diag-h"><h2 id="rx-diag-h">What the analysis found</h2>
    <p class="rx-diag-stage">${esc(d.processing)}. ${d.counts.detected} detected · ${d.counts.measured} measured · ${d.counts.measured_partial} visible part only · ${d.counts.not_measured} not measured${d.counts.not_applicable ? ` · ${d.counts.not_applicable} drop wires` : ""} · ${d.counts.assessed} assessed for severity.</p>
    ${reasons.length ? `<p class="rx-diag-stage">Not measured: ${esc(reasons.map(([k, n]) => `${k.toLowerCase()} (${n})`).join(", "))}.</p>` : ""}
    <div class="rx-diag-cols">
      ${d.observations.length ? `<div><h3>Observed in the image</h3>${li(d.observations)}</div>` : ""}
      ${d.unavailable.length ? `<div><h3>Not available from one photo</h3>${li(d.unavailable)}</div>` : ""}
      ${d.needed.length ? `<div><h3>What would complete the assessment</h3>${li(d.needed)}</div>` : ""}
    </div></section>`;
}

/** Everything technical, grouped, for administrators and researchers (signed-in staff: the public record does not
 *  carry these fields, v2.20). Collapsed by default. */
function diagnostics(r) {
  const sag = sagFactors(r);
  const rule = r.parameters?.hazard_rule || {};
  const q = r.quality || {};
  const iq = r.segmentation_input?.quality || {};
  const wm = r.wire_mask;
  const unc = q.uncertainty || {};
  const tel = r.telemetry || {};
  const sup = (r.suppressed_spans || []).filter((s) => !s.unavailable);
  const kv = (pairs) => `<dl class="fx-kv">${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  const ms = (k) => (r.timings_ms?.[k] != null ? `${fmt(r.timings_ms[k], 0)} ms` : null);
  const inf = r.wires.reduce((a, w) => a + (w.provenance?.inferred_px || 0), 0);
  return `
    <section><h3>Model information</h3>${kv([["Model", esc(tel.model || r.segmentation_source)], ["Segmentation source", esc(r.segmentation_source)],
      ["WireNet weights", esc(tel.wirenet_weights)], ["Detector weights", esc(tel.detector_weights)],
      ["Polarity", (r.flags || []).includes("polarity_inverted") ? "inverted (night)" : "as photographed"],
      ["Enhancement", esc((r.segmentation_input?.enhancement || []).join(", ") || "none")]])}</section>
    <section><h3>Image quality</h3>${kv([["Grade", esc(q.grade)], ["Reliability R", q.reliability == null ? null : fmt(q.reliability, 3)], ["Reason", esc(q.reason)],
      ["Entropy U", unc.entropy == null ? null : fmt(unc.entropy, 3)], ["Flip disagreement D", unc.disagreement == null ? null : fmt(unc.disagreement, 3)],
      ["Mean L*", iq.mean_l == null ? null : fmt(iq.mean_l, 1)], ["Contrast", iq.contrast == null ? null : fmt(iq.contrast, 1)],
      ["Near-white share", iq.bright_share == null ? null : pct(iq.bright_share, 0)], ["Thin-line contrast", iq.line_contrast == null ? null : fmt(iq.line_contrast, 1)],
      ["Sharpness", iq.sharpness == null ? null : fmt(iq.sharpness, 0)], ["Noise σ", iq.noise_sigma == null ? null : fmt(iq.noise_sigma, 1)],
      ["Motion coherence", iq.motion_coherence == null ? null : fmt(iq.motion_coherence, 2)], ["Resolution", `${r.image.width} × ${r.image.height}`]])}</section>
    <section><h3>Segmentation metrics</h3>${wm ? kv([["Wire pixels (model)", wm.wire_pixels?.toLocaleString()], ["Inside wire sections", wm.span_coverage == null ? "—" : pct(wm.span_coverage, 0)],
      ["Fragments outside sections", wm.wire_pixels_in_spans == null ? null : `${(wm.wire_pixels - wm.wire_pixels_in_spans).toLocaleString()} px`],
      ["Inferred gap pixels", `${inf.toLocaleString()} px`],
      ["Power px", wm.pixels?.["power-distribution-lines"]?.toLocaleString()], ["Telecom px", wm.pixels?.["telecom-cable"]?.toLocaleString()],
      ["Abandoned px", wm.pixels?.["abandoned-wire"]?.toLocaleString()], ["Vegetation px", wm.pixels?.Vegetation?.toLocaleString()]]) : "<p>No model mask (reference polygons).</p>"}
      <h4>Ignored as not wires / not vegetation</h4>${sup.length ? `<ul class="fx-suppressed">${sup.map((s) => `<li>${esc(s.wire_class === "Vegetation" ? "Vegetation" : (CLASS_SHORT[s.wire_class] || s.wire_class))} · ${esc(s.reason_code === "structure_edge" ? "structure outline" : s.reason_code === "not_vegetation" ? "not vegetation" : `${pct(s.ground_share, 0)} on road / pavement`)} · x ${Math.round(s.bbox[0])}–${Math.round(s.bbox[2])}, y ${Math.round(s.bbox[1])}–${Math.round(s.bbox[3])}</li>`).join("")}</ul>
      ${[...new Set(sup.map((s) => s.reason))].map((t) => `<p>${esc(t)}.</p>`).join("")}<p>These are never measured.</p>` : "<p>None: no trace lay on the road or along a structure outline, and every vegetation region was confirmed.</p>"}
      ${r.scene?.poles_detected ? `<h4>Poles</h4><p>${r.scene.poles_detected.length} utility pole${r.scene.poles_detected.length === 1 ? "" : "s"} found, outlined with dashed boxes on the photo (context only: a pole never creates or joins a wire).</p>` : ""}</section>
    <section><h3>Statistical analysis</h3>${rule.sag_rule === "corroborated" && sag.scored_spans ? `
      <p>High band: <b class="fx-num">${(sag.high || []).length}</b> of <b class="fx-num">${sag.scored_spans}</b> assessed spans; <b class="fx-num">${sag.k_high}</b> needed above the ${(rule.p0_high * 100).toFixed(1)}% per-span noise floor (one-sided binomial, α = ${rule.alpha}).</p>
      <p>Medium or above: <b class="fx-num">${(sag.high || []).length + (sag.medium || []).length}</b>; <b class="fx-num">${sag.k_medium}</b> needed (noise floor ${(rule.p0_medium * 100).toFixed(1)}%).</p>`
      : `<p>No span was assessed (no validated sag reference), so no sag rule was applied.</p>`}
      <p>Server verdict: <b>${esc(r.hazard?.verdict || "—")}</b></p>
      <ul class="fx-notes">${(r.hazard?.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>
    <section><h3>Processing performance</h3>${kv([["Total", tel.inference_s != null ? `${fmt(tel.inference_s, 2)} s` : null],
      ["Preprocess", ms("preprocess")], ["Segmentation", ms("segmentation")], ["Geometry and risk", ms("geometry_and_risk")]])}</section>
    <section><h3>Configuration and execution</h3>${kv([["Config fingerprint", esc(r.parameters?.fingerprint)], ["Current config", esc(r.parameters?.current_fingerprint)],
      ["Chord gate", r.parameters?.min_chord_px ? `${fmt(r.parameters.min_chord_px, 0)} px` : "off (resolution reported)"],
      ["Provisional blocks", esc((r.parameters?.provisional || []).join(", "))], ["Assessment basis", esc(r.metadata?.assessment_basis)],
      ["Pole code", esc(r.pole?.code)], ["Analysis id", `<span class="fx-num">${esc(r.id)}</span>`], ["Submitted via", esc(r.submitted_via)],
      ["Flags", esc((r.flags || []).join(", ") || "none")]])}</section>`;
}

function renderResult(r) {
  const a = assessment(r);
  const lims = limitations(r);
  const recs = recommendations(r, a, lims);
  const loc = r.pole.location;
  const anyScored = r.wires.some((w) => w.status === "scored" && w.level);
  // S5: with no assessed span there is no severity to colour by; wires are coloured by type (never a severity)
  const mode = anyScored ? "risk" : "class";
  const tbl = spansTable(r, mode);
  const c = tbl.counts;
  // first selection: the most severe assessed section (v2.18), else the measured section with the largest
  // image-derived ratio (the first row of the table)
  const first = tbl.rows.find((w) => (w.status === "scored" ? w.level !== "low" : isMeasured(w)));
  const hasMask = !!r.wire_mask;
  const hasVeg = (r.vegetation || []).length > 0;
  const when = new Date(r.created_at);
  const insp = r.inspection || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");
  const dc = r.diagnosis?.counts;
  // a retake helps only wires that were not measured or only partly measured, never a missing reference (S5)
  const fixable = r.wires.filter((w) => (!isMeasured(w) && w.status !== "service_drop") || w.reason_code === "endpoint_unavailable").length;
  const coverage = !anyScored && dc && r.wires.length
    ? `${dc.measured + dc.measured_partial} of ${r.wires.length} wire sections were measured in the image${dc.measured_partial ? ` (${dc.measured_partial} visible part only)` : ""}; ${dc.assessed} assessed for severity.`
    : a.coverage;
  const COUNTS = anyScored ? [["high", "High"], ["medium", "Moderate"], ["low", "Low"]].map(([g]) => [g, SEVERITY[g]])
    : [["measured", "Measured"], ["partial", "Visible part only"], ["unmeasured", "Not measured"]];
  const FILTERS = anyScored ? [["high", SEVERITY.high], ["medium", SEVERITY.medium], ["low", SEVERITY.low], ["measured", "Measured"], ["partial", "Visible part"], ["unmeasured", "Not measured"], ["drop", "Drop"]]
    : [["measured", "Measured"], ["partial", "Visible part"], ["unmeasured", "Not measured"], ["drop", "Drop"]];
  const legendSpans = anyScored
    ? [["--risk-high", SEVERITY.high], ["--risk-med", SEVERITY.medium], ["--risk-low", SEVERITY.low], ["--risk-none", SEVERITY.none]]
    : [["--cls-power", "Power line"], ["--cls-telecom", "Telecom / fibre"], ["--cls-dead", "Abandoned"], ["--cls-unknown", "Type uncertain"]];

  stages.result.innerHTML = `
    <header class="fx-insp">
      <div class="fx-insp-id">
        <h1>${esc(insp.name || r.image.original_filename || "Pole inspection")}</h1>
        <p>${place ? `<span>${esc(place)}</span>` : ""}
          <time datetime="${esc(r.created_at)}">${esc(when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))}, ${esc(when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))}</time>
          ${insp.name && r.image.original_filename ? `<span class="fx-file" title="Original file name">${esc(r.image.original_filename)}</span>` : ""}</p>
      </div>
      <div class="fx-insp-actions">
        ${r.status !== "error" ? `<a class="btn" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener" id="pdf">${ICON.pdf}PDF report</a>` : ""}
        <button class="btn primary" id="again" type="button">${ICON.plus}New inspection</button>
      </div>
    </header>

    ${guardBanner(r)}
    ${r.wires.length && fixable / r.wires.length > 0.5 ? `<div class="rx-retake" role="status"><b>More than half of the detected wires (${fixable} of ${r.wires.length}) could not be measured completely from this photo.</b> Retake the photo from a few steps along the street so the wires separate, with both poles in the frame.</div>` : ""}
    <section class="fx-summary ${a.cls}" aria-label="Overall assessment">
      <div class="fx-outcome">
        <span class="fx-pill ${a.cls}" id="verdict-pill">${esc(a.pill)}</span>
        <h2 id="rx-title">${esc(a.title)}</h2>
        ${a.lines.map((l) => `<p>${l}</p>`).join("")}
        ${r.status !== "error" && a.cls !== "low" ? "<p>This is a screening from one photo, not a safety inspection.</p>" : ""}
        ${coverage ? `<p class="fx-coverage">${esc(coverage)}</p>` : ""}
        ${r.comparison && r.wires.length ? `<p class="fx-coverage fx-cmp-line">Compared with the ${esc(r.comparison.label)}: <span class="fx-cmp above">${r.comparison.counts.above} above</span> <span class="fx-cmp at">${r.comparison.counts.at} at (within resolution)</span> <span class="fx-cmp below">${r.comparison.counts.below} below</span> · ${r.comparison.counts.not_measured} not measured. Image sag ratio, not physical sag; a comparison, not an assessment.</p>` : ""}
      </div>
      <dl class="fx-counts" aria-label="Wire sections">
        ${COUNTS.map(([g, label]) => `<div><dt><i class="${g}"></i>${label}</dt><dd><button type="button" class="fx-count ${g}" data-filter-go="${g}" ${c[g] ? "" : "disabled"} aria-label="Show ${label.toLowerCase()} wire sections">${c[g]}</button></dd></div>`).join("")}
      </dl>
    </section>

    <div class="fx-work">
      <section class="fx-viewer" aria-label="Photo and detected wires">
        <div class="fx-toolbar" role="toolbar" aria-label="Photo view">
          <div class="fx-seg" role="radiogroup" aria-label="View">
            <button type="button" role="radio" data-view="photo" aria-checked="false">Original</button>
            <button type="button" role="radio" data-view="spans" aria-checked="true">Wires</button>
            <button type="button" role="radio" data-view="mask" aria-checked="false" ${hasMask ? "" : "disabled title=\"No model mask for this inspection\""}>Mask</button>
            <button type="button" role="radio" data-view="overlay" aria-checked="false" ${hasMask ? "" : "disabled title=\"No model mask for this inspection\""}>Both</button>
          </div>
          <label class="fx-opacity" id="opacity-wrap"><span>Overlay</span><input type="range" id="opacity" min="0" max="100" value="100" aria-label="Overlay opacity"><output for="opacity">100%</output></label>
          <button class="btn sm" type="button" data-layer="vegetation" aria-pressed="${hasVeg}" ${hasVeg ? "" : "disabled title=\"No vegetation detected\""}>${ICON.leaf}Vegetation</button>
          <div class="fx-layers">
            <button class="btn sm" type="button" id="layers-btn" aria-expanded="false" aria-controls="layers-menu">${ICON.layers}Layers</button>
            <div class="fx-menu" id="layers-menu" hidden>${LAYERS.map(([k, label, help]) => `<label title="${esc(help)}"><input type="checkbox" data-l="${k}" ${k === "wiremask" && !hasMask ? "disabled" : ""}><span>${label}${help ? `<small>${help}</small>` : ""}</span></label>`).join("")}</div>
          </div>
          <span class="fx-spacer"></span>
          <span class="fx-zoom">
            <button class="btn sm icon" type="button" data-zoom="out" aria-label="Zoom out" title="Zoom out">${ICON.zout}</button>
            <output id="zoom-pct" class="fx-num" aria-live="off">100%</output>
            <button class="btn sm icon" type="button" data-zoom="in" aria-label="Zoom in" title="Zoom in">${ICON.zin}</button>
            <button class="btn sm" type="button" data-zoom="fit" title="Fit the photo to the viewer">${ICON.fit}Fit</button>
            <button class="btn sm" type="button" data-zoom="reset" title="Fit, clear the selection and restore the default view">${ICON.reset}Reset</button>
          </span>
        </div>
        <div class="fx-canvas" style="--ar:${r.image.width} / ${r.image.height}"><div class="canvas-wrap" id="fs-canvas"></div></div>
        <div class="fx-legend" aria-label="Legend">
          ${legendSpans.map(([v, t]) => `<span data-legend="spans"><i style="--c:${css(v)}"></i>${t}</span>`).join("")}
          <span data-legend="inferred"><i class="dash"></i>Estimated, not detected</span>
          <span data-legend="mask" hidden><i style="--c:rgb(25,113,194)"></i>Power line pixels</span><span data-legend="mask" hidden><i style="--c:rgb(156,54,181)"></i>Telecom pixels</span><span data-legend="mask" hidden><i style="--c:rgb(232,89,12)"></i>Abandoned wire pixels</span>
          ${hasVeg ? `<span data-legend="veg"><i style="--c:${css("--cls-veg")}"></i>Vegetation</span>` : ""}
          <span class="fx-legend-hint">${anyScored ? "" : "Colour shows wire type, not severity · "}Scroll to zoom · drag to move · double-click to fit</span>
        </div>
      </section>

      <aside class="fx-side" aria-label="Wire sections">
        <section class="fx-detail" id="span-detail" aria-live="polite">${spanDetail(r, null, mode)}</section>
        <section class="fx-spans-panel">
          <div class="fx-table-head"><h2>Wire sections <span class="fx-num">${r.wires.length}</span></h2>
            <div class="fx-filters" role="group" aria-label="Show">
              <button type="button" data-filter="all" aria-pressed="true">All</button>
              ${FILTERS.filter(([g]) => c[g] || g !== "drop").map(([g, label]) => `<button type="button" data-filter="${g}" aria-pressed="false" ${c[g] ? "" : "disabled"}>${label} <b class="fx-num">${c[g]}</b></button>`).join("")}
            </div></div>
          ${anyScored ? "" : `<p class="fx-table-note">Measured sections are listed by image-derived sag ratio: a measurement in the photo, not a severity. No section was assessed against a sag reference, so none has passed or failed any check. They have not passed any check.</p>`}
          <div class="fx-scroll" id="fx-scroll">${tbl.html}<p class="fx-empty" id="filter-empty" hidden>No wire sections in this group.</p></div>
        </section>
      </aside>
    </div>

    <div class="fx-guide">
      <section class="fx-recs" aria-labelledby="recs-h"><h2 id="recs-h">What to do next</h2>
        <ol id="next-action">${recs.map((x) => `<li><span>${x}</span></li>`).join("")}</ol></section>
      <section class="fx-limits" id="limits" aria-labelledby="lim-h"><h2 id="lim-h">Photo limitations</h2>
        ${lims.length ? `<ul>${lims.map((l) => `<li data-limit="${l.key}"><p>${esc(l.text)}${l.ids.length ? ` <span class="fx-lim-ids">(${SECTION(l.ids)})</span>` : ""}</p>${l.help ? `<p class="fx-help">${esc(l.help)}</p>` : ""}</li>`).join("")}</ul>`
          : `<p class="fx-none">None found. Every detected wire section was measured from this photo.</p>`}
      </section>
    </div>
    ${diagHtml(r)}

    ${r.public ? "" : `<section class="fx-drawer" id="diag">
      <button class="fx-drawer-btn" type="button" aria-expanded="false" aria-controls="diag-body">
        <b>Technical diagnostics</b><span>For administrators and researchers: model, image quality, segmentation, statistics, timings, configuration</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="fx-drawer-body" id="diag-body" role="region" aria-label="Technical diagnostics"><div><div class="fx-drawer-inner">${diagnostics(r)}</div></div></div>
    </section>`}

    <section class="rx-notice" aria-label="Important Notice">
      <p class="rx-notice-short">Automated screening: results can be wrong or incomplete. Have them verified by a qualified professional.</p>
      <wf-notice variant="full"></wf-notice>
    </section>
    ${loc ? `<section class="rx-loc" aria-labelledby="rx-loc-h"><div class="rx-loc-info"><h2 id="rx-loc-h">Inspection location</h2>
        <dl><div><dt>Latitude</dt><dd class="mono">${loc.lat.toFixed(6)}</dd></div><div><dt>Longitude</dt><dd class="mono">${loc.lon.toFixed(6)}</dd></div>
          ${insp.barangay ? `<div><dt>Barangay</dt><dd>${esc(insp.barangay)}</dd></div>` : ""}
          <div><dt>Street</dt><dd id="rx-street" class="muted">Looking up…</dd></div></dl>
        <a class="btn" href="https://www.google.com/maps/search/?api=1&query=${loc.lat.toFixed(6)},${loc.lon.toFixed(6)}" target="_blank" rel="noopener">Open in Google Maps</a></div>
      <div class="fx-map"><div id="fs-map" role="region" aria-label="Location of this inspection"></div></div></section>` : ""}
    ${r.reviews?.length ? `<p class="fx-muted">Reviewed by ${esc(r.reviews.at(-1).by)}</p>` : ""}`;

  const root = stages.result;
  const table = root.querySelector(".fx-spans");
  const detail = root.querySelector("#span-detail");

  // ---------------- viewer
  viewer?.destroy();
  viewer = new OverlayViewer($("#fs-canvas"), { onSelect: (i) => mark(i, true) });
  window.wfViewer = viewer; // read-only handle for automated UI checks (scripts/e2e_check.py)
  if (mode === "class") viewer.setMode("class");
  const zoomOut = root.querySelector("#zoom-pct");
  $("#fs-canvas").addEventListener("zoom", (e) => { zoomOut.textContent = `${Math.round(e.detail * 100)}%`; });
  viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => {
    if (first) { viewer.selected = first.index; viewer.draw(); mark(first.index, false); }
    if (hasMask) viewer.setWireMask(r.wire_mask.url);
    viewer.setPoles(r.scene?.poles_detected || []);
  }).catch(() => toast.error("Photo could not be displayed"));

  const mark = (i, scroll) => {
    table?.querySelectorAll("tr[data-wire]").forEach((tr) => {
      const on = Number(tr.dataset.wire) === i;
      tr.setAttribute("aria-selected", String(on));
      if (on && scroll && !tr.hidden) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    detail.innerHTML = spanDetail(r, i == null ? null : r.wires.find((w) => w.index === i), mode);
  };
  const select = (i, toggle = true) => {
    const next = toggle && viewer.selected === i ? null : i;
    viewer.select(next); mark(next, true);
  };
  table?.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-wire]"); if (tr) select(Number(tr.dataset.wire)); });
  table?.addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-wire]");
    if (!tr) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(Number(tr.dataset.wire)); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const rows = [...table.querySelectorAll("tr[data-wire]:not([hidden])")];
      const n = rows[rows.indexOf(tr) + (e.key === "ArrowDown" ? 1 : -1)];
      if (n) { n.focus(); select(Number(n.dataset.wire), false); }
    }
  });
  table?.addEventListener("pointerover", (e) => { const tr = e.target.closest("tr[data-wire]"); viewer.hover = tr ? Number(tr.dataset.wire) : null; viewer.draw(); });
  table?.addEventListener("pointerleave", () => { viewer.hover = null; viewer.draw(); });
  root.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-goto]");
    if (b) {
      select(Number(b.dataset.goto), false);
      root.querySelector(".fx-viewer").scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  });

  // ---------------- view presets, layers, opacity
  const seg = root.querySelector(".fx-seg");
  const opacity = root.querySelector("#opacity"), opWrap = root.querySelector("#opacity-wrap");
  const menu = root.querySelector("#layers-menu"), menuBtn = root.querySelector("#layers-btn");
  const legend = (k, on) => root.querySelectorAll(`[data-legend="${k}"]`).forEach((x) => { x.hidden = !on; });
  const syncUi = () => {
    const L = viewer.layers;
    menu.querySelectorAll("[data-l]").forEach((cb) => { cb.checked = !!L[cb.dataset.l] && L.overlays; });
    legend("spans", L.overlays && L.masks); legend("inferred", L.overlays && L.inferred && L.masks);
    legend("mask", L.overlays && L.wiremask); legend("veg", L.overlays && L.vegetation);
    opWrap.toggleAttribute("data-off", !L.overlays);
  };
  const setView = (name) => {
    seg.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.view === name)));
    const p = PRESETS[name];
    for (const [k] of LAYERS) viewer.layers[k] = !!p[k];
    viewer.layers.overlays = name !== "photo";
    viewer.draw(); syncUi();
  };
  seg.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b && !b.disabled) setView(b.dataset.view); });
  seg.addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight"].includes(e.key)) return;
    const opts = [...seg.querySelectorAll("[data-view]:not(:disabled)")];
    const i = opts.findIndex((b) => b.getAttribute("aria-checked") === "true");
    const next = opts[(i + (e.key === "ArrowRight" ? 1 : opts.length - 1)) % opts.length];
    setView(next.dataset.view); next.focus();
  });
  const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); };
  menuBtn.addEventListener("click", () => {
    const open = menu.hidden; menu.hidden = !open; menuBtn.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest(".fx-layers")) closeMenu(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { closeMenu(); menuBtn.focus(); } });
  menu.addEventListener("change", (e) => {
    const cb = e.target.closest("[data-l]"); if (!cb) return;
    viewer.layers.overlays = true;
    viewer.layers[cb.dataset.l] = cb.checked;
    // a hand-picked combination is no preset: un-check the view control so it never claims a state it is not in
    const match = Object.entries(PRESETS).find(([n, p]) => n !== "photo" && LAYERS.every(([k]) => !!p[k] === !!viewer.layers[k]));
    seg.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", String(!!match && b.dataset.view === match[0])));
    viewer.draw(); syncUi();
  });
  opacity.addEventListener("input", () => {
    viewer.setOpacity(opacity.value / 100);
    opWrap.querySelector("output").textContent = `${opacity.value}%`;
  });
  root.querySelector('[data-layer="vegetation"]')?.addEventListener("click", (e) => {
    const b = e.currentTarget, on = b.getAttribute("aria-pressed") !== "true";
    b.setAttribute("aria-pressed", String(on)); viewer.set("vegetation", on); syncUi();
  });
  root.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => {
    ({ in: () => viewer.zoomIn(), out: () => viewer.zoomOut(), fit: () => viewer.reset(),
      reset: () => {
        viewer.reset(); select(null, false);
        opacity.value = 100; viewer.setOpacity(1); opWrap.querySelector("output").textContent = "100%";
        const vb = root.querySelector('[data-layer="vegetation"]');
        if (vb && !vb.disabled) { vb.setAttribute("aria-pressed", "true"); viewer.layers.vegetation = true; }
        setView("spans");
      } })[b.dataset.zoom]();
  }));
  viewer.layers.vegetation = hasVeg;
  setView("spans");

  // ---------------- filters
  const filter = (f) => {
    root.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.filter === f)));
    let shown = 0;
    table?.querySelectorAll("tr[data-group]").forEach((tr) => { tr.hidden = f !== "all" && tr.dataset.group !== f; shown += !tr.hidden; });
    root.querySelector("#filter-empty").hidden = !table || shown > 0;
  };
  root.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => filter(b.dataset.filter)));
  root.querySelectorAll("[data-filter-go]").forEach((b) => b.addEventListener("click", () => {
    filter(b.dataset.filterGo);
    const f1 = table?.querySelector("tr[data-wire]:not([hidden])");
    if (f1) select(Number(f1.dataset.wire), false);
    root.querySelector(".fx-spans-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));

  // ---------------- diagnostics drawer (staff only)
  const drawer = root.querySelector("#diag"), dbtn = drawer?.querySelector(".fx-drawer-btn");
  dbtn?.addEventListener("click", () => {
    const open = !drawer.hasAttribute("data-open");
    drawer.toggleAttribute("data-open", open);
    dbtn.setAttribute("aria-expanded", String(open));
  });
  $("#again").addEventListener("click", reset);
  $("#guard-retake")?.addEventListener("click", async () => {
    try { await api(`/api/analysis/${r.id}/guardrail`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "retake" }) }); } catch { /* never blocks */ }
    startRetake(r);
  });

  // ---------------- map
  map?.remove(); map = null;
  if (loc && window.L) {
    map = L.map("fs-map", { zoomControl: false, attributionControl: true, scrollWheelZoom: false }).setView([loc.lat, loc.lon], 17);
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>' }).addTo(map);
    L.marker([loc.lat, loc.lon], { icon: L.divIcon({ className: "", iconSize: [20, 20], iconAnchor: [10, 10],
      html: `<div class="pin" style="--pc:${css({ low: "--risk-low", medium: "--risk-med", high: "--risk-high" }[VERDICT_CLASS[r.hazard?.verdict]] || "--risk-none")}"></div>` }),
      title: insp.name || "Inspection" }).addTo(map);
  }
  if (loc) { // street name from OpenStreetMap, labelled as such; nothing is shown when there is none
    fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=17&lat=${loc.lat}&lon=${loc.lon}`, { headers: { Accept: "application/json" } })
      .then((x) => (x.ok ? x.json() : null)).then((d) => {
        const el = $("#rx-street"); if (!el) return;
        const road = d?.address?.road;
        el.textContent = road ? `${road} (nearest street, OpenStreetMap)` : "Not available";
      }).catch(() => { const el = $("#rx-street"); if (el) el.textContent = "Not available"; });
  }
}


function reset() {
  form.reset(); setFile(null); coords = null;
  $("#locate").setAttribute("aria-pressed", "false"); $("#locate span").textContent = "Use my location"; locateStatus("");
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
