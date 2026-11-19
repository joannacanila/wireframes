// Field screener (v2.14). Result screen, in reading order:
//   1. inspection header: pole, time, file, PDF report, new inspection
//   2. outcome strip: verdict, physical hazards, next step; counts per risk level and the worst span (click to filter / select)
//   3. the photo (largest element): Original / Spans / Mask / Overlay presets, independent layers (segmentation pixels,
//      span masks, inferred gaps, centerlines, catenary, chord and sag, span ends), opacity, vegetation, zoom, fit, reset
//   4. beside it: the selected span in full, and the span table with risk filters (row <-> photo selection kept in sync)
//   5. inspection quality for the field; technical diagnostics for engineers, collapsed
// Screenings are remembered on this device only; the server never lists them publicly.

import { api, esc, ago, waitForAnalysis } from "./lib.js?v=2.23";
import { classLabel } from "./viewer.js?v=2.35";
import { resultViewHtml, bindResultView } from "./result-view.js?v=2.36";
import { checkPhoto, fmtSize, HEIC, DEFAULT_MAX_MB } from "./upload-rules.js?v=2.30";
import { onStatus, startStatus } from "./status.js?v=2.23";
import { toast } from "./toast.js?v=2.23";
import { factOrder, rememberFact } from "./facts.js?v=2.34";
import { reverseLookup, locate, fixLabel, appendLocation } from "./location.js?v=2.34";

const $ = (s, r = document) => r.querySelector(s);
const form = $("#form");
const stages = { idle: $("#idle"), working: $("#working"), result: $("#result") };
const RECENT_KEY = "wf-field-recent";
let viewer = null, map = null, coords = null, previewUrl = null, mode = "checking", chosen = null, maxMb = DEFAULT_MAX_MB;
let retakeOf = null; // v2.17: id of the analysis the next upload replaces (set by "Retake photo")

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

// ------------------------------------------------------------------ theme (S14: light by default, high contrast; dark one tap away)
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
// S15: the list shows the latest RECENT_SHOWN; "View all" opens the rest. Statuses come from the server's result
// (overview.citizen.status_label): Screened, Needs review, Low visibility, Processing or Processing failed.
const RECENT_SHOWN = 5;
const STATUS_TONE = { "Needs review": "medium", "Processing failed": "high", "Low visibility": "none", Processing: "none", Screened: "low" };
let recentAll = false;
function recentName(r) {
  if (r.name) return r.name;  // the person's own label; a camera file name is never shown
  const brgy = (r.place || "").split(",")[0].trim();
  if (brgy) return `Pole check · ${brgy}`;
  const d = r.at ? new Date(r.at) : null;
  return d && !isNaN(d) ? `Pole check · ${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })}, ${d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}` : "Pole check";
}
function recentLabel(r) {
  if (r.label) return r.label;
  if (r.status === "error") return "Processing failed";
  if (r.status === "processing") return "Processing";
  return "";  // an entry saved before S15: refreshed from the server below, never guessed
}
function renderRecent() {
  const list = recent();
  $("#recent").hidden = !list.length;
  const shown = recentAll ? list : list.slice(0, RECENT_SHOWN);
  $("#recent-list").innerHTML = shown.map((r) => {
    const label = recentLabel(r);
    return `<li><a href="/workspace/result/${r.id}" data-id="${r.id}">
    <img src="/api/analysis/${r.id}/image" alt="" loading="lazy" onerror="this.closest('li').remove()">
    <span><b>${esc(recentName(r))}</b><small>${esc([r.place, ago(r.at)].filter(Boolean).join(" · "))}</small></span>
    ${label ? `<span class="fx-tag ${STATUS_TONE[label] || "none"}">${esc(label)}</span>` : ""}</a></li>`;
  }).join("");
  const more = $("#recent-more");
  more.hidden = list.length <= RECENT_SHOWN;
  more.textContent = recentAll ? "Show fewer" : `View all ${list.length}`;
  more.setAttribute("aria-expanded", String(recentAll));
  refreshRecentLabels(shown);
}
const refreshing = new Set();
async function refreshRecentLabels(items) {
  for (const it of items.filter((x) => !x.label && x.status !== "error" && !refreshing.has(x.id))) {
    refreshing.add(it.id);
    try {
      const r = await api(`/api/analysis/${it.id}/result`);
      const label = r.overview?.citizen?.status_label;
      if (!label) continue;
      const list = recent().map((x) => (x.id === it.id ? { ...x, label, status: r.status } : x));
      try { localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* private mode */ }
      const tag = document.querySelector(`#recent-list a[data-id="${it.id}"]`);
      if (tag && !tag.querySelector(".fx-tag")) tag.insertAdjacentHTML("beforeend", `<span class="fx-tag ${STATUS_TONE[label] || "none"}">${esc(label)}</span>`);
    } catch { /* gone or still processing: left without a status rather than guessed */ }
  }
}
$("#recent-more").addEventListener("click", () => { recentAll = !recentAll; renderRecent(); });
$("#recent-list").addEventListener("click", (e) => {
  const a = e.target.closest("a[data-id]"); if (!a) return;
  e.preventDefault(); openResult(a.dataset.id, true);
});
renderRecent();

// ------------------------------------------------------------------ form
// The same limits as the server (app/pipeline/acquisition.py, Settings.max_upload_mb); the server re-checks everything.
fetch("/api/config").then((r) => (r.ok ? r.json() : null)).then((c) => {
  // the analysis config does not carry the upload limit; keep the server default unless it is ever exposed
  if (c?.max_upload_mb) { maxMb = c.max_upload_mb; $("#max-mb").textContent = maxMb; }
}).catch(() => {});
const fileInput = form.elements.file;
function fileError(msg) { const e = $("#file-error"); e.textContent = msg || ""; e.hidden = !msg; }
// S17 (Part 4.1): a photo the browser cannot draw (HEIC / HEIF, most TIFF) is previewed from the server's decoder
// (POST /api/analysis/preview: the same decode, orientation and limits as the upload, nothing stored), so the
// preview shows exactly the pixels that will be analysed. The decoded copy is reused on the processing screen.
let previewSeq = 0;
async function serverPreview(file) {
  const seq = ++previewSeq, img = $("#preview");
  $("#drop").setAttribute("data-decoding", "");
  $("#file-meta").textContent = `${fmtSize(file.size)} · preparing preview`;
  const fd = new FormData(); fd.append("file", file);
  try {
    const r = await api("/api/analysis/preview", { method: "POST", body: fd });
    if (seq !== previewSeq || chosen !== file) return; // the photo was replaced meanwhile
    img.onerror = null;
    img.src = r.image;
    decodedPreview = r.image;
    $("#file-meta").textContent = `${r.width} × ${r.height} px · ${r.format || "image"} · ${fmtSize(file.size)}`;
  } catch (err) {
    if (seq !== previewSeq || chosen !== file) return;
    fileError(err.message || "This photo could not be read.");
    setFile(null);
  } finally {
    if (seq === previewSeq) $("#drop").removeAttribute("data-decoding");
  }
}
let decodedPreview = null;
function setFile(file) {
  fileError("");
  const bad = file && checkPhoto(file, maxMb); // S18: the same rules as the admin upload (upload-rules.js)
  if (bad) { fileError(bad); file = null; }
  decodedPreview = null;
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
    const heic = HEIC.test(file.name) || /hei[cf]/.test(file.type);
    img.onerror = () => serverPreview(file);
    if (heic) serverPreview(file); // S17: never rely on the browser's own HEIC support
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

// ------------------------------------------------------------------ location (S15)
// Asked for only when the button is pressed; never needed to screen a photo; nothing is guessed. Coordinates are kept
// even when the address lookup fails, and a filled-in City / Barangay is never overwritten.
let place = null; // { city, barangay } from the address lookup, for the summary chip
function locateStatus(text, cls = "") { const s = $("#locate-status"); s.textContent = text; s.className = `fx-locate-status ${cls}`; }
function locBusy(on) { const b = $("#locate"); b.disabled = on; b.querySelector("span").textContent = on ? "Finding your location…" : coords ? "Update location" : "Use my location"; }
function syncLocChip() {
  const city = form.elements.city.value.trim(), brgy = form.elements.barangay.value.trim();
  const where = [brgy, city].filter(Boolean).join(", ");
  const text = where || (coords ? fixLabel(coords) : "");
  $("#loc-chip").hidden = !coords || $("#more").open;
  $("#loc-chip-text").textContent = text;
  $("#more-sum").textContent = !$("#more").open && (where || form.elements.inspection_name.value.trim())
    ? ` · ${[form.elements.inspection_name.value.trim(), where].filter(Boolean).join(" · ")}` : "";
}

$("#locate").addEventListener("click", async () => {
  locBusy(true); locateStatus("Waiting for your browser…");
  try { coords = await locate((t) => locateStatus(t)); }
  catch (err) { coords = null; locBusy(false); locateStatus(err.message, "err"); syncLocChip(); return; }
  locateStatus(`Got ${fixLabel(coords)}. Looking up the address…`);
  place = await reverseLookup(coords);
  locBusy(false);
  if (place) {
    if (!form.elements.city.value.trim() && place.city) form.elements.city.value = place.city;
    if (!form.elements.barangay.value.trim() && place.barangay) form.elements.barangay.value = place.barangay;
    locateStatus(`Location saved with this inspection: ${fixLabel(coords)}. Check the city and barangay below.`, "ok");
  } else {
    locateStatus(`Location saved with this inspection: ${fixLabel(coords)}. The address could not be looked up, so type the city and barangay if you know them.`, "ok");
  }
  syncLocChip();
});
$("#loc-remove").addEventListener("click", () => {
  coords = null; place = null; locBusy(false); locateStatus("Location removed. It will not be saved with this inspection.");
  syncLocChip();
});
$("#loc-edit").addEventListener("click", () => { $("#more").open = true; syncLocChip(); form.elements.city.focus(); });
$("#more").addEventListener("toggle", syncLocChip);
for (const k of ["city", "barangay", "inspection_name"]) form.elements[k].addEventListener("input", syncLocChip);

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (!chosen) { fileError("Choose a photo first."); return; }
  const fd = new FormData();
  fd.append("file", chosen);
  for (const k of ["inspection_name", "city", "barangay"]) { const v = form.elements[k].value.trim(); if (v) fd.append(k, v); }
  const labels = form.elements.labels.files[0];
  if (labels) fd.append("labels", labels);
  appendLocation(fd, coords, place);
  if (retakeOf) fd.append("retake_of", retakeOf);
  retakeOf = null; $("#retake-note")?.remove();
  run(fd, decodedPreview || previewUrl);
});

// ------------------------------------------------------------------ run (S15 processing screen)
// Each step changes only when the server reports it (upload accepted, queued with its position, running, finished);
// nothing is shown as done before it is, and there is no percentage to invent.
const SLOW_MS = 90000, GIVE_UP_MS = 10 * 60000;
// S18: facts shown while a photo is processed come from a reviewed, sourced pool (facts.js). Each processing session
// gets its own shuffled order and shows each fact at most once until all have been shown; with reduced motion, one fact.
let dykT = 0;
function dykStart() {
  const el = $("#dyk"), src = $("#dyk-src"); if (!el) return;
  let order = factOrder(), i = 0;
  const show = () => {
    const f = order[i];
    el.textContent = f.text; if (src) src.textContent = `Source: ${f.source}`;
    rememberFact(f);
  };
  clearInterval(dykT);
  show();
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  dykT = setInterval(() => {
    if ($("#dyk")?.offsetParent === null) { clearInterval(dykT); return; }
    i += 1;
    if (i >= order.length) { order = factOrder(); i = 0; } // every fact shown once: a new shuffle, not a loop
    el.classList.remove("in"); void el.offsetWidth; show(); el.classList.add("in");
  }, 9000);
}

function step(name, state) { const el = $(`[data-step="${name}"]`); if (el) el.dataset.state = state; }
function procNote(text) { $("#proc-note").textContent = text; }
function procActions(html) { const a = $("#proc-actions"); a.innerHTML = html; a.hidden = !html; }
function resetSteps() {
  dykStart();
  ["upload", "queue", "analyze", "result"].forEach((x) => step(x, "todo"));
  $('[data-step="queue"]').hidden = true; procActions(""); $("#proc-title").textContent = "Checking your photo";
  procNote("This usually takes under a minute.");
}
function onTick(s) {
  if (s.processing === "pending") {
    const q = $('[data-step="queue"]'); q.hidden = false; step("queue", "doing"); step("analyze", "todo");
    $("#queue-note").textContent = s.queue_position ? (s.queue_position === 1 ? "Next in line" : `${s.queue_position - 1} photo${s.queue_position > 2 ? "s" : ""} ahead`) : "";
  } else if (s.status === "processing") {
    if (!$('[data-step="queue"]').hidden) step("queue", "done");
    step("analyze", "doing");
  }
}

async function track(id) {
  const ctl = new AbortController();
  const slow = setTimeout(() => {
    procNote("This is taking longer than usual. You can keep waiting, or leave: the result will be in Recent on this device.");
  }, SLOW_MS);
  const giveUp = setTimeout(() => ctl.abort(), GIVE_UP_MS);
  try { return await waitForAnalysis(id, { signal: ctl.signal, onTick }); }
  finally { clearTimeout(slow); clearTimeout(giveUp); }
}

function stalled(id, err) {
  $("#proc-title").textContent = "We couldn't get your result yet";
  procNote(err?.name === "AbortError"
    ? "The photo is still being checked, or the server stopped responding. Your photo was received and nothing is lost."
    : `The connection to the server was interrupted (${err?.message || "no response"}). Your photo was received.`);
  procActions(`<button class="btn primary" type="button" data-proc="again">Check again</button>
    <button class="btn" type="button" data-proc="new">Screen another photo</button>`);
  $("#proc-actions").onclick = (e) => {
    const b = e.target.closest("[data-proc]"); if (!b) return;
    if (b.dataset.proc === "new") reset(); else openResult(id, false);
  };
}

async function run(fd, imgUrl) {
  // S16: a photo this browser cannot draw (HEIC in most browsers, TIFF) shows a quiet placeholder until the server's
  // decoded copy is available, right after the upload is accepted
  const wi = $("#work-img"), fig = wi.closest("figure");
  fig?.removeAttribute("data-nopreview");
  wi.onerror = () => fig?.setAttribute("data-nopreview", "");
  wi.onload = () => fig?.removeAttribute("data-nopreview");
  wi.src = imgUrl || "";
  resetSteps();
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
  step("upload", "done"); step("analyze", "doing");
  if (fig?.hasAttribute("data-nopreview")) wi.src = `/api/analysis/${id}/image`;
  history.pushState({}, "", `/workspace/result/${id}`);
  const name = fd.get("inspection_name") || "", place = [fd.get("barangay"), fd.get("city")].filter(Boolean).join(", ");
  remember({ id, name, place, label: "Processing", status: "processing", at: new Date().toISOString() });
  try {
    await track(id);
  } catch (err) {
    stalled(id, err);
    return;
  }
  step("analyze", "done"); step("result", "doing");
  await openResult(id, false);
}

async function openResult(id, push) {
  if (push) history.pushState({}, "", `/workspace/result/${id}`);
  let r;
  try {
    const s = await api(`/api/analysis/${id}`);
    if (s.status === "processing") {
      resetSteps(); step("upload", "done"); step("analyze", "doing"); onTick(s); show("working");
      $("#work-img").src = `/api/analysis/${id}/image`;
      try { await track(id); } catch (err) { stalled(id, err); return; }
      step("analyze", "done"); step("result", "doing");
    }
    r = await api(`/api/analysis/${id}/result`);
  } catch (err) {
    toast.error("Screening not found", { description: err.message });
    history.replaceState({}, "", "/workspace"); show("idle"); return;
  }
  remember({ id, pole: r.pole.code, name: r.inspection?.name || "",
    place: [r.inspection?.barangay, r.inspection?.city].filter(Boolean).join(", "), label: r.overview?.citizen?.status_label || "",
    status: r.status, at: r.created_at });
  if (r.guardrail?.requires_retake && !r.guardrail.choice) {  // v2.17: ask before showing a doubtful result
    const choice = await retakePrompt(r);
    if (choice === "retake") { startRetake(r); return; }
  }
  show("result");  // visible first: Leaflet and the canvas measure their containers on creation
  renderResult(r);  // S18: one result page for everyone; staff details live on the admin inspection page
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
import { pc, assessment } from "./findings.js?v=2.34";
// ------------------------------------------------------------------ result view (v2.20: written for the public)
// One result page for everyone (S18): the shared result view (result-view.js). Engineering details are on the admin
// inspection page; this page computes no threshold.
const ICON = {
  pdf: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3h7l5 5v13H7zM14 3v5h5M12 11v6M9 14l3 3 3-3"/></svg>`,
  plus: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>`,
};

/** S14 citizen results (register D-30). Answers four questions, in this order: what did the system find, does
 * anything need attention, what should I do, who can I contact. Every sentence is the server's (overview.citizen,
 * finding.plain); the page computes no threshold. S18: there is no separate technical view; engineering details are on
 * the admin inspection page. */
const CZ_ICON = {
  away: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M5 10l7-7 7 7"/></svg>`,
  hand: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m6 6 12 12"/></svg>`,
  share: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="6" cy="12" r="2.5"/><circle cx="18" cy="6" r="2.5"/><circle cx="18" cy="18" r="2.5"/><path d="m8.2 10.8 7.6-3.6M8.2 13.2l7.6 3.6"/></svg>`,
  phone: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>`,
  web: `<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>`,
  chat: `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v11H9l-5 4z"/></svg>`,
};
/** S17 (Part 5.3): the copied message: report ID, the wires that matter, each one's measured relative sag and class,
 * the result, and the recommended next action. No safety claim and no link (sharing a link is its own action). */
function czMessage(r, lists) {
  const insp = r.inspection || {}, cz = r.overview?.citizen || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");
  const shot = r.captured_at ? new Date(r.captured_at) : new Date(r.created_at);
  const line = (w) => `- Wire ${w.index}, ${w.finding?.type || classLabel(w, true)}: estimated relative sag ${w.finding?.sag?.text || "not measured"}${w.finding?.classification_label ? `, ${w.finding.classification_label.toLowerCase()}` : ""}${w.finding?.reference_short ? ` (${w.finding.reference_short})` : ""}.`;
  const listed = [...lists.attn, ...lists.notable];
  const next = lists.attn.length ? "Recommended next action: professional inspection of the listed span(s) by the utility."
    : lists.notable.length ? "Recommended next action: review of the listed cable(s) by the telecom provider." : "";
  return [`WIREFRAME image-based screening, report ${r.report_id || ""}`.trim(),
    [place, `photo ${r.captured_at ? "taken" : "submitted"} ${shot.toLocaleString()}`].filter(Boolean).join(" · "),
    "", `Result: ${cz.title || ""}.`, cz.text || "", ...(listed.length ? ["", ...listed.map(line)] : []),
    ...(next ? ["", next] : []),
    "", "Image-derived screening result; it requires review by qualified professionals."].join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

/** S17: the canonical Important Notice, shown once per page, at the foot of the result (both views). */
const NOTICE_FOOTER = `<footer class="rp-notice" aria-label="Important Notice"><wf-notice variant="full"></wf-notice></footer>`;
/** the shell's footer chip is the upload screen's notice; a result carries its own, so only one is ever shown */
const shellNotice = (on) => { const f = document.querySelector(".fx-foot"); if (f) f.hidden = !on; };

/** S18: the citizen result is the shared result view (result-view.js): What we know -> What you should do next ->
 * How did we know? Every count, finding, tip and next step is the server's (r.screening); this page adds only the
 * actions (report, share, copy) and the providers for the location. Staff review is not shown here (admin portal). */
let rv = null;
function renderResult(r) {
  const ov = r.overview || { counts: {}, lines: [], display: null, include_default: [] };
  const sc = r.screening || {};
  const pc = r.comparison;
  const insp = r.inspection || {};
  const place = [insp.barangay, insp.city].filter(Boolean).join(", ");
  const failed = r.status === "error";
  const staff = !r.public;
  const byIdx = new Map(r.wires.map((w) => [w.index, w]));
  const plan = ov.display || { attention: [], notable: [] };
  const take = (ids) => (ids || []).map((i) => byIdx.get(i)).filter(Boolean);
  const attn = take(plan.attention), notable = take(plan.notable);
  const incKey = `wf-include-${r.id}`;
  const fromUrl = new URLSearchParams(location.search).get("include");
  let saved = null; try { saved = JSON.parse(localStorage.getItem(incKey) || "null"); } catch { /* storage unavailable */ }
  const included = new Set(fromUrl != null ? (fromUrl === "none" ? [] : fromUrl.split(",").map(Number).filter((x) => byIdx.has(x)))
    : Array.isArray(saved) ? saved : ov.include_default || []);
  const incParam = () => (included.size ? [...included].sort((a, b) => a - b).join(",") : "none");
  const steps = sc.next_steps || [];
  const next = `<section class="rv-next" id="next" aria-labelledby="rv-next-h">
      <h2 id="rv-next-h">What you should do next</h2>
      <div class="rv-next-grid">
        <ol class="rv-steps">${steps.map((x) => `<li data-step="${esc(x.code)}"><b>${esc(x.title)}</b><span>${esc(x.text)}</span>${x.code === "retake" || x.code === "retry" ? `<button class="btn sm" type="button" data-retake>${x.code === "retry" ? "Upload again" : "Retake photo"}</button>` : ""}</li>`).join("")}</ol>
        <section class="rv-prov" id="contacts" aria-labelledby="rx-ct-h"><h3 id="rx-ct-h">Providers for this location</h3><p class="muted">Loading providers…</p></section>
      </div>
      <p class="rv-emerg" role="note">If a wire is down, sparking or burning, or someone is hurt, keep away and call <a href="tel:911"><b>911</b></a> immediately.</p>
    </section>`;

  shellNotice(false);
  stages.result.innerHTML = `
    <div class="cz rp rv-page">
      <header class="rv-top">
        <p class="rv-ref"><span class="num">${esc(r.report_id || "")}</span>${place ? `<span>${esc(place)}</span>` : ""}${insp.name ? `<span>${esc(insp.name)}</span>` : ""}${r.image?.original_filename ? `<span class="mono">${esc(r.image.original_filename)}</span>` : ""}</p>
        <div class="rv-acts">
          ${failed ? "" : `<a class="btn primary" id="pdf" target="_blank" rel="noopener">${ICON.pdf}Download report</a>
          <button class="btn" type="button" id="copy-msg">Copy message</button>
          <button class="btn" type="button" id="share">${CZ_ICON.share}Share link</button>`}
          <button class="btn ghost" id="again" type="button">${ICON.plus}New photo</button>
        </div>
      </header>
      ${guardBanner(r)}
      ${resultViewHtml(r, { mode: "citizen", nextHtml: next })}
      ${NOTICE_FOOTER}
    </div>`;

  const root = stages.result;
  $("#again").addEventListener("click", reset);
  root.querySelectorAll("[data-retake]").forEach((b) => b.addEventListener("click", () => startRetake(r)));
  $("#guard-retake")?.addEventListener("click", async () => {
    try { await api(`/api/analysis/${r.id}/guardrail`, { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "retake" }) }); } catch { /* never blocks */ }
    startRetake(r);
  });
  loadContacts(r);
  rv?.destroy();
  rv = bindResultView(root, r);
  viewer = rv.viewer;
  mountHelp(r);
  if (failed) return;
  try { localStorage.setItem(incKey, JSON.stringify([...included])); } catch { /* private mode */ }
  $("#pdf").href = `/api/reports/${r.id}/pdf?include=${incParam()}`;
  $("#copy-msg").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(czMessage(r, { attn, notable }));
      toast.success("Message copied", { description: "Paste it into a message or email to the organization you contact." });
    } catch { toast.error("Could not copy", { description: "The browser blocked the clipboard. Use Download report instead." }); }
  });
  $("#share").addEventListener("click", async () => {
    const url = `${location.origin}/workspace/result/${r.id}?include=${incParam()}`;
    try {
      if (navigator.share) await navigator.share({ title: insp.name || "Pole inspection report", url });
      else { await navigator.clipboard.writeText(url); toast.success("Link copied", { description: "Anyone with the link can open this result." }); }
    } catch { /* cancelled or blocked */ }
  });
}

/** S18: providers for this inspection's location, grouped by category (server: contacts.providers). Coverage is
 * never shown as ownership: "Confirmed responsible provider" appears only for an authoritative attachment record. */
const SOURCE_TYPE = { official_site: "official website", government_record: "government record", law: "law", press: "press report", third_party: "third-party page" };
/** the first number of a phone field: "(02) 16211" -> 0216211; "171 (PLDT landline); ..." -> 171 (labels in
 *  parentheses are dropped, an area code in parentheses is kept) */
const firstNum = (p) => ((p || "").split(";")[0].replace(/\([^)]*[A-Za-z][^)]*\)?/g, "").split(/\s\/\s/)[0].trim());
const telOf = (p) => { const n = firstNum(p).replace(/[^\d+]/g, ""); return n.length >= 3 ? n : null; };
function providerItem(c) {
  const t = telOf(c.phone);
  const details = [c.organization, c.phone && `Phone: ${c.phone}`, c.email && `Email: ${c.email}`, c.reporting_url || c.url].filter(Boolean).join("\n");
  return `<li class="rv-pv rel-${esc(c.relation)}">
    <div class="rv-pv-who"><b>${esc(c.organization)}</b>
      <span class="rv-pv-rel">${esc(c.relation_text)}</span>
      ${c.previous_names?.length ? `<small>Formerly ${esc(c.previous_names.join(", "))}</small>` : ""}
      ${c.verification === "secondary_source" ? `<small class="rx-ct-v">Contact from a secondary source: confirm it on the official site.</small>` : ""}</div>
    <div class="rv-pv-acts">${t ? `<a class="btn primary sm" href="tel:${t}">${CZ_ICON.phone}Call ${esc(firstNum(c.phone))}</a>` : ""}
      ${c.reporting_url ? `<a class="btn sm" href="${esc(c.reporting_url)}" target="_blank" rel="noopener">Report page</a>` : c.url ? `<a class="btn sm" href="${esc(c.url)}" target="_blank" rel="noopener">${CZ_ICON.web}Website</a>` : ""}
      <button class="btn sm ghost" type="button" data-copy="${esc(details)}">Copy details</button></div>
    <details class="rv-pv-src"><summary>Source and verification</summary><p>${esc(c.verification === "verified" ? "Verified" : "Secondary source")}${c.last_verified ? ` on ${esc(c.last_verified)}` : ""}${c.source_type ? ` from the ${esc(SOURCE_TYPE[c.source_type] || c.source_type)}` : ""}.${c.coverage_basis === "branch_office" ? " The provider lists a branch for this city; street-level coverage is not published." : c.coverage_basis === "provincial_listing" ? " The provider lists this province; town coverage is not published." : c.coverage_basis === "national_license" ? " Nationwide operator; availability at this address is not established." : ""}${c.source_url ? ` <a href="${esc(c.source_url)}" target="_blank" rel="noopener">Source</a>` : ""}</p>${c.confirmed ? `<p>Confirmed by ${esc(c.confirmed.verified_by)}: ${esc(c.confirmed.record_reference)}.</p>` : ""}</details></li>`;
}
async function loadContacts(r) {
  const el = $("#contacts"); if (!el) return;
  const head = `<h3 id="rx-ct-h">Providers for this location</h3>`;
  let d;
  try { d = await api(`/api/analysis/${r.id}/contacts`); } catch { el.innerHTML = `${head}<p>The provider list could not be loaded. In an emergency call <a href="tel:911">911</a>.</p>`; return; }
  const p = d.providers || { groups: [], nationwide: [] };
  const pl = p.place || {};
  const relevant = p.groups.filter((g) => g.relevant), others = p.groups.filter((g) => !g.relevant);
  const grp = (g) => `<section class="rv-pg"><h4>${esc(g.label)}</h4><ul>${g.items.map(providerItem).join("")}</ul></section>`;
  const more = others.length + p.nationwide.length;
  el.innerHTML = `${head}
    ${pl.resolved ? `<p class="rv-place">Matched location: <b>${esc([pl.barangay && `Brgy. ${pl.barangay}`, pl.city, pl.province].filter(Boolean).join(", "))}</b>. If this is not where the pole is, tell the provider the correct place.</p>` : ""}
    ${relevant.length ? `<p class="rv-pk">Relevant providers in this area</p>${relevant.map(grp).join("")}` : `<p class="muted">${esc(p.gap || "No provider with documented coverage for this location is in the directory yet.")}</p>`}
    ${p.unidentified_wires ? `<p class="rv-pnote">Some wires' type could not be identified, so providers of every category are shown.</p>` : ""}
    ${more ? `<details class="rv-pall"><summary>View all providers for this location <span class="num">${more}</span></summary>
      ${others.map(grp).join("")}
      ${p.nationwide.length ? `<section class="rv-pg"><h4>Nationwide providers</h4><ul>${p.nationwide.map(providerItem).join("")}</ul></section>` : ""}</details>` : ""}
    <p class="rx-ct-note">${esc(p.ownership_note)}</p>`;
  el.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-copy]"); if (!b) return;
    try { await navigator.clipboard.writeText(b.dataset.copy); toast.success("Contact details copied"); } catch { toast.error("Could not copy"); }
  });
}

/** S14 citizen assistant (register D-30): a small help panel on the result. It talks to /api/help, which knows only
 * this result's summary and the citizen guide; it has no tools and cannot see or change any record. */
function mountHelp(r) {
  document.querySelector(".cz-help-fab")?.remove(); document.querySelector(".cz-chat")?.remove();
  const SUGGEST = ["What does my result mean?", "Who should I contact?", "What is estimated sag?", "Is the wire dangerous?", "How do I share my report?"];
  const fab = document.createElement("button");
  fab.className = "cz-help-fab"; fab.type = "button"; fab.setAttribute("aria-expanded", "false"); fab.setAttribute("aria-controls", "cz-chat");
  fab.innerHTML = `${CZ_ICON.chat}<span>Questions?</span>`;
  const box = document.createElement("section");
  box.className = "cz-chat"; box.id = "cz-chat"; box.hidden = true; box.setAttribute("aria-label", "Help with your result");
  box.innerHTML = `<header><b>Help with your result</b><button type="button" class="cz-chat-x" aria-label="Close">×</button></header>
    <ol class="cz-chat-log" aria-live="polite"><li class="bot">Hi! I can explain your result, what estimated sag means, who to contact and how to share your report.</li></ol>
    <div class="cz-chat-sug">${SUGGEST.map((q) => `<button type="button" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>
    <form class="cz-chat-f"><input name="q" maxlength="500" placeholder="Type a question" aria-label="Your question" autocomplete="off"><button class="btn primary sm" type="submit">Ask</button></form>`;
  document.body.append(fab, box);
  const log = box.querySelector(".cz-chat-log"), hist = [];
  const add = (cls, html) => { const li = document.createElement("li"); li.className = cls; li.innerHTML = html; log.append(li); log.scrollTop = log.scrollHeight; return li; };
  const fmtA = (t) => esc(t).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/\n/g, "<br>");
  const ask = async (q) => {
    q = q.trim(); if (!q) return;
    add("me", esc(q)); const wait = add("bot pending", "…");
    try {
      const a = await api("/api/help/ask", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, analysis_id: r.id, history: hist.slice(-6) }) });
      wait.className = "bot"; wait.innerHTML = fmtA(a.answer) + (a.link ? ` <a href="${esc(a.link.href)}">${esc(a.link.label)}</a>` : "");
      hist.push({ role: "user", content: q }, { role: "assistant", content: a.answer });
    } catch (e) { wait.className = "bot"; wait.textContent = e.message || "Sorry, I can't answer right now."; }
  };
  const toggle = (open) => { box.hidden = !open; fab.setAttribute("aria-expanded", String(open)); if (open) box.querySelector("input").focus(); };
  fab.addEventListener("click", () => toggle(box.hidden));
  box.querySelector(".cz-chat-x").addEventListener("click", () => { toggle(false); fab.focus(); });
  box.addEventListener("click", (e) => { const b = e.target.closest("[data-q]"); if (b) ask(b.dataset.q); });
  box.querySelector("form").addEventListener("submit", (e) => { e.preventDefault(); const i = e.target.q; ask(i.value); i.value = ""; });
  box.addEventListener("keydown", (e) => { if (e.key === "Escape") { toggle(false); fab.focus(); } });
}

function reset() {
  shellNotice(true);
  document.querySelector(".cz-help-fab")?.remove(); document.querySelector(".cz-chat")?.remove();
  form.reset(); setFile(null); coords = null; place = null;
  locBusy(false); locateStatus(""); $("#more").open = false; syncLocChip();
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
