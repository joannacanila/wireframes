// Field screener (v2.14). Result screen, in reading order:
//   1. inspection header: pole, time, file, PDF report, new inspection
//   2. outcome strip: verdict, physical hazards, next step; counts per risk level and the worst span (click to filter / select)
//   3. the photo (largest element): Original / Spans / Mask / Overlay presets, independent layers (segmentation pixels,
//      span masks, inferred gaps, centerlines, catenary, chord and sag, span ends), opacity, vegetation, zoom, fit, reset
//   4. beside it: the selected span in full, and the span table with risk filters (row <-> photo selection kept in sync)
//   5. inspection quality for the field; technical diagnostics for engineers, collapsed
// Screenings are remembered on this device only; the server never lists them publicly.

import { api, esc, fmt, pct, ago, waitForAnalysis } from "./lib.js?v=2.15";
import { OverlayViewer, wireColor, CLASS_LABEL } from "./viewer.js?v=2.15";
import { onStatus, startStatus } from "./status.js?v=2.15";
import { toast } from "./toast.js?v=2.15";

const $ = (s, r = document) => r.querySelector(s);
const form = $("#form");
const stages = { idle: $("#idle"), working: $("#working"), result: $("#result") };
const RECENT_KEY = "wf-field-recent";
let viewer = null, map = null, coords = null, previewUrl = null, mode = "checking", chosen = null, maxMb = 64;

const CLASS_SHORT = { "power-distribution-lines": "Power line", "telecom-cable": "Telecom cable", "abandoned-wire": "Abandoned wire" };
const CLASS_VAR = { "power-distribution-lines": "--cls-power", "telecom-cable": "--cls-telecom", "abandoned-wire": "--cls-dead" };
const VERDICT_CLASS = { "AT RISK - HIGH": "high", "AT RISK - MODERATE": "medium", "NOT AT RISK": "low", "INCONCLUSIVE - RETAKE PHOTO": "none" };
// Field labels for the screener's verdicts (display only; the API verdicts are unchanged). A retake is its own
// state: an unreadable photo must never look like a clean pole.
const PILL = { "AT RISK - HIGH": "High risk found", "AT RISK - MODERATE": "Moderate risk found", "NOT AT RISK": "No high risk found",
  "INCONCLUSIVE - RETAKE PHOTO": "Photo not clear enough" };

function show(name) {
  for (const [k, el] of Object.entries(stages)) el.hidden = k !== name;
  $("#main").focus({ preventScroll: true });
  scrollTo({ top: 0 });
}

// ------------------------------------------------------------------ server state (not shown; it only gates the form)
startStatus({ retryFailed: false });
onStatus((s) => {
  mode = s.mode;
  document.documentElement.dataset.server = s.mode; // read by scripts/ui_check.py; never displayed
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
  show("result");  // visible first: Leaflet and the canvas measure their containers on creation
  renderResult(r);
}

// ------------------------------------------------------------------ result model (v2.15: written for non-specialists)
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const group = (w) => w.level || "none";
const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const list = (xs) => (xs.length <= 1 ? String(xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
const pc = (x, d = 1) => `${(x * 100).toFixed(d)}%`;
function sagFactors(r) { return r.hazard?.factors?.sag || {}; }
const SECTION = (ids) => `${ids.length === 1 ? "section" : "sections"} ${list(ids.map((i) => `<button type="button" class="fx-ref" data-goto="${i}">${i}</button>`))}`;
const CONDITION = { high: "High risk", medium: "Moderate risk", low: "Low risk", none: "Not assessed" };
const SCORE_HELP = "Sag score, 0 to 10: how far the wire hangs compared with the sag allowed for its type. "
  + "10 means it hangs as low as the allowance or lower. Low below 3, moderate 3 to 6, high above 6.";

/** One plain sentence on what the photo shows for this wire section. Measurements only; never a guess. */
function finding(w, short = false) {
  const veg = w.veg_clearance;
  const vegTxt = veg ? (veg.gap_px === 0 ? (short ? "; vegetation touching" : "; vegetation is touching it")
    : veg.gap_px <= 15 ? (short ? "; vegetation close" : "; vegetation is close to it") : "") : "";
  const edge = (w.flags || []).includes("truncated") ? (short ? "; runs past photo edge" : "; it runs past the edge of the photo, so the full span may not be shown") : "";
  if (short && w.status === "scored") {
    return `${{ high: "Hangs low", medium: "Some sag", low: "Normal sag" }[w.level]} (${pc(w.sag_ratio)})${vegTxt}${edge}`;
  }
  if (short && w.status !== "scored") {
    const why = { chord_below_precision_gate: "Too small or far to measure", too_short: "Too small or far to measure",
      residual_too_large: "Overlaps other wires", non_positive_a: "Overlaps other wires", empty_mask: "Too faint to trace" }[w.reason_code];
    return (w.status === "service_drop" ? "Drop wire, not checked for sag" : why || "Could not be measured") + vegTxt + edge;
  }
  if (w.status === "scored") {
    const s = pc(w.sag_ratio), lim = w.delta_lim ? pc(w.delta_lim) : null;
    const base = { high: `Hangs low: sags ${s} of its length${lim ? ` (allowed: ${lim})` : ""}`,
      medium: `Some sag: ${s} of its length${lim ? ` (allowed: ${lim})` : ""}`,
      low: `Sag within the normal range (${s} of its length)` }[w.level];
    return base + vegTxt + edge;
  }
  if (w.status === "service_drop") return "Runs steeply down, likely a drop wire to a building; drop wires are not checked for sag" + vegTxt;
  const why = {
    chord_below_precision_gate: "Too small or too far away in the photo to measure",
    too_short: "Too small or too far away in the photo to measure",
    residual_too_large: "Overlaps or crosses other wires, so its shape could not be separated",
    non_positive_a: "Overlaps or crosses other wires, so its shape could not be separated",
    empty_mask: "Too faint to trace",
  }[w.reason_code] || "Its shape could not be measured from this photo";
  return why + vegTxt + edge;
}

/** Photo limitations: only those the result actually shows, each with what would help. */
function limitations(r) {
  const out = [];
  const ws = r.wires;
  const short = Math.min(r.image.width, r.image.height);
  const ids = (pred) => ws.filter(pred).map((w) => w.index).sort((a, b) => a - b);
  const small = ids((w) => ["chord_below_precision_gate", "too_short"].includes(w.reason_code));
  const overlap = ids((w) => ["residual_too_large", "non_positive_a"].includes(w.reason_code));
  const other = ids((w) => w.status !== "scored" && w.status !== "service_drop" && !["chord_below_precision_gate", "too_short", "residual_too_large", "non_positive_a"].includes(w.reason_code));
  const edge = ids((w) => (w.flags || []).includes("truncated"));
  const est = ids((w) => (w.provenance?.centerline_inferred_share ?? 0) >= 0.05);
  const drops = ids((w) => w.status === "service_drop");
  if (small.length) out.push({ key: "small", ids: small, text: `${plural(small.length, "wire section")} ${small.length === 1 ? "is" : "are"} too small or too far away in the photo to measure.`,
    help: short < 1000 ? `Use the original, full-resolution photo (this one is ${r.image.width} × ${r.image.height} pixels), or move closer.` : "Move closer or zoom in so these wires appear longer." });
  if (overlap.length) out.push({ key: "overlap", ids: overlap, text: `${plural(overlap.length, "wire section")} ${overlap.length === 1 ? "overlaps" : "overlap"} or ${overlap.length === 1 ? "crosses" : "cross"} other wires, so ${overlap.length === 1 ? "its" : "their"} shape could not be separated.`,
    help: "Take another photo from a few steps along the street, so the wires separate." });
  if (other.length) out.push({ key: "other", ids: other, text: `${plural(other.length, "wire section")} could not be measured from this photo.`, help: "A clearer, closer photo may help." });
  if (edge.length) out.push({ key: "edge", ids: edge, text: `${plural(edge.length, "wire section")} ${edge.length === 1 ? "continues" : "continue"} past the edge of the photo, so the full span is not shown.`,
    help: "Step back or use a wider angle so each wire is visible from pole to pole." });
  if (est.length) out.push({ key: "estimated", ids: est, text: `Parts of ${plural(est.length, "wire section")} were hidden or too faint to see. The gaps were filled by estimation and are shown dashed on the photo.`,
    help: "A photo with fewer obstructions in front of the wires may help." });
  const q = r.quality || {};
  if (q.grade && q.grade !== "A" && q.reason) {
    const m = [
      [/^The photo is blank/, "The photo appears blank or uniform.", "Retake the photo with the pole and its wires in view."],
      [/^Photo resolution too low/, "The photo is too small to see thin wires.", "Use the original, full-resolution photo, or move closer."],
      [/glare/i, "Bright sky glare makes the wires hard to see.", "Take the photo with the sun behind you or to one side."],
      [/^Photo too dark|^Low light/, "The photo is dark, so the wires stand out poorly.", "Take the photo in daylight or better lighting."],
      [/^Almost no wire pixels/, "Very few wires are visible in the photo.", "Frame the pole so its wires fill more of the photo."],
      [/^Camera shake/, "The photo is blurred by camera movement.", "Hold the camera steady when taking the photo."],
      [/out of focus/, "The photo is out of focus.", "Tap the screen on the wires to focus before taking the photo."],
    ].find(([re]) => re.test(q.reason));
    const [, text, help] = m || [null, "A busy background or low contrast makes some wires hard to tell apart.", "A photo against open sky helps."];
    out.push({ key: "quality", ids: [], text, help });
  }
  if (drops.length) out.push({ key: "drops", ids: drops, text: `${plural(drops.length, "wire")} ${drops.length === 1 ? "runs" : "run"} steeply down to a building (drop ${drops.length === 1 ? "wire" : "wires"}). These are not checked for sag.`, help: "" });
  return out;
}

/** The overall assessment. Mirrors the server's verdict; never presents a screening as a safety certification. */
function assessment(r) {
  const hz = r.hazard;
  const total = r.wires.length;
  const scored = r.wires.filter((w) => w.status === "scored");
  const nHigh = scored.filter((w) => w.level === "high").length;
  const nMed = scored.filter((w) => w.level === "medium").length;
  const notAssessed = total - scored.length;
  const veg = hz?.factors?.vegetation || [];
  const vegPower = veg.filter((v) => v.class === "power-distribution-lines" && v.gap_px === 0).map((v) => v.span);
  const vegOther = veg.filter((v) => v.class !== "power-distribution-lines" && v.gap_px === 0).map((v) => v.span);
  const coverage = total ? `${scored.length} of ${total} wire sections could be assessed${notAssessed ? `; ${notAssessed} could not (see Photo limitations)` : ""}.` : "";
  if (r.status === "error") return { cls: "none", pill: "Not screened", title: "This photo could not be screened.", lines: ["Try the photo again. If it fails again, contact your administrator."], coverage: "" };
  const v = hz?.verdict || "";
  const sagBit = nHigh ? `${plural(nHigh, "wire section")} ${nHigh === 1 ? "hangs" : "hang"} noticeably low` : "";
  if (v.startsWith("INCONCLUSIVE") || (!hz && !scored.length)) {
    return { cls: "none", pill: "Photo not clear enough", title: "This photo is not clear enough for a reliable screening.",
      lines: [`${r.quality?.reason ? `${limitations(r).find((l) => l.key === "quality")?.text || ""} ` : ""}A clearer photo is needed before anything can be said about this pole.`.trim()], coverage };
  }
  if (v === "AT RISK - HIGH") {
    const bits = [sagBit, vegPower.length ? `vegetation is touching power line ${SECTION(vegPower)}` : ""].filter(Boolean);
    return { cls: "high", pill: "High risk found", title: "Some wires on this pole should be checked soon.",
      lines: [`${bits.join(", and ").replace(/^./, (c) => c.toUpperCase())}.`], coverage };
  }
  if (v === "AT RISK - MODERATE") {
    const bits = [nHigh ? sagBit : nMed ? `${plural(nMed, "wire section")} ${nMed === 1 ? "shows" : "show"} some sag` : "",
      vegOther.length ? `vegetation is touching ${SECTION(vegOther)}` : "", vegPower.length ? `vegetation is close to or touching power line ${SECTION(vegPower)}` : ""].filter(Boolean);
    return { cls: "medium", pill: "Moderate risk found", title: "Some wires on this pole should be looked at.",
      lines: [bits.length ? `${bits.join("; ").replace(/^./, (c) => c.toUpperCase())}.` : "See the wire sections below."], coverage };
  }
  // NOT AT RISK: nothing corroborated; individual readings are still shown, and the screening is not a clearance
  const lines = [];
  if (nHigh || nMed) {
    lines.push(`${plural(nHigh + nMed, "wire section")} ${nHigh + nMed === 1 ? "reads" : "read"} as ${nHigh ? "high" : "moderate"} risk on its own. One or two readings like this can come from the photo rather than the wire, so the pole as a whole was not flagged, but ${nHigh + nMed === 1 ? "that section is" : "those sections are"} worth a look.`);
  }
  lines.push("This is a screening from one photo, not a safety inspection.");
  return { cls: "low", pill: "No high risk found", title: scored.length ? "No measured wire on this pole was flagged." : "Nothing could be measured in this photo.", lines, coverage };
}

/** What to do next, built from this result's findings. Never asks anyone to handle a wire. */
function recommendations(r, a, lims) {
  const out = [];
  const by = (lv) => r.wires.filter((w) => w.level === lv).sort((x, y) => y.crs - x.crs).map((w) => w.index);
  const high = by("high"), med = by("medium");
  const veg = r.hazard?.factors?.vegetation || [];
  const vegPower = veg.filter((v) => v.class === "power-distribution-lines" && v.gap_px === 0).map((v) => v.span);
  const vegOther = veg.filter((v) => v.class !== "power-distribution-lines" && v.gap_px === 0).map((v) => v.span);
  if (high.length) out.push(`Have wire ${SECTION(high)} checked by your electric utility or a qualified electrical professional${high.length > 1 ? `, starting with section ${high[0]}` : ""}.`);
  if (vegPower.length) out.push(`Report the vegetation touching power line ${SECTION(vegPower)} to your electric utility or barangay. Do not trim branches near the wires yourself.`);
  if (med.length) out.push(`Include wire ${SECTION(med)} in the next routine inspection.`);
  if (vegOther.length && !vegPower.length) out.push(`Vegetation is touching cable ${SECTION(vegOther)}. Let the cable operator or barangay know.`);
  const unassessed = lims.filter((l) => ["small", "overlap", "other", "edge", "quality"].includes(l.key));
  if (a.cls === "none") out.unshift(`Retake the photo. ${unassessed.map((l) => l.help).filter(Boolean)[0] || "Frame the pole and the full wires on both sides, in good light."}`);
  else if (unassessed.length) out.push(`Some sections could not be assessed. A clearer photo may help: ${unassessed.map((l) => l.help).filter(Boolean)[0]?.replace(/^./, (c) => c.toLowerCase()) || "frame the full wires in good light."}`);
  if (high.length || med.length || vegPower.length) out.push("Download the PDF report to share with the inspector.");
  out.push("Do not touch, climb toward or try to move any overhead wire. Treat every wire as live.");
  return out;
}

// ------------------------------------------------------------------ result: table, detail, diagnostics
function conditionCell(w) {
  const g = group(w);
  return `<span class="fx-risk ${g}"><i></i>${CONDITION[g]}</span>`;
}

function spansTable(r) {
  const rank = { high: 0, medium: 1, low: 2, none: 3 };
  const rows = [...r.wires].sort((a, b) => rank[group(a)] - rank[group(b)] || (b.crs ?? -1) - (a.crs ?? -1) || a.index - b.index);
  const counts = Object.fromEntries(["high", "medium", "low", "none"].map((g) => [g, rows.filter((w) => group(w) === g).length]));
  const body = rows.map((w) => `<tr data-wire="${w.index}" data-group="${group(w)}" tabindex="0" aria-selected="false">
      <td class="id"><span class="fx-id" style="--wc:${wireColor(w)}">${w.index}</span></td>
      <td class="cls">${esc(CLASS_SHORT[w.class] || w.class)}</td>
      <td class="risk">${conditionCell(w)}</td>
      <td class="find" title="${esc(finding(w))}">${esc(finding(w, true))}</td></tr>`).join("");
  return { counts, html: rows.length ? `<table class="fx-spans"><thead><tr><th class="id">Wire</th><th>Type</th><th>Condition</th><th>Finding</th></tr></thead>
      <tbody>${body}</tbody></table>` : `<p class="fx-empty">No wire was detected in this photo.</p>` };
}

/** The selected wire section: the plain reading first, the measurement detail folded underneath. */
function spanDetail(r, w) {
  if (!w) return `<p class="fx-detail-empty">Select a wire section in the list or on the photo to see it highlighted and read its details.</p>`;
  const f = w.fit || {};
  const p = w.provenance;
  const veg = w.veg_clearance;
  const kv = (pairs) => `<dl class="fx-dl">${pairs.filter(([, v]) => v != null && v !== "").map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>`;
  const scored = w.status === "scored";
  return `<header><span class="fx-id" style="--wc:${wireColor(w)}">${w.index}</span>
      <b>${esc(CLASS_LABEL[w.class] || w.class)}</b>${conditionCell(w)}</header>
    <p class="fx-finding">${esc(finding(w))}.</p>
    ${w.action && !scored ? `<p class="fx-do"><b>What would help:</b> ${esc(w.action)}.</p>` : ""}
    ${p && p.inferred_px ? `<p class="fx-note">About ${pc(p.centerline_inferred_share, 0)} of this wire's traced length was hidden or too faint and was filled by estimation (dashed on the photo).</p>` : ""}
    ${scored ? kv([
      ["Sag", `${pc(w.sag_ratio)} of its length`],
      ["Allowed sag", w.delta_lim ? pc(w.delta_lim) : null],
      [`<abbr title="${esc(SCORE_HELP)}">Sag score</abbr>`, `${fmt(w.crs, 1)} of 10`],
      ["Vegetation", veg ? (veg.gap_px === 0 ? "Touching" : `${veg.gap_px <= 15 ? "Close" : "Nearby"} (${veg.gap_px.toFixed(0)} px in the photo)`) : "None nearby"],
    ]) : kv([["Vegetation", veg ? (veg.gap_px === 0 ? "Touching" : `${veg.gap_px <= 15 ? "Close" : "Nearby"} (${veg.gap_px.toFixed(0)} px in the photo)`) : "None nearby"]])}
    <details class="fx-more"><summary>Measurement details</summary>${kv([
      ["Sag score (CRS)", w.crs == null ? "not computed" : fmt(w.crs, 3)],
      ["Chord L", f.chord_px == null ? null : `${fmt(f.chord_px, 1)} px`],
      ["Sag δ", w.sag_px == null || !scored ? null : `${fmt(w.sag_px, 2)} px`],
      ["δ / L", w.sag_ratio == null ? null : pc(w.sag_ratio, 3)],
      ["δ_lim", w.delta_lim == null ? null : pc(w.delta_lim, 2)],
      ["Fit residual", f.rms_residual_px == null ? null : `${fmt(f.rms_residual_px, 2)} px`],
      ["Inclination", f.inclination_deg == null ? null : `${fmt(f.inclination_deg, 1)}°`],
      ["Mean class probability", fmt(w.confidence, 3)],
      ["Inferred share", p ? pc(p.centerline_inferred_share, 1) : "n/a (reference polygons)"],
      ["Status code", esc(w.reason_code ? `${w.status} · ${w.reason_code}` : w.status)],
      ["Reason (technical)", w.reason ? esc(w.reason) : null],
    ])}</details>`;
}

/** Everything technical, grouped, for administrators and researchers. Collapsed by default; nothing is removed. */
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
      ["Polarity", r.flags.includes("polarity_inverted") ? "inverted (night)" : "as photographed"],
      ["Enhancement", esc((r.segmentation_input?.enhancement || []).join(", ") || "none")]])}</section>
    <section><h3>Image quality</h3>${kv([["Grade", esc(q.grade)], ["Reliability R", fmt(q.reliability, 3)], ["Reason", esc(q.reason)],
      ["Entropy U", fmt(unc.entropy, 3)], ["Flip disagreement D", fmt(unc.disagreement, 3)], ["Mean L*", fmt(iq.mean_l, 1)], ["Contrast", fmt(iq.contrast, 1)],
      ["Near-white share", iq.bright_share == null ? null : pct(iq.bright_share, 0)], ["Thin-line contrast", fmt(iq.line_contrast, 1)],
      ["Sharpness", fmt(iq.sharpness, 0)], ["Noise σ", fmt(iq.noise_sigma, 1)], ["Motion coherence", fmt(iq.motion_coherence, 2)],
      ["Resolution", `${r.image.width} × ${r.image.height}`]])}</section>
    <section><h3>Segmentation metrics</h3>${wm ? kv([["Wire pixels (model)", wm.wire_pixels?.toLocaleString()], ["Inside wire sections", wm.span_coverage == null ? "—" : pct(wm.span_coverage, 0)],
      ["Fragments outside sections", wm.wire_pixels_in_spans == null ? null : `${(wm.wire_pixels - wm.wire_pixels_in_spans).toLocaleString()} px`],
      ["Inferred gap pixels", `${inf.toLocaleString()} px`],
      ["Power px", wm.pixels?.["power-distribution-lines"]?.toLocaleString()], ["Telecom px", wm.pixels?.["telecom-cable"]?.toLocaleString()],
      ["Abandoned px", wm.pixels?.["abandoned-wire"]?.toLocaleString()], ["Vegetation px", wm.pixels?.Vegetation?.toLocaleString()]]) : "<p>No model mask (reference polygons).</p>"}
      <h4>Ground structures ignored</h4>${sup.length ? `<ul class="fx-suppressed">${sup.map((s) => `<li>${esc(CLASS_SHORT[s.wire_class] || s.wire_class)} · ${pct(s.ground_share, 0)} on road / pavement · x ${Math.round(s.bbox[0])}–${Math.round(s.bbox[2])}, y ${Math.round(s.bbox[1])}–${Math.round(s.bbox[3])}</li>`).join("")}</ul>
      <p>${esc(sup[0].reason)}. These traces are never scored.</p>` : "<p>None: no wire trace lay on the road or pavement.</p>"}</section>
    <section><h3>Statistical analysis</h3>${rule.sag_rule === "corroborated" && sag.scored_spans != null ? `
      <p>High band: <b class="fx-num">${(sag.high || []).length}</b> of <b class="fx-num">${sag.scored_spans}</b> measured spans; <b class="fx-num">${sag.k_high}</b> needed above the ${(rule.p0_high * 100).toFixed(1)}% per-span noise floor (one-sided binomial, α = ${rule.alpha}).</p>
      <p>Medium or above: <b class="fx-num">${(sag.high || []).length + (sag.medium || []).length}</b>; <b class="fx-num">${sag.k_medium}</b> needed (noise floor ${(rule.p0_medium * 100).toFixed(1)}%).</p>`
      : `<p>Sag rule: ${esc(rule.sag_rule || "—")}.</p>`}
      <p>Server verdict: <b>${esc(r.hazard?.verdict || "—")}</b></p>
      <ul class="fx-notes">${(r.hazard?.reasons || []).map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>
    <section><h3>Processing performance</h3>${kv([["Total", tel.inference_s != null ? `${fmt(tel.inference_s, 2)} s` : null],
      ["Preprocess", ms("preprocess")], ["Segmentation", ms("segmentation")], ["Geometry and risk", ms("geometry_and_risk")]])}</section>
    <section><h3>Configuration and execution</h3>${kv([["Config fingerprint", esc(r.parameters?.fingerprint)], ["Current config", esc(r.parameters?.current_fingerprint)],
      ["Chord gate", `${fmt(r.parameters?.min_chord_px, 0)} px`], ["Provisional blocks", esc((r.parameters?.provisional || []).join(", "))],
      ["Pole code", esc(r.pole.code)], ["Analysis id", `<span class="fx-num">${esc(r.id)}</span>`], ["Submitted via", esc(r.submitted_via)],
      ["Flags", esc((r.flags || []).join(", ") || "none")]])}</section>`;
}

// ------------------------------------------------------------------ result view
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
  spans: { masks: 1, inferred: 1, catenary: 1, sag: 1, ends: 1, labels: 1 },
  mask: { wiremask: 1 },
  overlay: { wiremask: 1, masks: 1, inferred: 1, catenary: 1, sag: 1, ends: 1, labels: 1 },
};
const LAYERS = [
  ["wiremask", "Model mask", "Every pixel the model marked as wire, including small pieces that are not part of any wire section"],
  ["masks", "Wire sections", "The detected pixels of each wire section, coloured by condition"],
  ["inferred", "Estimated gaps", "Short gaps where a wire was hidden or faint and was joined by estimation (dashed); not detected"],
  ["skeleton", "Centre lines", "The traced centre line of each wire section"],
  ["catenary", "Fitted curve", "The hanging curve fitted to each measured wire section"],
  ["sag", "Sag measurement", "For the selected section: the straight line between its ends and how far the wire hangs below it"],
  ["ends", "Section ends", "Where each traced wire section starts and ends in the photo (not where it is attached to a pole)"],
  ["labels", "Section numbers", ""],
];

function renderResult(r) {
  const a = assessment(r);
  const lims = limitations(r);
  const recs = recommendations(r, a, lims);
  const loc = r.pole.location;
  const tbl = spansTable(r);
  const scored = r.wires.filter((w) => w.crs != null);
  const worst = scored.reduce((x, y) => (!x || y.crs > x.crs ? y : x), null);
  const hasMask = !!r.wire_mask;
  const hasVeg = (r.vegetation || []).length > 0;
  const when = new Date(r.created_at);
  const c = tbl.counts;
  const insp = r.inspection || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");

  stages.result.innerHTML = `
    <header class="fx-insp">
      <div class="fx-insp-id">
        <h1>${esc(insp.name || r.image.original_filename || `Pole ${r.pole.code}`)}</h1>
        <p>${place ? `<span>${esc(place)}</span>` : ""}
          <time datetime="${esc(r.created_at)}">${esc(when.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }))}, ${esc(when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }))}</time>
          ${insp.name ? `<span class="fx-file" title="Original file name">${esc(r.image.original_filename || "")}</span>` : ""}</p>
      </div>
      <div class="fx-insp-actions">
        ${r.status !== "error" ? `<a class="btn" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener" id="pdf">${ICON.pdf}PDF report</a>` : ""}
        <button class="btn primary" id="again" type="button">${ICON.plus}New inspection</button>
      </div>
    </header>

    <section class="fx-summary ${a.cls}" aria-label="Overall assessment">
      <div class="fx-outcome">
        <span class="fx-pill ${a.cls}" id="verdict-pill">${esc(a.pill)}</span>
        <h2>${esc(a.title)}</h2>
        ${a.lines.map((l) => `<p>${l}</p>`).join("")}
        ${a.coverage ? `<p class="fx-coverage">${esc(a.coverage)}</p>` : ""}
      </div>
      <dl class="fx-counts" aria-label="Wire sections by risk">
        ${[["high", "High risk"], ["medium", "Moderate risk"], ["low", "Low risk"]].map(([g, label]) => `<div><dt><i class="${g}"></i>${label}</dt><dd><button type="button" class="fx-count ${g}" data-filter-go="${g}" ${c[g] ? "" : "disabled"} aria-label="Show ${label.toLowerCase()} wire sections">${c[g]}</button></dd></div>`).join("")}
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
          <span data-legend="spans"><i style="--c:${css("--risk-high")}"></i>High risk</span><span data-legend="spans"><i style="--c:${css("--risk-med")}"></i>Moderate</span>
          <span data-legend="spans"><i style="--c:${css("--risk-low")}"></i>Low</span><span data-legend="spans"><i style="--c:${css("--risk-none")}"></i>Not assessed</span>
          <span data-legend="inferred"><i class="dash"></i>Estimated, not detected</span>
          <span data-legend="mask" hidden><i style="--c:rgb(25,113,194)"></i>Power line pixels</span><span data-legend="mask" hidden><i style="--c:rgb(156,54,181)"></i>Telecom pixels</span><span data-legend="mask" hidden><i style="--c:rgb(232,89,12)"></i>Abandoned wire pixels</span>
          ${hasVeg ? `<span data-legend="veg"><i style="--c:${css("--cls-veg")}"></i>Vegetation</span>` : ""}
          <span class="fx-legend-hint">Scroll to zoom · drag to move · double-click to fit</span>
        </div>
      </section>

      <aside class="fx-side" aria-label="Wire sections">
        <section class="fx-detail" id="span-detail" aria-live="polite">${spanDetail(r, null)}</section>
        <section class="fx-spans-panel">
          <div class="fx-table-head"><h2>Wire sections <span class="fx-num">${r.wires.length}</span></h2>
            <div class="fx-filters" role="group" aria-label="Show">
              <button type="button" data-filter="all" aria-pressed="true">All</button>
              ${[["high", "High"], ["medium", "Moderate"], ["low", "Low"], ["none", "Not assessed"]].map(([g, label]) => `<button type="button" data-filter="${g}" aria-pressed="false" ${c[g] ? "" : "disabled"}>${label} <b class="fx-num">${c[g]}</b></button>`).join("")}
            </div></div>
          <div class="fx-scroll" id="fx-scroll">${tbl.html}<p class="fx-empty" id="filter-empty" hidden>No wire sections in this group.</p></div>
        </section>
      </aside>
    </div>

    <div class="fx-guide">
      <section class="fx-recs" aria-labelledby="recs-h"><h2 id="recs-h">What to do next</h2>
        <ol id="next-action">${recs.map((x) => `<li><span>${x}</span></li>`).join("")}</ol></section>
      <section class="fx-limits" id="limits" aria-labelledby="lim-h"><h2 id="lim-h">Photo limitations</h2>
        ${lims.length ? `<ul>${lims.map((l) => `<li data-limit="${l.key}"><p>${esc(l.text)}${l.ids.length ? ` <span class="fx-lim-ids">(${SECTION(l.ids)})</span>` : ""}</p>${l.help ? `<p class="fx-help">${esc(l.help)}</p>` : ""}</li>`).join("")}</ul>`
          : `<p class="fx-none">None found. Every detected wire section could be assessed from this photo.</p>`}
      </section>
    </div>

    <section class="fx-drawer" id="diag">
      <button class="fx-drawer-btn" type="button" aria-expanded="false" aria-controls="diag-body">
        <b>Technical diagnostics</b><span>For administrators and researchers: model, image quality, segmentation, statistics, timings, configuration</span>
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg></button>
      <div class="fx-drawer-body" id="diag-body" role="region" aria-label="Technical diagnostics"><div><div class="fx-drawer-inner">${diagnostics(r)}</div></div></div>
    </section>

    ${loc ? `<section class="fx-map"><div id="fs-map" role="region" aria-label="Location of this inspection"></div></section>` : ""}
    ${r.reviews?.length ? `<p class="fx-muted">Reviewed by ${esc(r.reviews.at(-1).by)}${r.reviews.at(-1).override_level ? ` · level set to ${r.reviews.at(-1).override_level}` : ""}</p>` : ""}`;

  const root = stages.result;
  const table = root.querySelector(".fx-spans");
  const detail = root.querySelector("#span-detail");

  // ---------------- viewer
  viewer?.destroy();
  viewer = new OverlayViewer($("#fs-canvas"), { onSelect: (i) => mark(i, true) });
  window.wfViewer = viewer; // read-only handle for automated UI checks (scripts/ui_check.py)
  const zoomOut = root.querySelector("#zoom-pct");
  $("#fs-canvas").addEventListener("zoom", (e) => { zoomOut.textContent = `${Math.round(e.detail * 100)}%`; });
  viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => {
    if (worst && worst.level !== "low") { viewer.selected = worst.index; viewer.draw(); mark(worst.index, false); }
    if (hasMask) viewer.setWireMask(r.wire_mask.url);
  }).catch(() => toast.error("Photo could not be displayed"));

  const mark = (i, scroll) => {
    table?.querySelectorAll("tr[data-wire]").forEach((tr) => {
      const on = Number(tr.dataset.wire) === i;
      tr.setAttribute("aria-selected", String(on));
      if (on && scroll && !tr.hidden) tr.scrollIntoView({ block: "nearest", behavior: "smooth" });
    });
    detail.innerHTML = spanDetail(r, i == null ? null : r.wires.find((w) => w.index === i));
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
    const first = table?.querySelector("tr[data-wire]:not([hidden])");
    if (first) select(Number(first.dataset.wire), false);
    root.querySelector(".fx-spans-panel").scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));

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
      title: insp.name || r.pole.code }).addTo(map);
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
