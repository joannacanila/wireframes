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

let photoScale = 0; // S7: one meter scale per photo, shared by the measurement-only meters
// a key label is centred on its mark unless it would run past either end of the track (long labels need ~30 %)
const keyAt = (v) => (v < 30 ? "at-start" : v > 70 ? "at-end" : "");
/** D-26 / D-27: the original v2.21 meter (track, green / amber / red zones, reference tick, dot, key, legend) for a
 * power line compared with the configured provisional 3 % reference. Zones come from the reference and this wire's
 * own ±1 px resolution (below / at within resolution / above), never from the withdrawn 2.4 % / 4.0 % boundaries.
 * A comparison, not an assessment: the wire gets no severity from it. */
const DOT = { below: "low", at: "medium", above: "high" };
function meterRef(w) {
  const c = w.comparison, ref = c.reference, z = c.zones, res = w.sag_ratio_resolution || 0, part = c.state === "measured_partial";
  const max = Math.max(w.sag_ratio + res, z.above_from) * 1.3;
  const v = (q) => Math.min(100, Math.max(0, (q / max) * 100)), x = (q) => `${v(q)}%`, at = v(w.sag_ratio);
  return `<div class="rx-meter cmp ${c.position}" role="img" aria-label="Image sag ratio ${measuredText(w)}${part ? ", visible part only" : ""}; ${esc(c.label)}; ${{ below: "below", at: "at the reference within the measurement resolution", above: "above" }[c.position]}">
    <div class="rx-meter-track"><i class="z z-low" style="left:0;width:${x(z.at_from)}"></i><i class="z z-medium" style="left:${x(z.at_from)};width:calc(${x(z.above_from)} - ${x(z.at_from)})"></i><i class="z z-high" style="left:${x(z.above_from)};right:0"></i>
      ${res ? `<i class="rx-meter-res" style="left:${v(Math.max(0, w.sag_ratio - res))}%;width:${Math.max(v(w.sag_ratio + res) - v(Math.max(0, w.sag_ratio - res)), 0.6)}%"></i>` : ""}
      <i class="rx-meter-ref" style="left:${x(ref)}"></i><i class="rx-meter-dot ${DOT[c.position]}${part ? " partial" : ""}" style="left:${at}%" title="This wire: image sag ratio ${measuredText(w)}"></i></div>
    <div class="rx-meter-key"><span class="ref-k ${keyAt(v(ref))}" style="left:${x(ref)}">Reference ${pp(ref)}</span><span class="ref ${keyAt(at)}" style="left:${at}%">This wire ${measuredText(w)}${part ? " (visible part)" : ""}</span></div>
    <p class="rx-zones"><span><i class="z-low"></i>Below reference</span><span><i class="z-medium"></i>At reference (±resolution)</span><span><i class="z-high"></i>Above reference</span></p></div>
    <p class="rx-note">${esc(c.label)}: not a validated limit (ERB Res. 95-21, since superseded). The image sag ratio is not physical sag, and this comparison is not an assessment.</p>`;
}

/** D-27: a measured wire with no applicable reference (telecom / fibre, abandoned, type uncertain). The same meter on
 * the photo's shared scale, filled in the wire's type colour up to its image sag ratio: a measurement only, with no
 * zones and no reference tick. */
function meterType(w) {
  const res = w.sag_ratio_resolution || 0, part = w.reason_code === "endpoint_unavailable";
  const max = photoScale || (w.sag_ratio + res) * 1.3 || 0.01;
  const v = (x) => Math.min(100, Math.max(0, (x / max) * 100));
  const at = v(w.sag_ratio), lo = v(Math.max(0, w.sag_ratio - res)), hi = v(w.sag_ratio + res);
  const why = w.class_status === "uncertain" ? "the photo does not show this wire's type clearly"
    : `no validated sag reference exists for ${w.class === "telecom-cable" ? "telecom / fibre cable" : w.class === "abandoned-wire" ? "an abandoned wire" : "this wire type"}`;
  return `<div class="rx-meter type" style="--wc:${wireColor(w, "class")}" role="img" aria-label="Image sag ratio ${measuredText(w)}${part ? ", visible part only" : ""}, on a scale from 0 to ${pp(max)} shared by the wires on this photo; comparison unavailable">
    <div class="rx-meter-track"><i class="rx-meter-fill" style="width:${at}%"></i>${res ? `<i class="rx-meter-res" style="left:${lo}%;width:${Math.max(hi - lo, 0.6)}%"></i>` : ""}<i class="rx-meter-dot${part ? " partial" : ""}" style="left:${at}%" title="This wire: image sag ratio ${measuredText(w)}"></i></div>
    <div class="rx-meter-key"><span class="at-start" style="left:0">0%</span><span class="at-end" style="left:100%">${pp(max)}</span><span class="ref ${keyAt(at)}" style="left:${at}%">This wire ${measuredText(w)}${part ? " (visible part)" : ""}</span></div></div>
    <p class="rx-note"><b>Comparison unavailable:</b> ${why}, so this is an image measurement only, with no condition. Colour shows the wire type; the scale is shared by the wires on this photo.</p>`;
}

/** D-27: the power-line comparison counts in one sentence, or that no wire in the photo is covered. */
function cmpLine(r) {
  const c = r.comparison;
  if (!c || !r.wires.length) return "";
  const n = c.counts;
  if (!Object.values(n).some(Boolean)) return `<p class="rx-coverage rx-cmp-line">No wire in this photo is covered by the ${esc(c.label)}; the image sag ratios are measurements only.</p>`;
  return `<p class="rx-coverage rx-cmp-line">Power lines compared with the ${esc(c.label)}: <span class="rx-cmp above">${n.above} above</span> <span class="rx-cmp at">${n.at} at (within resolution)</span> <span class="rx-cmp below">${n.below} below</span>${n.not_measured ? ` · ${n.not_measured} not measured` : ""}. A comparison, not an assessment.</p>`;
}

function meterEmpty(w) {
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

/** S5: what the analysis found for this photo (app/services/diagnostics.py), shown with every result. */
function diagHtml(r) {
  const d = r.diagnosis;
  if (!d || r.status === "error" || !r.wires.length) return "";
  const li = (xs) => `<ul>${xs.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>`;
  const reasons = Object.entries(d.not_measured_reasons || {});
  return `<section class="rx-diag" aria-labelledby="rx-diag-h"><h2 id="rx-diag-h">What the analysis found</h2>
    <p class="rx-diag-stage">${esc(d.processing)}. ${d.counts.detected} detected · ${d.counts.measured} measured · ${d.counts.measured_partial} visible part only · ${d.counts.not_measured} not measured${d.counts.not_applicable ? ` · ${d.counts.not_applicable} drop wires` : ""} · ${d.counts.assessed} assessed for severity.</p>
    ${reasons.length ? `<p class="rx-diag-stage">Not measured: ${esc(reasons.map(([k, n]) => `${k.toLowerCase()} (${n})`).join(", "))}.</p>` : ""}
    <div class="rx-diag-cols">
      ${d.observations.length ? `<div><h3>Observed in the image</h3>${li(d.observations)}</div>` : ""}
      ${d.unavailable.length ? `<div><h3>Not available from one photo</h3>${li(d.unavailable)}</div>` : ""}
      ${d.needed.length ? `<div><h3>What would complete the assessment</h3>${li(d.needed)}</div>` : ""}
    </div></section>`;
}

function wireCard(w) {
  if (!w) return `<p class="rx-pick">Select a numbered wire on the photo or in the list to see its measurement.</p>`;
  const g = group(w);
  const scored = w.status === "scored" && w.sag_ratio != null;
  const dg = w.diagnosis;
  const lim = scored && w.delta_lim ? w.delta_lim : null;
  const diff = lim != null ? (w.sag_ratio - lim) * 100 : null;
  const max = lim != null ? Math.max(w.sag_ratio, lim) * 1.35 : 1;
  return `<header class="rx-wire-head"><span class="rx-id" style="--wc:${wireColor(w)}">${w.index}</span>
      <div><b>${esc(classLabel(w))}</b><span class="rx-sev ${g}">${esc(bandOf(w))}</span><span class="rx-span">Span ${spanId(w.index)}</span></div></header>
    <p class="rx-obs">${esc(observation(w))}</p>
    ${scored ? `<dl class="rx-meas">
        <div><dt>This wire's sag</dt><dd>${pp(w.sag_ratio)}</dd></div>
        ${lim != null ? `<div><dt>Reference limit</dt><dd>${pp(lim)}</dd></div>
        <div><dt>Difference</dt><dd class="${diff > 0 ? "over" : ""}">${diff > 0 ? "+" : diff < 0 ? "−" : ""}${Math.abs(diff).toFixed(1)} percentage points</dd></div>` : ""}
      </dl>
      ${lim != null ? `${meter(w, g)}
        <p class="rx-note">Sag is how far the wire hangs below the straight line between its ends, as a share of that length. The reference is the sag configured for this wire type in WIREFRAME; it has not been calibrated against local field measurements.</p>` : ""}`
      : ""}
    ${!scored && isMeasured(w) ? `<dl class="rx-meas"><div><dt>Image sag ratio${w.reason_code === "endpoint_unavailable" ? " (visible part)" : ""}</dt><dd>${measuredText(w)}</dd></div>
        ${w.comparison?.position ? `<div><dt>Reference</dt><dd>${pp(w.comparison.reference)} <small>(${esc(w.comparison.status)}, power lines)</small></dd></div>
        <div><dt>Difference</dt><dd class="${w.comparison.position === "above" ? "over" : ""}">${w.comparison.difference_pp > 0 ? "+" : w.comparison.difference_pp < 0 ? "−" : ""}${Math.abs(w.comparison.difference_pp).toFixed(1)} percentage points</dd></div>`
          : `<div><dt>Reference</dt><dd class="none">Not applicable</dd></div>
        <div><dt>Difference</dt><dd class="none">Not available</dd></div>`}</dl>
      ${w.comparison?.position ? meterRef(w) : meterType(w)}
      ${dg ? "" : `<p class="rx-note">Measured in image pixels: the largest distance from the traced wire to the straight line between its visible ends, divided by that line's length (± is the 1 pixel resolution). It is not physical sag and is not compared with any limit, because no validated sag reference exists for this wire type.</p>`}` : ""}
    ${!isMeasured(w) && w.status !== "service_drop" && w.status !== "scored" ? meterEmpty(w) : ""}
    ${dg ? `<ul class="rx-diag-wire" aria-label="What the analysis found for this wire">${dg.lines.filter((l) => l.key !== "detection").map((l) => `<li data-k="${esc(l.key)}">${esc(l.text)}.</li>`).join("")}</ul>
      ${dg.needed.length ? `<p class="rx-note"><b>To complete the assessment:</b> ${esc(dg.needed.join(". "))}.</p>` : ""}` : ""}
    ${w.class_status === "uncertain" ? `<p class="rx-note">The photo does not show clearly whether this is a power line or a telecommunications cable, so no type is claimed.</p>` : ""}`;
}

function renderResult(r) {
  const h = headline(r);
  const loc = r.pole.location;
  const insp = r.inspection || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");
  const when = new Date(r.created_at);
  const groups = { high: [], medium: [], low: [], none: [] };
  r.wires.forEach((w) => groups[group(w)].push(w));
  for (const k of Object.keys(groups)) groups[k].sort((a, b) => (b.sag_ratio ?? -1) - (a.sag_ratio ?? -1) || a.index - b.index);
  const attention = [...groups.high, ...groups.medium];
  const assessed = r.wires.filter((w) => w.status === "scored").length;
  const notAssessed = r.wires.filter((w) => w.status !== "scored").length;
  // S5: with no assessed span (no validated reference), the measured wires are the diagnostic focus: drawn with their
  // fitted curve, chord and sag point, listed by image-derived sag ratio (a measurement, never a severity)
  const measuredWs = r.wires.filter(isMeasured).sort((a, b) => b.sag_ratio - a.sag_ratio || a.index - b.index);
  const unmeasured = r.wires.filter((w) => !isMeasured(w));
  const diagMode = !assessed && measuredWs.length > 0;
  const focus = attention.length ? attention : diagMode ? measuredWs : [];
  // a retake helps only wires that were not measured or only partly measured, never a missing reference
  const fixable = r.wires.filter((w) => (!isMeasured(w) && w.status !== "service_drop") || w.reason_code === "endpoint_unavailable").length;
  const dc = r.diagnosis?.counts;
  photoScale = Math.max(0, ...measuredWs.map((w) => w.sag_ratio + (w.sag_ratio_resolution || 0))) * 1.3; // S7: one meter scale per photo
  const hasMask = !!r.wire_mask, hasVeg = (r.vegetation || []).length > 0;
  const lims = limitations(r);
  const limKeys = new Set(lims.map((l) => l.key));
  const LIMS = [
    ["overlap", "Overlapping wires can prevent reliable separation."],
    ["edge", "Wires extending beyond the photograph may not be fully measurable."],
    ["small", "Distant or obscured wires may not have sufficient visual detail."],
  ].filter(([k]) => limKeys.has(k) || (k === "small" && (limKeys.has("other") || limKeys.has("quality") || limKeys.has("estimated"))));
  const row = (w) => `<li data-wire="${w.index}" tabindex="0" aria-selected="false" role="option">
      <span class="rx-id" style="--wc:${wireColor(w, diagMode ? "class" : "risk")}">${w.index}</span>
      <span class="rx-row"><span class="rx-line"><span class="rx-type">${esc(classLabel(w, true))}</span><span class="rx-sev ${group(w)}">${esc(bandOf(w))}</span></span>
        <span class="rx-find">${esc(plainFinding(w))}</span></span></li>`;
  const list = (ws, empty) => ws.length ? `<ul class="rx-list" role="listbox" aria-label="Wires">${ws.map(row).join("")}</ul>` : `<p class="rx-empty">${empty}</p>`;

  stages.result.innerHTML = `
    <div class="rx">
    <header class="rx-head">
      <div><h1>${esc(insp.name || "Pole inspection")}</h1>
        <p>${[place, `${when.toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric" })}, ${when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })}`].filter(Boolean).map(esc).join(" · ")}</p></div>
      <div class="rx-actions">
        ${r.status !== "error" ? `<a class="btn lg" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener" id="pdf">${ICON.pdf}Download PDF report</a>` : ""}
        <button class="btn primary lg" id="again" type="button">${ICON.plus}New inspection</button>
      </div>
    </header>
    <p class="rx-phototip"><b>Photography tip:</b> stand where both supporting poles and the full span of wire are visible, with the wires against open sky.</p>
    ${guardBanner(r)}
    ${r.wires.length && fixable / r.wires.length > 0.5 ? `<div class="rx-retake" role="status"><b>More than half of the detected wires (${fixable} of ${r.wires.length}) could not be measured completely from this photo.</b> Retake the photo from a few steps along the street so the wires separate, with both poles in the frame.</div>` : ""}

    <section class="rx-summary ${h.cls}" aria-labelledby="rx-title">
      <h2 id="rx-title">${esc(h.title)}</h2>
      ${h.lines.map((l) => `<p>${esc(l)}</p>`).join("")}
      <dl class="rx-counts">
        ${(assessed ? [["high", SEVERITY.high, groups.high.length], ["medium", SEVERITY.medium, groups.medium.length], ["low", SEVERITY.low, groups.low.length], ["none", SEVERITY.none, groups.none.length]]
          // S4: no validated reference, so no severity counts; the measurements and the not-assessed total only
          : dc ? [["none", "Measured", dc.measured], ["none", "Visible part only", dc.measured_partial], ["none", "Not measured", dc.not_measured], ["none", "Assessed", dc.assessed]]
          : [["none", "Sag ratio measured", r.wires.filter((w) => w.sag_ratio != null).length], ["none", SEVERITY.none, groups.none.length]])
          .map(([k, t, n]) => `<div class="${k}"><dt>${t}</dt><dd>${n}</dd></div>`).join("")}
      </dl>
      ${r.wires.length ? `<p class="rx-coverage">${r.wires.length} ${r.wires.length === 1 ? "wire" : "wires"} detected · ${assessed} assessed · ${notAssessed} not assessed${statusLine(r) ? ` · ${esc(statusLine(r))}` : ""}</p>` : ""}
      ${cmpLine(r)}
    </section>

    <div class="rx-work">
      <section class="rx-photo" aria-label="Photo">
        <div class="rx-bar" role="toolbar" aria-label="Photo view">
          <div class="rx-seg" role="radiogroup" aria-label="Show">
            <button type="button" role="radio" data-view="problem" aria-checked="true">${diagMode ? "Measured wires" : "Problem wires"}</button>
            <button type="button" role="radio" data-view="photo" aria-checked="false">Original photo</button>
            <button type="button" role="radio" data-view="all" aria-checked="false">All traced wires</button>
            <button type="button" role="radio" data-view="mask" aria-checked="false" ${hasMask ? "" : "disabled title=\"No model mask for this inspection\""}>Mask</button>
          </div>
          <span class="fx-spacer"></span>
          <div class="rx-menu"><button class="btn" type="button" id="opts-btn" aria-expanded="false" aria-controls="opts">View options</button>
            <div class="rx-pop" id="opts" hidden>
              <label class="rx-op"><span>Overlay opacity</span><input type="range" id="opacity" min="0" max="100" value="100"><output>100%</output></label>
              <label><input type="checkbox" id="veg" ${hasVeg ? "" : "disabled"}> Vegetation${hasVeg ? "" : " (none found)"}</label>
              <label><input type="checkbox" id="lyr-mask" ${hasMask ? "" : "disabled"}> Every pixel marked as wire</label>
              <label><input type="checkbox" id="lyr-labels" checked> Wire numbers</label>
              <label><input type="checkbox" id="lyr-poles"> Utility poles (approximate)</label>
            </div></div>
          <span class="rx-zoom"><button class="btn icon" type="button" data-zoom="out" aria-label="Zoom out">${ICON.zout}</button>
            <button class="btn icon" type="button" data-zoom="in" aria-label="Zoom in">${ICON.zin}</button>
            <button class="btn" type="button" data-zoom="fit">${ICON.fit}Fit</button></span>
        </div>
        <div class="fx-canvas rx-canvas" style="--ar:${r.image.width} / ${r.image.height}"><div class="canvas-wrap" id="fs-canvas"></div></div>
        ${focus.length ? `<div class="rx-callouts" aria-label="${diagMode ? "Measured wires" : "Flagged wires"}">${focus.slice(0, 12).map((w) => `<button type="button" data-goto="${w.index}" class="rx-call ${diagMode ? "measured" : group(w)}"><span${diagMode ? ` style="background:${wireColor(w, "class")}"` : ""}>${w.index}</span>${esc(classLabel(w, true))} · ${pp(w.sag_ratio)}${diagMode ? " (image)" : ""}</button>`).join("")}</div>` : ""}
        ${diagMode ? `<p class="rx-legend"><span><i style="background:var(--cls-power)"></i>Power line</span><span><i style="background:var(--cls-telecom)"></i>Telecom / fibre</span><span><i style="background:var(--cls-dead)"></i>Abandoned</span><span><i style="background:var(--cls-unknown)"></i>Type uncertain</span><span class="rx-legend-note">Dashed line: chord between the visible ends · dot: largest sag. Colour shows wire type, not severity.</span></p>`
          : `<p class="rx-legend"><span><i class="high"></i>${SEVERITY.high}</span><span><i class="medium"></i>${SEVERITY.medium}</span><span class="all-only"><i class="low"></i>${SEVERITY.low}</span><span class="all-only"><i class="none"></i>${SEVERITY.none}</span></p>`}
      </section>

      <aside class="rx-side">
        <section class="rx-wire" id="span-detail" aria-live="polite">${wireCard(focus[0] || null)}</section>
        <section class="rx-wires" aria-labelledby="rx-wl">
          <h2 id="rx-wl">Wires</h2>
          ${diagMode ? `<div class="rx-tabs" role="tablist">
            <button role="tab" data-tab="att" aria-selected="true">Measured <b>${measuredWs.length}</b></button>
            <button role="tab" data-tab="ok" aria-selected="false">Not measured <b>${unmeasured.length}</b></button>
          </div>
          <div data-panel="att"><p class="rx-split">Sorted by image-derived sag ratio. A ratio is a measurement in the photo, not a severity. They have not passed any check.</p>${list(measuredWs, "")}</div>
          <div data-panel="ok" hidden>${unmeasured.length ? `<p class="rx-split">${esc(skipSummary(unmeasured))}</p>` : ""}${list(unmeasured, "Every detected wire was measured.")}</div>`
          : `<div class="rx-tabs" role="tablist">
            <button role="tab" data-tab="att" aria-selected="true">Flagged <b>${attention.length}</b></button>
            <button role="tab" data-tab="ok" aria-selected="false">${SEVERITY.low} <b>${groups.low.length}</b></button>
          </div>
          <div data-panel="att">${attention.length ? `<p class="rx-split">${groups.high.length} ${SEVERITY.high.toLowerCase()} · ${groups.medium.length} ${SEVERITY.medium.toLowerCase()}</p>` : ""}${list(attention, "No wire in this photo was flagged.")}</div>
          <div data-panel="ok" hidden>${list(groups.low, "No wire was measured within the reference.")}</div>
          ${groups.none.length ? `<details class="rx-skipped"><summary><b>${groups.none.length} ${groups.none.length === 1 ? "wire" : "wires"} not assessed</b>: ${esc(skipSummary(groups.none))}</summary>
            <p class="rx-skip-note">These wires were not assessed. They have not passed any check. Where a sag ratio is shown, it is an image measurement only.</p>
            ${list(groups.none, "")}</details>` : ""}`}
        </section>
      </aside>
    </div>

    <div class="rx-guide">
      <section class="rx-steps" aria-labelledby="rx-do"><h2 id="rx-do">What to do</h2>
        <ol>
          <li><b>Keep your distance</b><p>Never touch, pull, move or try to repair low-hanging wires. Telecommunications cables may be near energized electrical conductors.</p></li>
          <li><b>Report the findings</b><p>Download the PDF report and share it with your barangay office, the electric utility or the telecommunications provider.</p></li>
          <li><b>Arrange field verification</b><p>If another photograph is needed, capture both poles and the full visible span from a safe location.</p></li>
        </ol></section>
      <section class="rx-limits" aria-labelledby="rx-lim"><h2 id="rx-lim">Photo limitations</h2>
        ${LIMS.length ? `<ul>${LIMS.map(([, t]) => `<li>${t}</li>`).join("")}</ul>` : `<p>${assessed === r.wires.length ? "None found: every detected wire could be assessed." : "None found: every detected wire was measured."}</p>`}
      </section>
    </div>
    ${diagHtml(r)}

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
    ${r.reviews?.length ? `<p class="fx-muted">Reviewed by ${esc(r.reviews.at(-1).by)}</p>` : ""}
    </div>`;

  const root = stages.result;
  const detail = root.querySelector("#span-detail");
  const VIEWS = {
    problem: { masks: 1, catenary: 0, sag: 1, labels: 1, inferred: 1, ends: 0, wiremask: 0 },
    photo: {},
    all: { masks: 1, catenary: 0, sag: 1, labels: 1, inferred: 1, ends: 0, wiremask: 0 },
    mask: { wiremask: 1 }, // the model's wire mask on its own (every pixel marked as wire)
  };
  let view = "problem";

  viewer?.destroy();
  viewer = new OverlayViewer($("#fs-canvas"), { onSelect: (i) => mark(i, true) });
  window.wfViewer = viewer; // read-only handle for automated UI checks (scripts/e2e_check.py)
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
    if (focus[0]) { viewer.selected = focus[0].index; viewer.draw(); mark(focus[0].index, false); }
  }).catch(() => toast.error("Photo could not be displayed"));

  const mark = (i, scroll) => {
    root.querySelectorAll("li[data-wire]").forEach((li) => {
      const on = Number(li.dataset.wire) === i;
      li.setAttribute("aria-selected", String(on));
      if (on && scroll && li.offsetParent) li.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    detail.innerHTML = wireCard(i == null ? null : r.wires.find((w) => w.index === i));
  };
  const select = (i) => {
    const w = r.wires.find((x) => x.index === i);
    if (w && view === "problem" && !focus.includes(w)) applyView("all"); // a wire the current view hides: show all
    viewer.select(i); mark(i, true);
  };
  root.addEventListener("click", (e) => {
    const li = e.target.closest("li[data-wire]"); if (li) select(Number(li.dataset.wire));
    const go = e.target.closest("[data-goto]"); if (go) { select(Number(go.dataset.goto)); root.querySelector(".rx-photo").scrollIntoView({ block: "nearest", behavior: "smooth" }); }
    const v = e.target.closest("[data-view]"); if (v && !v.disabled) applyView(v.dataset.view);
    const t = e.target.closest("[data-tab]");
    if (t) {
      root.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === t)));
      root.querySelectorAll("[data-panel]").forEach((p) => { p.hidden = p.dataset.panel !== t.dataset.tab; });
    }
  });
  root.addEventListener("keydown", (e) => {
    const li = e.target.closest("li[data-wire]"); if (!li) return;
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(Number(li.dataset.wire)); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); const n = e.key === "ArrowDown" ? li.nextElementSibling : li.previousElementSibling;
      if (n) { n.focus(); select(Number(n.dataset.wire)); }
    }
  });
  // view options
  const optsBtn = $("#opts-btn"), opts = $("#opts");
  optsBtn.addEventListener("click", () => { const o = opts.hidden; opts.hidden = !o; optsBtn.setAttribute("aria-expanded", String(o)); });
  document.addEventListener("click", (e) => { if (!opts.hidden && !e.target.closest(".rx-menu")) { opts.hidden = true; optsBtn.setAttribute("aria-expanded", "false"); } });
  const op = $("#opacity");
  op.addEventListener("input", () => { viewer.setOpacity(op.value / 100); op.nextElementSibling.textContent = `${op.value}%`; });
  $("#veg").addEventListener("change", (e) => viewer.set("vegetation", e.target.checked));
  for (const id of ["lyr-mask", "lyr-labels", "lyr-poles"]) $(`#${id}`).addEventListener("change", () => applyView(view));
  root.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => ({ in: () => viewer.zoomIn(), out: () => viewer.zoomOut(), fit: () => viewer.reset() })[b.dataset.zoom]()));

  $("#again").addEventListener("click", reset);
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
