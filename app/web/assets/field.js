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
import { css, group, plural, pc, SECTION, CONDITION, SEVERITY, spanId, skipReason, SCORE_HELP, sagFactors, finding, limitations, assessment, recommendations, MEASURED_ONLY, measuredText } from "./findings.js?v=2.23";
// ------------------------------------------------------------------ result view (v2.20: written for the public)
// Reading order: what was found (headline from the record), the photo with only the problem wires, the selected
// wire against its reference limit, the wire list (needs attention first), what to do, photo limitations, notice.
// Scores, fit parameters and model internals are not shown here (System / technical diagnostics in the admin portal).
const ICON = {
  pdf: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7zM14 3v5h5M12 11v6M9 14l3 3 3-3"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
  zin: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2M11 8v6M8 11h6"/></svg>`,
  zout: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6"/><path d="m20 20-4.2-4.2M8 11h6"/></svg>`,
  fit: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/></svg>`,
};
const BAND = SEVERITY; // v2.21: Critical / Minor sag / Within reference / Not assessed, everywhere
const isPower = (w) => w.class === "power-distribution-lines" && w.class_status !== "uncertain";
const isTelecom = (w) => w.class === "telecom-cable" && w.class_status !== "uncertain";
const pp = (x) => `${(x * 100).toFixed(1)}%`;

/** The headline and its supporting lines, from the stored verdict and the stored wires only. */
function headline(r) {
  const v = r.hazard?.verdict || "";
  const scored = r.wires.filter((w) => w.status === "scored");
  const flagged = scored.filter((w) => w.level === "high" || w.level === "medium");
  const high = scored.filter((w) => w.level === "high");
  const tel = flagged.filter(isTelecom).length, pow = flagged.filter(isPower).length;
  const powScored = scored.filter(isPower).length;
  const n = (k, one, many) => `${k} ${k === 1 ? one : many}`;
  const lines = [];
  if (flagged.length) {
    const parts = [tel && `${n(tel, "appears", "appear")} to be ${tel === 1 ? "a telecommunications cable" : "telecommunications cables"}`,
      pow && `${n(pow, "appears", "appear")} to be ${pow === 1 ? "a power line" : "power lines"}`].filter(Boolean);
    if (parts.length) lines.push(`Of the ${n(flagged.length, "flagged wire", "flagged wires")}, ${parts.join(" and ")}.`);
    if (!pow && powScored) lines.push(`None of the ${n(powScored, "power line", "power lines")} that could be assessed was flagged. This does not mean the location is electrically safe.`);
  }
  if (r.status === "error") return { cls: "none", title: "This photo could not be processed", lines: ["Try again. If it fails again, take a new photo or contact your administrator."] };
  if (v.startsWith("INCONCLUSIVE")) { // v2.21: say why (spans off the photo, no reference, poor photo), never "safe"
    const why = (r.hazard?.reasons || []).find((x) => x.startsWith("INCONCLUSIVE"));
    const noRef = r.wires.some((w) => MEASURED_ONLY.has(w.reason_code));
    const d = r.diagnosis;
    if (d?.why && d.counts.measured + d.counts.measured_partial > 0) { // S5: what was measured, and why no verdict
      const hist = why && /withdrawn/.test(why) ? [why.replace(/^INCONCLUSIVE - RETAKE PHOTO:\s*/, "").replace(/\.*$/, ".")] : [];
      return { cls: "none", title: "Inspection summary: assessment incomplete", lines: [...hist, d.why,
        d.retake_helps ? "A new photo can complete the measurement of wires that run off the photo or could not be traced, but it cannot supply a sag reference. Arrange a field inspection by a qualified person for any concern."
          : "A new photo will not complete the assessment. Arrange a field inspection by a qualified person for any concern."] };
    }
    return { cls: "none", title: "Inspection summary: assessment incomplete",
      lines: [why ? why.replace(/^INCONCLUSIVE - RETAKE PHOTO:\s*/, "").replace(/^./, (c) => c.toUpperCase()) + "." : "The photo is not clear enough to assess.",
        noRef ? "This is not a low-risk result. No validated sag reference exists for these wires, so a retake will not complete the assessment; the sag ratios shown are image measurements only. Arrange a field inspection by a qualified person for any concern."
          : "This is not a low-risk result. Retake the photo with both poles in the frame, or arrange a field check."] };
  }
  if (!scored.length) return { cls: "none", title: "Inspection summary: no span could be assessed", lines: ["Take a new photo using the tip above, or arrange a field check."] };
  if (v === "AT RISK - HIGH" || v === "AT RISK - MODERATE") {
    const k = v === "AT RISK - HIGH" ? (high.length || flagged.length) : flagged.length;
    const what = v === "AT RISK - HIGH" ? n(k, "low-hanging wire", "low-hanging wires") : n(k, "wire with some sag", "wires with some sag");
    const veg = (r.hazard?.factors?.vegetation || []).some((x) => x.gap_px === 0 && x.class === "power-distribution-lines");
    if (veg) lines.push("Vegetation appears to touch a power line.");
    void what;
    return { cls: v === "AT RISK - HIGH" ? "high" : "medium", title: summaryTitle(r), lines };
  }
  if (flagged.length) lines.unshift(`${n(flagged.length, "wire reads", "wires read")} as sagging on ${flagged.length === 1 ? "its" : "their"} own. A reading like this can come from the photo rather than the wire, so the pole as a whole was not flagged, but ${flagged.length === 1 ? "it is" : "they are"} worth a look.`);
  return { cls: "low", title: summaryTitle(r), lines: [...lines, "This is a screening of one photo, not a safety inspection."] };
}

/** v2.21 status header: counts of the stored wire levels, the same numbers as the tabs and the PDF. */
function summaryTitle(r) {
  const c = r.wires.filter((w) => w.level === "high").length, m = r.wires.filter((w) => w.level === "medium").length;
  if (c) return `Inspection summary: ${c} ${c === 1 ? "wire needs" : "wires need"} attention`;
  if (m) return `Inspection summary: no critical wires, ${m} with minor sag`;
  return "Inspection summary: no wire flagged in this photo";
}

/** One plain sentence per wire for the list (never "normal" for a wire that was not assessed). */
function plainFinding(w) {
  const edge = (w.flags || []).includes("truncated");
  if (w.status === "scored") {
    const s = pp(w.sag_ratio);
    const over = w.delta_lim != null && w.sag_ratio >= w.delta_lim;
    const base = { high: over ? `Exceeds reference limit: ${s} sag` : `Near reference limit: ${s} sag`, medium: `Minor sag: ${s}`, low: `Within normal range: ${s}` }[w.level];
    return base + (edge ? "; partly outside the photo" : "");
  }
  if (w.status === "service_drop") return "Drop wire to a building; not checked for sag";
  if (w.status === "not_assessed") return { endpoint_unavailable: w.sag_ratio != null ? `Not assessed: runs off the photo; visible part measured ${measuredText(w)}` : "Not assessed: endpoint unavailable (runs off the photo)",
    class_uncertain: w.sag_ratio != null ? `Measured, not assessed (type uncertain): image-derived sag ratio ${measuredText(w)}` : "Not assessed: uncertain wire type", no_reference_for_class: "Not assessed: no sag reference for power lines",
    no_validated_reference: w.sag_ratio != null ? `Measured, not assessed: image-derived sag ratio ${measuredText(w)}` : "Not assessed: no validated sag reference",
    withdrawn_reference: w.sag_ratio != null ? `Measured, not assessed: image-derived sag ratio ${measuredText(w)} (earlier reference withdrawn)` : "Not assessed: earlier reference withdrawn" }[w.reason_code] || "Not assessed";
  return { chord_below_precision_gate: "Unable to assess: too small or far away", too_short: "Unable to assess: too small or far away",
    residual_too_large: "Unable to assess due to overlapping wires", non_positive_a: "Unable to assess due to overlapping wires",
    empty_mask: "Unable to assess: too faint" }[w.reason_code] || (edge ? "Partially outside the photograph" : "Unable to assess from this photo");
}

function observation(w) {
  if (w.status !== "scored") return plainFinding(w) + ".";
  const t = isTelecom(w) ? "This cable" : isPower(w) ? "This power line" : "This wire";
  const veg = w.veg_clearance?.gap_px === 0 ? " Vegetation appears to touch it." : "";
  const over = w.delta_lim != null && w.sag_ratio >= w.delta_lim;
  // "Needs attention" starts below the reference (the screening flags sag from 60% of it), so say which it is
  return ({ high: over ? `${t} sags beyond the configured reference.` : `${t} sags close to the configured reference.`,
    medium: `${t} shows some sag.`, low: `${t} sags within the normal range.` }[w.level] || "") + veg;
}

/** Selected wire: measured sag against the configured reference, as numbers and as a meter. */
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

/** S12 meters (register D-28). The three-zone sag meter for a wire covered by the configured reference. Every boundary
 * comes from the configuration through the server (comparison.zones, one source): green below the lower boundary, amber
 * the reference-tolerance region, red above the upper boundary, each widened by the wire's own measurement uncertainty.
 * No engineering tolerance is validated, so both boundaries are the 3 % reference and the amber region is the
 * uncertainty alone; it is drawn at its true width with a hairline minimum so it stays visible. The scale is fixed by
 * the configuration (meter_scale_max) and a ratio beyond it is drawn as overflow at the end of the track. */
const ZONE = { below: "Below reference", at: "Reference-tolerance region", above: "Above reference" };
const ZONE_MARK = { below: "↓", at: "≈", above: "↑" };
const ZDOT = { below: "low", at: "medium", above: "high" };
const keyAt = (v) => (v < 22 ? "at-start" : v > 78 ? "at-end" : "");
const scaleOf = (pc) => pc?.meter_scale_max || 0.1;
const onScale = (max) => (q) => Math.min(100, Math.max(0, (q / max) * 100));
const pp2 = (x) => `${(x * 100).toFixed(2)}%`;
const resBand = (w, v) => {
  const res = w.sag_ratio_resolution || 0;
  if (!res) return "";
  const lo = v(Math.max(0, w.sag_ratio - res)), hi = v(w.sag_ratio + res);
  return `<i class="rx-meter-res" style="left:${lo}%;width:${Math.max(hi - lo, 0.6)}%"></i>`;
};

/** Why no reference applies to this wire, from the server's states (the same words as the admin sheet and the CSV). */
function refWhy(w) {
  const k = w.states?.reference_unavailable_reason
    || (w.class_status === "uncertain" ? "type_uncertain" : w.class === "telecom-cable" ? "no_reference_for_telecom" : w.class === "abandoned-wire" ? "no_reference_for_abandoned" : "no_reference_for_class");
  return { no_reference_for_telecom: "No validated sag reference exists for telecom / fibre cable; manufacturer data for fibre cable allow more than 3% sag under load.",
    no_reference_for_abandoned: "An abandoned wire's type and owner are not established, so no sag reference applies.",
    type_uncertain: "The photo does not show clearly whether this is a power line or a telecom cable, so no reference is assumed.",
    no_reference_for_class: "No validated sag reference exists for this wire type.",
    drop_wire: "Drop wire to a building: not checked for sag.",
    reference_disabled: "No sag reference is configured." }[k] || "No applicable sag reference.";
}

function meterRef(w, pc) {
  const c = w.comparison, z = c.zones, part = c.state === "measured_partial";
  const max = scaleOf(pc), v = onScale(max), at = v(w.sag_ratio), over = w.sag_ratio > max;
  const a = v(z.at_from), b = Math.min(100, Math.max(v(z.above_from), a + 0.8));
  const validated = pc?.classification_enabled;
  return `<figure class="rx-meter cmp ${c.position}${over ? " overflow" : ""}" role="img" aria-label="Image sag ratio ${measuredText(w)}${part ? ", visible part only" : ""}: ${ZONE[c.position].toLowerCase()} (${esc(c.label)})${c.uncertainty_overlaps ? "; the uncertainty overlaps the reference" : ""}">
    <div class="rx-meter-read"><b class="n">${measuredText(w)}</b>${part ? "<small>visible part</small>" : ""}
      <span class="rx-zone ${c.position}"><i aria-hidden="true">${ZONE_MARK[c.position]}</i>${ZONE[c.position]}</span>
      ${c.uncertainty_overlaps ? `<span class="rx-amb" title="The ± uncertainty band crosses a zone boundary, so the photo cannot place this wire on one side of it">Uncertainty overlaps the reference</span>` : ""}</div>
    <div class="rx-meter-track"><i class="z z-low" style="left:0;width:${a}%"></i><i class="z z-medium" style="left:${a}%;width:${b - a}%"></i><i class="z z-high" style="left:${b}%;right:0"></i>
      ${resBand(w, v)}<i class="rx-meter-ref" style="left:${v(c.reference)}%"></i><i class="rx-meter-dot ${ZDOT[c.position]}${part ? " partial" : ""}" style="left:${at}%" title="This wire: ${measuredText(w)}"></i>${over ? `<i class="rx-meter-over" aria-hidden="true"></i>` : ""}</div>
    <div class="rx-meter-key"><span class="at-start" style="left:0">0%</span><span class="ref-k ${keyAt(v(c.reference))}" style="left:${v(c.reference)}%">Reference ${pp(c.reference)}</span><span class="at-end" style="left:100%">${pp(max)}${over ? "+" : ""}</span></div>
    <figcaption class="rx-zones"><span><i class="z-low"></i>${ZONE.below} <small>under ${pp2(z.at_from)}</small></span><span><i class="z-medium"></i>${ZONE.at} <small>${pp2(z.at_from)} to ${pp2(z.above_from)}</small></span><span><i class="z-high"></i>${ZONE.above} <small>over ${pp2(z.above_from)}</small></span>
      <span class="rx-zones-note">${validated ? "Validated reference: the zones are severity classes." : `Provisional comparison: the zones are not severity classes.`}${over ? ` This wire is beyond the ${pp(max)} scale.` : ""}</span></figcaption></figure>`;
}

/** D-27: a measured wire with no applicable reference keeps the colourful meter in its wire-type colour, with its raw
 * image sag ratio on the same configured scale, and says that no comparison applies. No zones, no reference tick. */
function meterType(w, pc) {
  const part = w.reason_code === "endpoint_unavailable";
  const max = scaleOf(pc), v = onScale(max), at = v(w.sag_ratio), over = w.sag_ratio > max;
  return `<figure class="rx-meter type${over ? " overflow" : ""}" style="--wc:${wireColor(w, "class")}" role="img" aria-label="Image sag ratio ${measuredText(w)}${part ? ", visible part only" : ""}; no applicable comparison">
    <div class="rx-meter-read"><b class="n">${measuredText(w)}</b>${part ? "<small>visible part</small>" : ""}<span class="rx-zone none"><i aria-hidden="true">○</i>No applicable comparison</span></div>
    <div class="rx-meter-track"><i class="rx-meter-fill" style="width:${at}%"></i>${resBand(w, v)}<i class="rx-meter-dot${part ? " partial" : ""}" style="left:${at}%" title="This wire: ${measuredText(w)}"></i>${over ? `<i class="rx-meter-over" aria-hidden="true"></i>` : ""}</div>
    <div class="rx-meter-key"><span class="at-start" style="left:0">0%</span><span class="at-end" style="left:100%">${pp(max)}${over ? "+" : ""}</span></div>
    <figcaption class="rx-zones"><span class="rx-zones-note">${esc(refWhy(w))} The colour shows the wire type.</span></figcaption></figure>`;
}

/** The comparison counts in one line, or that no wire in the photo is covered by the reference. */
function cmpLine(r) {
  const c = r.comparison;
  if (!c || !r.wires.length) return "";
  const n = c.counts;
  if (!Object.values(n).some(Boolean)) return `<p class="rx-cmp-line">No wire in this photo is covered by the ${esc(c.label)}.</p>`;
  return `<p class="rx-cmp-line"><span>Compared with the ${esc(c.label)}:</span> <span class="rx-zone above"><i aria-hidden="true">↑</i>${n.above} above</span> <span class="rx-zone at"><i aria-hidden="true">≈</i>${n.at} in the tolerance region</span> <span class="rx-zone below"><i aria-hidden="true">↓</i>${n.below} below</span>${n.not_measured ? ` <span class="rx-zone none">${n.not_measured} not measured</span>` : ""}</p>`;
}

function meterEmpty() {
  return `<div class="rx-meter empty" role="img" aria-label="Not measured: this wire has no image sag ratio.">
    <div class="rx-meter-track"></div>
    <div class="rx-meter-key"><span class="ref at-start" style="left:0">Not measured: no sag ratio from this photo</span></div></div>`;
}

/** S5: the wire's badge. A severity only when one was assessed; otherwise what was measured. */
function bandOf(w) {
  if (w.status === "scored" && w.level) return BAND[w.level];
  if (w.status === "service_drop") return "Not checked";
  if (!isMeasured(w)) return "Not measured";
  return w.reason_code === "endpoint_unavailable" ? "Visible part measured" : "Measured";
}

// S12: coverage and assessment are separate facts with separate badges (wire_states on the server)
const COVER = { full_span: "Full Span", edge_clipped: "Edge Clipped", unmeasured: "Unmeasured", drop: "Drop Wire" };
function coverageOf(w) {
  if (w.status === "service_drop") return "drop";
  return w.states?.coverage || (isMeasured(w) ? (w.reason_code === "endpoint_unavailable" ? "edge_clipped" : "full_span") : "unmeasured");
}
const withinRes = (w) => w.states ? !!w.states.within_resolution
  : isMeasured(w) && !!w.sag_ratio_resolution && w.sag_ratio < w.sag_ratio_resolution;
/** The assessment status, in one phrase: never "safe", and never a severity unless one was assessed. */
function assessmentOf(w) {
  if (w.status === "scored" && w.level) return { k: "assessed", t: `Assessed: ${BAND[w.level]}` };
  if (w.status === "service_drop") return { k: "na", t: "Not Checked (Drop Wire)" };
  if (!isMeasured(w)) return { k: "un", t: "Unassessed (Not Measured)" };
  if (w.comparison?.position) return { k: "un", t: "Unassessed (Provisional Comparison Only)" };
  return { k: "un", t: "Unassessed (No Validated Reference)" };
}
const chordPx = (w) => { const e = w.fit?.endpoints; return e ? Math.hypot(e[1][0] - e[0][0], e[1][1] - e[0][1]) : null; };
const pill = (cls, text, title = "") => `<span class="rx-pill ${cls}"${title ? ` title="${esc(title)}"` : ""}>${esc(text)}</span>`;
const covPill = (w) => pill(`cov ${coverageOf(w)}`, COVER[coverageOf(w)]);
/** The inventory's Status cell: assessment, then the reference fact, then "within resolution" (text, not colour alone). */
function statusPills(w) {
  const a = assessmentOf(w), out = [pill(`as ${a.k}`, a.k === "assessed" ? a.t : a.k === "na" ? "Not Checked" : "Unassessed")];
  if (w.comparison?.position) out.push(`<span class="rx-zone ${w.comparison.position} sm"><i aria-hidden="true">${ZONE_MARK[w.comparison.position]}</i>${ZONE[w.comparison.position]}</span>`);
  else if (isMeasured(w) && w.status !== "scored") out.push(pill("ref", "Reference Unavailable", refWhy(w)));
  if (withinRes(w)) out.push(pill("res", "Within Resolution", "Straight to within the 1 pixel resolution: no sag is resolvable at this image scale. This is not evidence that the wire is safe."));
  return out.join("");
}

/** Measurement details (folded): how this wire's number was produced, from the stored trace and fit only. */
function measureDetails(w) {
  const p = w.provenance, ch = chordPx(w), res = w.sag_ratio_resolution, part = w.reason_code === "endpoint_unavailable";
  const share = p?.centerline_inferred_share; // share of the centreline LENGTH, as in the fit diagnostics line
  const gaps = p?.inferred_segments?.length || 0;
  const L = Object.fromEntries((w.diagnosis?.lines || []).map((l) => [l.key, l.text]));
  const na = `<span class="muted">Not available</span>`;
  const rows = isMeasured(w) ? [
    ["Traced fraction", share != null ? `${Math.round((1 - share) * 100)}% of the centreline length follows the wire mask` : na],
    ["Interpolation", share != null ? (share > 0 ? `${Math.round(share * 100)}% of the length bridged across ${gaps} gap${gaps === 1 ? "" : "s"} in the mask` : "None: traced without gaps") : na],
    ["Chord", ch ? `${Math.round(ch)} px between the ${part ? "visible ends (the span runs off the photo)" : "traced ends"}` : na],
    ["Sag calculation", "Largest perpendicular distance from the fitted curve to the chord, divided by the chord length, both in image pixels"],
    ["Uncertainty", res ? `±${(res * 100).toFixed(2)} percentage points (1 px over the chord)${withinRes(w) ? "; the ratio is smaller, so no sag is resolvable" : ""}` : na],
    ["Pixel resolution", ch ? `1 px = ${(100 / ch).toFixed(3)}% of the chord` : na],
    ["Fit diagnostics", L.measurement || L.trace ? esc([L.trace, L.measurement].filter(Boolean).join(". ")) + "." : na],
  ] : [["Reason", esc(plainFinding(w))], ...(L.trace ? [["Trace", esc(L.trace) + "."]] : [])];
  return `<details class="rx-md"><summary>Measurement details</summary><dl>${rows.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
    ${w.diagnosis?.needed?.length ? `<p class="rx-note"><b>To complete the assessment:</b> ${esc(w.diagnosis.needed.join(". "))}.</p>` : ""}</details>`;
}

/** The selected-wire inspector. Rows with nothing to say are left out; a wire with no applicable reference gets one
 * status with its type-specific reason instead of empty Reference / Difference rows. */
function wireCard(w, pc) {
  if (!w) return `<p class="rx-pick">Select a numbered wire on the photo or in the inventory to see its measurement.</p>`;
  const g = group(w), a = assessmentOf(w), c = w.comparison;
  const scored = w.status === "scored" && w.sag_ratio != null && w.delta_lim != null;
  const ch = chordPx(w);
  const row = (k, v, cls = "") => `<div><dt>${k}</dt><dd class="${cls}">${v}</dd></div>`;
  const signed = (d) => `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(d).toFixed(1)} pp`;
  return `<header class="rx-wire-head"><span class="rx-id" style="--wc:${wireColor(w, "class")}">${w.index}</span>
      <div><b>${esc(classLabel(w))}</b><span class="rx-span">${spanId(w.index)}</span></div></header>
    <p class="rx-pills">${covPill(w)}${withinRes(w) ? pill("res", "Within Resolution") : ""}</p>
    <dl class="rx-meas">
      ${isMeasured(w) ? row(`Image sag ratio${w.reason_code === "endpoint_unavailable" ? " (visible part)" : ""}`, measuredText(w)) : ""}
      ${ch ? row("Chord", `${Math.round(ch)} px`) : ""}
      ${scored ? row("Reference limit", pp(w.delta_lim)) + row("Difference", signed((w.sag_ratio - w.delta_lim) * 100), w.sag_ratio > w.delta_lim ? "over" : "") : ""}
      ${!scored && c?.position ? row("Reference", `${pp(c.reference)} <small>(${esc(c.status)})</small>`) + row("Difference", signed(c.difference_pp), c.position === "above" ? "over" : "") : ""}
      ${row("Assessment status", esc(a.t), "txt")}
    </dl>
    ${scored ? meter(w, g) : isMeasured(w) ? (c?.position ? meterRef(w, pc) : meterType(w, pc)) : w.status === "service_drop" ? "" : meterEmpty()}
    ${measureDetails(w)}`;
}

/** S12: the browse filter "Notable sag". The approved UI defines it at 2 % of the chord; it narrows the list only and is
 * not an engineering threshold (no wire gets a condition from it). */
const BROWSE_SAG = 0.02;
const FILTERS = {
  notable: { t: `Notable Sag > ${BROWSE_SAG * 100}%`, title: `Browsing filter only: image sag ratio above ${BROWSE_SAG * 100}%. Not an engineering threshold.`, f: (w) => isMeasured(w) && w.sag_ratio > BROWSE_SAG },
  full: { t: "Full Span Only", f: (w) => coverageOf(w) === "full_span", not: "clipped" },
  clipped: { t: "Clipped at Edge", f: (w) => coverageOf(w) === "edge_clipped", not: "full" },
};

function renderResult(r) {
  const h = headline(r);
  const pc = r.comparison;
  const loc = r.pole.location;
  const insp = r.inspection || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");
  const when = new Date(r.created_at);
  const groups = { high: [], medium: [], low: [], none: [] };
  r.wires.forEach((w) => groups[group(w)].push(w));
  const attention = [...groups.high, ...groups.medium].sort((a, b) => (b.sag_ratio ?? -1) - (a.sag_ratio ?? -1));
  const assessed = r.wires.filter((w) => w.status === "scored").length;
  // S5: with no assessed span the measured wires are the focus, drawn with their fitted curve, chord and sag point
  const measuredWs = r.wires.filter(isMeasured).sort((a, b) => b.sag_ratio - a.sag_ratio || a.index - b.index);
  const unmeasured = r.wires.filter((w) => !isMeasured(w)).sort((a, b) => a.index - b.index);
  const diagMode = !assessed && measuredWs.length > 0;
  const focus = attention.length ? attention : diagMode ? measuredWs : [];
  const dc = r.diagnosis?.counts;
  const hasMask = !!r.wire_mask, hasVeg = (r.vegetation || []).length > 0;
  const lims = limitations(r);
  const limKeys = new Set(lims.map((l) => l.key));
  const LIMS = [
    ["overlap", "Overlapping wires can prevent reliable separation."],
    ["edge", "Wires extending beyond the photograph may not be fully measurable."],
    ["small", "Distant or obscured wires may not have sufficient visual detail."],
  ].filter(([k]) => limKeys.has(k) || (k === "small" && (limKeys.has("other") || limKeys.has("quality") || limKeys.has("estimated"))));
  const clipped = dc ? dc.measured_partial : r.wires.filter((w) => w.reason_code === "endpoint_unavailable").length;
  const notMeas = dc ? dc.not_measured : unmeasured.filter((w) => w.status !== "service_drop").length;
  const kpi = dc ? [["Measured", dc.measured, "full span"], ["Visible Part Only", dc.measured_partial, "clipped at the edge"],
    ["Not Measured", dc.not_measured, dc.not_applicable ? `+ ${dc.not_applicable} drop wire${dc.not_applicable === 1 ? "" : "s"}` : "no ratio"], ["Assessed", dc.assessed, "for severity"]]
    : [["Measured", measuredWs.length, "image ratio"], ["Not Measured", unmeasured.length, "no ratio"], ["Assessed", assessed, "for severity"]];
  const bannerKey = `wf-banner-${r.id}`;
  let bannerHidden = false;
  try { bannerHidden = sessionStorage.getItem(bannerKey) === "1"; } catch { /* storage unavailable: show it */ }
  const pdfHref = `/api/reports/${r.id}/pdf`;

  stages.result.innerHTML = `
    <div class="rx">
    <header class="rx-head">
      <div><h1>${esc(insp.name || "Pole inspection")}</h1>
        <p>${[place, `${when.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })}, ${when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`].filter(Boolean).map(esc).join(" · ")}</p></div>
      <div class="rx-actions">
        ${r.status !== "error" ? `<a class="btn lg" href="${pdfHref}" target="_blank" rel="noopener" id="pdf">${ICON.pdf}Download PDF Report</a>` : ""}
        <button class="btn primary lg" id="again" type="button">${ICON.plus}New Inspection</button>
      </div>
    </header>
    ${guardBanner(r)}
    ${bannerHidden ? "" : `<div class="rx-banner${clipped + notMeas ? " warn" : ""}" id="rx-banner" role="status">
      <p>${clipped + notMeas ? `<b>${[clipped && `${clipped} of ${r.wires.length} wires run off the photo edge, so only their visible part is measured`, notMeas && `${notMeas} could not be measured`].filter(Boolean).join("; ")}.</b> ` : ""}<b>Tip:</b> stand where both supporting poles and the full span are in the frame, with the wires against open sky.</p>
      <button type="button" class="rx-x" id="banner-x" aria-label="Dismiss this tip">×</button></div>`}

    <section class="rx-summary ${h.cls}" aria-labelledby="rx-title">
      <h2 id="rx-title">${esc(h.title)}</h2>
      ${(assessed ? h.lines : r.summary_lines || []).map((l) => `<p>${esc(l)}</p>`).join("")}
      <dl class="rx-kpi" style="--n:${kpi.length}">${kpi.map(([t, v, s]) => `<div><dt>${t}</dt><dd class="n">${v}</dd><dd class="sub">${esc(s)}</dd></div>`).join("")}</dl>
      ${cmpLine(r)}
    </section>

    <ol class="rx-do" aria-label="What to do">
      <li><span class="rx-do-n">1</span><div><b>Keep Distance</b><p>Never touch, pull or move a low wire. Treat every conductor as energised.</p></div></li>
      <li><span class="rx-do-n">2</span><div><b>Download / Share Report</b><p>For the barangay office, the electric utility or the telecom provider.</p></div>
        <span class="rx-do-acts">${r.status !== "error" ? `<a class="btn sm" href="${pdfHref}" target="_blank" rel="noopener" aria-label="Download the PDF report">PDF</a>` : ""}<button class="btn sm" type="button" id="share">Share</button></span></li>
      <li><span class="rx-do-n">3</span><div><b>Retake Photo (If Needed)</b><p>${clipped + notMeas ? `A new photo with both poles in frame could complete ${clipped + notMeas} wire${clipped + notMeas === 1 ? "" : "s"}.` : "Every detected wire was measured from this photo."} It cannot supply a sag reference.</p></div>
        <span class="rx-do-acts"><button class="btn sm" type="button" id="retake">Retake</button></span></li>
    </ol>

    <div class="rx-work">
      <div class="rx-main">
      <section class="rx-photo" aria-label="Photo">
        <div class="rx-bar" role="toolbar" aria-label="Photo view">
          <div class="rx-seg" role="radiogroup" aria-label="Show">
            <button type="button" role="radio" data-view="problem" aria-checked="true">${diagMode ? "Measured" : "Flagged"}</button>
            <button type="button" role="radio" data-view="all" aria-checked="false">All wires</button>
            <button type="button" role="radio" data-view="photo" aria-checked="false">Original</button>
            <button type="button" role="radio" data-view="mask" aria-checked="false" ${hasMask ? "" : "disabled title=\"No model mask for this inspection\""}>Mask</button>
            <button type="button" role="radio" data-view="both" aria-checked="false" ${hasMask ? "" : "disabled title=\"No model mask for this inspection\""}>Both</button>
          </div>
          <span class="fx-spacer"></span>
          <div class="rx-menu"><button class="btn sm" type="button" id="opts-btn" aria-expanded="false" aria-controls="opts">Layers</button>
            <div class="rx-pop" id="opts" hidden>
              <label class="rx-op"><span>Overlay opacity</span><input type="range" id="opacity" min="0" max="100" value="100"><output>100%</output></label>
              <label><input type="checkbox" id="veg" ${hasVeg ? "" : "disabled"}> Vegetation${hasVeg ? "" : " (none found)"}</label>
              <label><input type="checkbox" id="lyr-mask" ${hasMask ? "" : "disabled"}> Every pixel marked as wire</label>
              <label><input type="checkbox" id="lyr-labels" checked> Wire numbers</label>
              <label><input type="checkbox" id="lyr-poles"> Utility poles (approximate)</label>
            </div></div>
          <span class="rx-zoom"><button class="btn icon sm" type="button" data-zoom="out" aria-label="Zoom out">${ICON.zout}</button>
            <output id="zoom-pct" class="n" aria-live="off">100%</output>
            <button class="btn icon sm" type="button" data-zoom="in" aria-label="Zoom in">${ICON.zin}</button>
            <button class="btn sm" type="button" data-zoom="fit">${ICON.fit}Fit</button>
            <button class="btn sm" type="button" data-zoom="reset" title="Back to the default view, layers and zoom">Reset</button></span>
          ${diagMode ? `<p class="rx-legend"><span><i style="background:var(--cls-power)"></i>Power</span><span><i style="background:var(--cls-telecom)"></i>Telecom / fibre</span><span><i style="background:var(--cls-dead)"></i>Abandoned</span><span><i style="background:var(--cls-unknown)"></i>Type uncertain</span><span><i class="chord"></i>Chord</span><span><i class="dot"></i>Largest sag</span><span class="rx-legend-note">Colour shows wire type, not severity.</span></p>`
            : `<p class="rx-legend"><span><i class="high"></i>${SEVERITY.high}</span><span><i class="medium"></i>${SEVERITY.medium}</span><span class="all-only"><i class="low"></i>${SEVERITY.low}</span><span class="all-only"><i class="none"></i>${SEVERITY.none}</span></p>`}
        </div>
        <div class="fx-canvas rx-canvas" style="--ar:${r.image.width} / ${r.image.height}"><div class="canvas-wrap" id="fs-canvas"></div></div>
      </section>

      <section class="rx-wires" id="inventory" aria-labelledby="rx-wl">
        <header class="rx-inv-head"><h2 id="rx-wl">Wire inventory</h2>
          <div class="rx-tabs" role="tablist">
            <button role="tab" data-tab="m" aria-selected="true">Measured <b>${measuredWs.length}</b></button>
            <button role="tab" data-tab="u" aria-selected="false">Not measured <b>${unmeasured.length}</b></button>
          </div></header>
        <div class="rx-filters">
          <div class="rx-chips" role="group" aria-label="Quick filters"><button type="button" data-chip="all" aria-pressed="true">All <b></b></button>${Object.entries(FILTERS).map(([k, f]) => `<button type="button" data-chip="${k}" aria-pressed="false"${f.title ? ` title="${esc(f.title)}"` : ""}>${f.t} <b></b></button>`).join("")}</div>
          <input type="search" id="inv-q" placeholder="Search wire ID, type or status" aria-label="Search wires">
          <button type="button" class="btn sm" id="inv-clear" hidden>Clear filters</button>
        </div>
        <p class="rx-inv-hint" id="inv-hint" hidden></p>
        <div class="rx-inv-scroll" id="inv-scroll"><table class="rx-inv">
          <thead><tr><th scope="col">Wire ID</th><th scope="col">Type</th><th scope="col">Coverage</th><th scope="col" class="r">Image Sag Ratio</th><th scope="col">Status</th></tr></thead>
          <tbody id="inv-main"></tbody>
          <tbody id="inv-grp" class="rx-inv-grp"></tbody>
          <tbody id="inv-flat" hidden></tbody>
        </table></div>
      </section>
      </div>

      <aside class="rx-side"><section class="rx-wire" id="span-detail" aria-live="polite">${wireCard(focus[0] || null, pc)}</section></aside>
    </div>

    ${loc ? `<section class="rx-loc" aria-labelledby="rx-loc-h"><div class="rx-loc-info"><h2 id="rx-loc-h">Inspection location</h2>
        <dl><div><dt>Latitude</dt><dd class="mono">${loc.lat.toFixed(6)}</dd></div><div><dt>Longitude</dt><dd class="mono">${loc.lon.toFixed(6)}</dd></div>
          ${insp.barangay ? `<div><dt>Barangay</dt><dd>${esc(insp.barangay)}</dd></div>` : ""}
          <div><dt>Street</dt><dd id="rx-street" class="muted">Looking up…</dd></div></dl>
        <a class="btn" href="https://www.google.com/maps/search/?api=1&query=${loc.lat.toFixed(6)},${loc.lon.toFixed(6)}" target="_blank" rel="noopener">Open in Google Maps</a></div>
      <div class="fx-map"><div id="fs-map" role="region" aria-label="Location of this inspection"></div></div></section>` : ""}

    ${methodology(r, pc, LIMS)}
    ${r.reviews?.length ? `<p class="fx-muted">Reviewed by ${esc(r.reviews.at(-1).by)}</p>` : ""}
    </div>`;

  const root = stages.result;
  const detail = root.querySelector("#span-detail");
  const VIEWS = {
    problem: { masks: 1, sag: 1, labels: 1, inferred: 1 },
    all: { masks: 1, sag: 1, labels: 1, inferred: 1 },
    photo: {},
    mask: { wiremask: 1 }, // the model's wire mask on its own (every pixel marked as wire)
    both: { masks: 1, sag: 1, labels: 1, inferred: 1, wiremask: 1 },
  };
  let view = "problem", selected = focus[0]?.index ?? null;

  viewer?.destroy();
  viewer = new OverlayViewer($("#fs-canvas"), { onSelect: (i) => select(i, "photo") });
  window.wfViewer = viewer; // read-only handle for automated UI checks (scripts/e2e_check.py)
  const zoomOut = root.querySelector("#zoom-pct");
  $("#fs-canvas").addEventListener("zoom", () => { zoomOut.textContent = `${viewer.zoomPct}%`; });
  const applyView = (name) => {
    view = name;
    root.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.view === name)));
    const p = VIEWS[name];
    for (const k of ["masks", "catenary", "sag", "labels", "inferred", "ends", "skeleton"]) viewer.layers[k] = !!p[k];
    viewer.layers.labels = !!p.labels && $("#lyr-labels").checked;
    viewer.layers.wiremask = !!p.wiremask || $("#lyr-mask")?.checked || false;
    viewer.layers.poles = $("#lyr-poles").checked;
    viewer.layers.overlays = name !== "photo";
    viewer.setVisible(name === "problem" ? focus.map((w) => w.index) : null);
    root.querySelectorAll(".rx-legend .all-only").forEach((x) => { x.hidden = name !== "all"; });
  };
  viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => {
    if (hasMask) viewer.setWireMask(r.wire_mask.url);
    viewer.setPoles(r.scene?.poles_detected || []);
    viewer.layers.vegetation = false;
    if (diagMode) viewer.setMode("class"); // S5: colour by wire type; no severity exists to colour by
    applyView("problem");
    if (selected != null) { viewer.selected = selected; viewer.draw(); }
    zoomOut.textContent = `${viewer.zoomPct}%`;
  }).catch(() => toast.error("Photo could not be displayed"));

  // ---- inventory: tab + quick filters + search, combinable; the selection survives every filter change
  const inv = { tab: "m", chips: new Set(), q: "", open: false };
  const tabList = () => (inv.tab === "m" ? measuredWs : unmeasured);
  const words = (w) => [String(w.index), spanId(w.index), classLabel(w, true), COVER[coverageOf(w)], assessmentOf(w).t,
    w.comparison?.position ? ZONE[w.comparison.position] : isMeasured(w) && w.status !== "scored" ? "Reference Unavailable" : "",
    withinRes(w) ? "Within Resolution" : "", isMeasured(w) ? "" : plainFinding(w)].join(" ").toLowerCase();
  const hit = (w, chips = inv.chips) => (!inv.q || words(w).includes(inv.q)) && [...chips].every((k) => FILTERS[k].f(w));
  const trow = (w) => `<tr data-wire="${w.index}" tabindex="0" aria-selected="${w.index === selected}">
      <th scope="row"><span class="rx-id sm" style="--wc:${wireColor(w, "class")}">${w.index}</span><span class="rx-span">${spanId(w.index)}</span></th>
      <td>${esc(classLabel(w, true))}</td><td>${covPill(w)}</td>
      <td class="r n">${isMeasured(w) ? measuredText(w).replace(" (within resolution)", "") : `<span class="muted" title="Not measured: there is no value, not a zero">—</span>`}</td>
      <td><span class="rx-st">${statusPills(w)}</span>${isMeasured(w) ? "" : `<small class="rx-st-why">${esc(plainFinding(w))}</small>`}</td></tr>`;
  const renderInv = () => {
    const all = tabList(), vis = all.filter((w) => hit(w));
    const flat = inv.tab === "m" ? vis.filter(withinRes) : [];
    const main = vis.filter((w) => !flat.includes(w));
    const empty = (t) => `<tr class="rx-inv-empty"><td colspan="5">${t}</td></tr>`;
    root.querySelector("#inv-main").innerHTML = main.length ? main.map(trow).join("")
      : empty(!all.length ? (inv.tab === "m" ? "No wire was measured in this photo." : "Every detected wire was measured.") : flat.length ? "Every matching wire is straight within the resolution (below)." : "No wire matches these filters.");
    root.querySelector("#inv-grp").innerHTML = flat.length ? `<tr><td colspan="5"><button type="button" id="grp-btn" aria-expanded="${inv.open}" aria-controls="inv-flat">${inv.open ? "Hide" : "Show"} ${flat.length} wire${flat.length === 1 ? "" : "s"} with no resolvable sag</button>
      <small>Straight to within the 1 pixel resolution at this image scale. This is not evidence that ${flat.length === 1 ? "the wire is" : "these wires are"} safe.</small></td></tr>` : "";
    const fl = root.querySelector("#inv-flat");
    fl.innerHTML = flat.map(trow).join(""); fl.hidden = !inv.open || !flat.length;
    root.querySelector('[data-chip="all"] b').textContent = all.filter((w) => hit(w, new Set())).length;
    for (const k of Object.keys(FILTERS)) {
      const others = new Set([...inv.chips].filter((x) => x !== k && x !== FILTERS[k].not)); others.add(k);
      root.querySelector(`[data-chip="${k}"] b`).textContent = all.filter((w) => hit(w, others)).length;
    }
    root.querySelectorAll("[data-chip]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.chip === "all" ? !inv.chips.size : inv.chips.has(b.dataset.chip))));
    root.querySelector("#inv-clear").hidden = !inv.chips.size && !inv.q;
    hint();
  };
  // the selected wire stays selected when the filters hide it, and the inventory says so (with a way back)
  const hint = () => {
    const el = root.querySelector("#inv-hint");
    const w = r.wires.find((x) => x.index === selected);
    const shown = w && root.querySelector(`#inventory tr[data-wire="${selected}"]`);
    const tabOk = w && (inv.tab === "m") === isMeasured(w);
    el.hidden = !w || (shown && tabOk);
    if (!el.hidden) el.innerHTML = `Selected wire ${w.index} (${spanId(w.index)}) is ${tabOk ? "hidden by the filters" : `in the ${isMeasured(w) ? "Measured" : "Not measured"} tab`}. <button type="button" class="linkish" id="inv-reveal">Show it</button>`;
  };
  const setTab = (t) => {
    inv.tab = t;
    root.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x.dataset.tab === t)));
    renderInv();
  };
  // controlled scrolling: only the inventory's own box scrolls, never the page
  const reveal = (i, force) => {
    const w = r.wires.find((x) => x.index === i); if (!w) return;
    if (force) {
      if ((inv.tab === "m") !== isMeasured(w)) setTab(isMeasured(w) ? "m" : "u");
      if (!hit(w)) { inv.chips.clear(); inv.q = ""; root.querySelector("#inv-q").value = ""; }
      if (withinRes(w) && !inv.open) inv.open = true;
      renderInv();
    }
    const tr = root.querySelector(`#inventory tr[data-wire="${i}"]`), box = root.querySelector("#inv-scroll");
    if (!tr || tr.offsetParent === null) return;
    const top = tr.offsetTop - box.querySelector("thead").offsetHeight, bottom = tr.offsetTop + tr.offsetHeight;
    if (top < box.scrollTop) box.scrollTop = top;
    else if (bottom > box.scrollTop + box.clientHeight) box.scrollTop = bottom - box.clientHeight;
  };
  const mark = () => {
    root.querySelectorAll("#inventory tr[data-wire]").forEach((tr) => tr.setAttribute("aria-selected", String(Number(tr.dataset.wire) === selected)));
    detail.innerHTML = wireCard(selected == null ? null : r.wires.find((w) => w.index === selected), pc);
    hint();
  };
  const select = (i, from) => {
    selected = i;
    const w = r.wires.find((x) => x.index === i);
    if (from !== "photo" && w && view === "problem" && !focus.includes(w)) applyView("all"); // a wire the view hides: show all
    if (from !== "photo") viewer.select(i); // centres the wire inside the canvas only
    mark();
    if (from === "photo" && i != null) reveal(i, true);
  };
  renderInv();

  root.addEventListener("click", (e) => {
    const tr = e.target.closest("#inventory tr[data-wire]"); if (tr) select(Number(tr.dataset.wire), "table");
    const v = e.target.closest("[data-view]"); if (v && !v.disabled) applyView(v.dataset.view);
    const t = e.target.closest("[data-tab]"); if (t) setTab(t.dataset.tab);
    const c = e.target.closest("[data-chip]");
    if (c) {
      const k = c.dataset.chip;
      if (k === "all") inv.chips.clear();
      else if (inv.chips.has(k)) inv.chips.delete(k);
      else { inv.chips.add(k); if (FILTERS[k].not) inv.chips.delete(FILTERS[k].not); }
      renderInv();
    }
    if (e.target.closest("#grp-btn")) { inv.open = !inv.open; renderInv(); }
    if (e.target.closest("#inv-clear")) { inv.chips.clear(); inv.q = ""; root.querySelector("#inv-q").value = ""; renderInv(); }
    if (e.target.closest("#inv-reveal")) reveal(selected, true);
  });
  root.querySelector("#inv-q").addEventListener("input", (e) => { inv.q = e.target.value.trim().toLowerCase(); renderInv(); });
  root.addEventListener("keydown", (e) => {
    const tr = e.target.closest("#inventory tr[data-wire]"); if (!tr) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(Number(tr.dataset.wire), "table"); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const rows = [...root.querySelectorAll("#inventory tr[data-wire]")].filter((x) => x.offsetParent !== null);
      const n = rows[rows.indexOf(tr) + (e.key === "ArrowDown" ? 1 : -1)];
      if (n) { n.focus({ preventScroll: true }); select(Number(n.dataset.wire), "table"); reveal(Number(n.dataset.wire), false); }
    }
  });
  // view options
  const optsBtn = $("#opts-btn"), opts = $("#opts");
  optsBtn.addEventListener("click", () => { const o = opts.hidden; opts.hidden = !o; optsBtn.setAttribute("aria-expanded", String(o)); });
  document.addEventListener("click", (e) => { if (!opts.hidden && !e.target.closest(".rx-menu")) { opts.hidden = true; optsBtn.setAttribute("aria-expanded", "false"); } });
  const op = $("#opacity");
  const setOpacity = (x) => { op.value = x; viewer.setOpacity(x / 100); op.nextElementSibling.textContent = `${x}%`; };
  op.addEventListener("input", () => setOpacity(Number(op.value)));
  $("#veg").addEventListener("change", (e) => viewer.set("vegetation", e.target.checked));
  for (const id of ["lyr-mask", "lyr-labels", "lyr-poles"]) $(`#${id}`).addEventListener("change", () => applyView(view));
  root.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => ({
    in: () => viewer.zoomIn(), out: () => viewer.zoomOut(), fit: () => viewer.reset(),
    reset: () => { // the default view, layers, opacity and zoom
      $("#veg").checked = false; viewer.set("vegetation", false);
      $("#lyr-mask").checked = false; $("#lyr-labels").checked = true; $("#lyr-poles").checked = false;
      setOpacity(100); applyView("problem"); viewer.reset();
    },
  })[b.dataset.zoom]()));

  $("#again").addEventListener("click", reset);
  $("#retake").addEventListener("click", () => startRetake(r));
  $("#share").addEventListener("click", async () => {
    const url = `${location.origin}/workspace/result/${r.id}`;
    try {
      if (navigator.share) await navigator.share({ title: insp.name || "Pole inspection", url });
      else { await navigator.clipboard.writeText(url); toast.success("Link copied"); }
    } catch { /* cancelled or blocked: nothing to do */ }
  });
  $("#banner-x")?.addEventListener("click", () => {
    try { sessionStorage.setItem(bannerKey, "1"); } catch { /* storage unavailable: hide for this view only */ }
    $("#rx-banner").remove();
  });
  $("#guard-retake")?.addEventListener("click", async () => {
    try { await api(`/api/analysis/${r.id}/guardrail`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "retake" }) }); } catch { /* never blocks */ }
    startRetake(r);
  });

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

/** S12: every disclaimer and method note in one folded section at the bottom of the page. */
function methodology(r, pc, LIMS) {
  const d = r.diagnosis;
  const topics = (r.methodology || []).map((t) => [t.title, t.text]); // app/services/methodology.py, as in the PDF
  const li = (xs) => `<ul>${xs.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>`;
  return `<details class="rx-method" id="methodology"><summary><span>Methodology &amp; Limitations</span><small>How the numbers are produced, the reference used and what one photo cannot show</small></summary>
    <div class="rx-method-body">
      <div class="rx-method-grid">${topics.map(([t, p]) => `<section${p.length > 400 ? ' class="wide"' : ""}><h3>${esc(t)}</h3><p>${esc(p)}</p></section>`).join("")}</div>
      ${LIMS.length ? `<section class="rx-method-sec"><h3>This photo</h3><ul>${LIMS.map(([, t]) => `<li>${t}</li>`).join("")}</ul></section>` : ""}
      ${d && r.wires.length && r.status !== "error" ? `<section class="rx-method-sec" aria-labelledby="rx-diag-h"><h3 id="rx-diag-h">What the analysis found</h3>
        <p>${esc(d.processing)}. ${d.why ? esc(d.why) : ""}</p>
        ${Object.keys(d.not_measured_reasons || {}).length ? `<p>Not measured: ${esc(Object.entries(d.not_measured_reasons).map(([k, n]) => `${k.toLowerCase()} (${n})`).join(", "))}.</p>` : ""}
        <div class="rx-diag-cols">
          ${d.observations.length ? `<div><h4>Observed in the image</h4>${li(d.observations)}</div>` : ""}
          ${d.unavailable.length ? `<div><h4>Not available from one photo</h4>${li(d.unavailable)}</div>` : ""}
          ${d.needed.length ? `<div><h4>What would complete the assessment</h4>${li(d.needed)}</div>` : ""}
        </div></section>` : ""}
      <section class="rx-method-sec rx-notice" aria-label="Important Notice"><wf-notice variant="full"></wf-notice></section>
    </div></details>`;
}

function skipSummary(ws) {
  const n = {};
  for (const w of ws) { const k = skipReason(w); n[k] = (n[k] || 0) + 1; }
  return Object.entries(n).sort((a, b) => b[1] - a[1]).map(([k, v]) => `${v} ${k}`).join(", ");
}
const statusLine = (r) => ({ "AT RISK - HIGH": "Overall: at risk, high", "AT RISK - MODERATE": "Overall: at risk, moderate", "NOT AT RISK": "Overall: no pole-level risk indicated" })[r.hazard?.verdict] || "";


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
