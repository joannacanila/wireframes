// WIREFRAME admin portal (v2.20; first written v2.19). Three operational destinations, Dashboard, Inspections and Map & Locations, plus
// (v2.23: the System page was removed; technical exports are on Inspections, diagnostics on each inspection).
// Every API it calls is guarded server-side (inspector vs admin included); this file decides presentation only.
// Operational pages show plain statuses (risk level, assessment, review); scores, fit parameters and formulas stay
// in System and in the collapsed technical diagnostics.

import { api, json, esc, ago, waitForAnalysis } from "./lib.js?v=2.23";
import { wireColor, classLabel } from "./viewer.js?v=2.35";
import { assessment, limitations, recommendations, finding, SECTION } from "./findings.js?v=2.34";
import { onStatus, startStatus } from "./status.js?v=2.23";
import { toast } from "./toast.js?v=2.23";
import { mountMap } from "./admin-map.js?v=2.34";
import { mountAssistant } from "./assistant.js?v=2.23";
import { resultViewHtml, bindResultView, riskChip, reviewChip, otherMetricsHtml, wireRefs } from "./result-view.js?v=2.36";
import { mountReviewPanel } from "./admin-review.js?v=2.34";
import { ACCEPT, DEFAULT_MAX_MB, checkPhoto } from "./upload-rules.js?v=2.30";
import { locate, reverseLookup, fixLabel, appendLocation } from "./location.js?v=2.34";
// S12 (D-28): why no sag reference applies to a wire, the same words as the result page and the findings CSV
const REF_WHY = { no_reference_for_telecom: "No validated telecom / fibre reference", no_reference_for_abandoned: "Abandoned wire: type not established",
  no_reference_for_class: "No validated reference for this wire type", type_uncertain: "Wire type uncertain", drop_wire: "Drop wire", reference_disabled: "No reference configured" };

const view = document.getElementById("view");
const $ = (s, r = document) => r.querySelector(s);
let me = null;
let cleanup = () => {};
let assistant = null;
const ctx = { page: "/admin", analysisId: null, label: "" }; // what the assistant knows about the current page

export const RISK = { high: "High risk", medium: "Moderate risk", low: "Low risk", unassessed: "Assessment incomplete",
  failed: "Processing failed", processing: "Processing" };
export const REVIEW = { pending: "Needs review", not_reviewed: "Not reviewed", confirmed: "Confirmed",
  flagged: "Flagged for inspection", overridden: "Level changed", dismissed: "Dismissed" };
export const COMPLETE = { complete: "Complete", partial: "Partial", measured: "Measured, not assessed", not_assessed: "Not assessed" };
export const riskBadge = (r, big = false) => `<span class="risk risk-${esc(r)}${big ? " big" : ""}"><i aria-hidden="true"></i>${esc(RISK[r] || r)}</span>`;
export const reviewText = (v, row) => `<span class="rev rev-${esc(v)}">${esc(REVIEW[v] || v)}${v === "overridden" && row?.override_level ? ` to ${esc(RISK[row.override_level].toLowerCase())}` : ""}</span>`;
const day = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
const dayTime = (iso) => new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
const place = (r) => [r.barangay, r.city].filter(Boolean).join(", ");
export const isAdmin = () => me?.role === "admin";

// ------------------------------------------------------------------ session
async function boot() {
  try { me = (await api("/api/auth/me")).user; } catch { me = null; }
  if (!me) return showLogin();
  $("#top").hidden = false;
  $("#who").textContent = me.display_name;
  $("#who-full").textContent = me.display_name;
  $("#who-role").textContent = me.role === "admin" ? "Administrator" : "Inspector";
  $("#avatar").textContent = me.display_name.trim().slice(0, 1).toUpperCase();
  if (location.pathname === "/admin/login") history.replaceState({}, "", "/admin");
  startStatus();
  onStatus(({ mode }) => {
    const a = $("#sys-alert");
    // a subtle line only when something needs attention; nothing when all is well
    a.hidden = mode === "field" || mode === "checking";
    a.textContent = { validation: "Inspection processing unavailable", offline: "Server unreachable" }[mode] || "";
    a.href = "/admin"; // v2.23: the System page is gone; the dashboard banner explains the problem
  });
  assistant ??= mountAssistant({ api, json, esc, getContext: () => ({ ...ctx }), navigate, launcher: $("#asst-open") });
  assistant.show();
  route();
}

function showLogin() {
  $("#top").hidden = true;
  assistant?.hide();
  if (location.pathname !== "/admin/login") history.replaceState({}, "", "/admin/login");
  view.innerHTML = `<section class="login">
    <form class="login-card" id="login" novalidate>
      <img src="/assets/logo.svg" alt="" width="36" height="36">
      <h1>Sign in to WIREFRAME</h1>
      <p class="hint">For inspectors, engineers and administrators. The Field Screener needs no account.</p>
      <label class="field"><span>Username</span><input name="username" autocomplete="username" required autofocus></label>
      <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
      <p class="form-error" id="login-error" role="alert" hidden></p>
      <button class="btn primary lg" type="submit">Sign in</button>
      <a class="back" href="/">Back to WIREFRAME</a>
    </form></section>`;
  const f = $("#login");
  f.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("#login-error"); err.hidden = true;
    const btn = f.querySelector("button"); btn.disabled = true;
    try {
      await api("/api/auth/login", json("POST", { username: f.username.value.trim(), password: f.password.value }));
      boot();
    } catch (ex) {
      err.textContent = ex.message; err.hidden = false; f.password.select();
    } finally { btn.disabled = false; }
  });
}

$("#signout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch { /* already signed out */ }
  me = null; closeMenu(); showLogin();
});
addEventListener("wf:unauthorized", () => {
  if (!me) return;
  me = null; toast("Session ended", { description: "Sign in again to continue." }); showLogin();
});

// user menu
const menuBtn = $("#user-btn"), menu = $("#user-menu");
const closeMenu = () => { menu.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); };
menuBtn.addEventListener("click", () => { const open = menu.hidden; menu.hidden = !open; menuBtn.setAttribute("aria-expanded", String(open)); });
document.addEventListener("click", (e) => { if (!menu.hidden && !e.target.closest(".adm-menu")) closeMenu(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) { closeMenu(); menuBtn.focus(); } });
menu.addEventListener("click", (e) => { if (e.target.closest("a")) closeMenu(); });
// v2.20: day / night toggle beside the profile menu; one attribute on <html> themes every page, the map and the assistant
const syncTheme = () => {
  const dark = document.documentElement.dataset.theme === "dark";
  $("#theme-btn").setAttribute("aria-label", dark ? "Switch to day theme" : "Switch to night theme");
  document.querySelector('meta[name="theme-color"]').content = dark ? "#0c0d0f" : "#f3f2ef";
  dispatchEvent(new CustomEvent("wf:theme", { detail: dark ? "dark" : "light" }));
};
$("#theme-btn").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("wf-admin-theme", next); } catch { /* private window */ }
  syncTheme();
});
syncTheme();

// ------------------------------------------------------------------ router
const LEGACY = [ // v2.18 addresses keep working
  [/^\/admin\/analyses\/([0-9a-f]{32})$/, (m) => `/admin/inspections/${m[1]}`],
  [/^\/admin\/analyses\/?$/, () => "/admin/inspections"],
  [/^\/admin\/poles\/(.+)$/, (m) => `/admin/inspections?q=${m[1]}`],
  [/^\/admin\/poles\/?$/, () => "/admin/inspections?latest_only=1"],
  [/^\/admin\/sites\/?$/, () => "/admin/map"],
  [/^\/admin\/(models|evaluation)\/?$/, () => "/admin"],  // v2.23: their System page was removed
  [/^\/admin\/exports\/?$/, () => "/admin/inspections"],  // technical exports are on Inspections
];
const ROUTES = [
  [/^\/admin\/?$/, "dashboard", () => mountDashboard()],
  [/^\/admin\/inspections\/?$/, "inspections", () => mountInspections()],
  [/^\/admin\/inspections\/([0-9a-f]{32})$/, "inspections", (m) => mountInspection(m[1])],
  [/^\/admin\/directory\/?$/, "directory", () => mountDirectory()],
  [/^\/admin\/map\/?$/, "map", () => mountMap(view, { api, json, esc, navigate, riskBadge, RISK, REVIEW, isAdmin, poleName })],
];
function route() {
  if (!me) return;
  cleanup(); cleanup = () => {};
  for (const [re, to] of LEGACY) {
    const m = location.pathname.match(re);
    if (m) { history.replaceState({}, "", to(m)); break; }
  }
  const p = location.pathname;
  Object.assign(ctx, { page: p, analysisId: null, label: "" });
  for (const [re, nav, fn] of ROUTES) {
    const m = p.match(re);
    if (m) {
      document.querySelectorAll("[data-nav]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === nav));
      cleanup = fn(m) || (() => {});
      view.focus({ preventScroll: true });
      scrollTo({ top: 0 });
      assistant?.context();
      return;
    }
  }
  view.innerHTML = `<section class="page"><h1 class="pg-title">Page not found</h1><p><a class="btn" href="/admin" data-link>Go to the dashboard</a></p></section>`;
}
export function navigate(href) { history.pushState({}, "", href); route(); }
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || a.target) return;
  e.preventDefault(); navigate(a.getAttribute("href"));
});
addEventListener("popstate", route);
addEventListener("wf:refresh", () => route());

const fail = (el) => (e) => { if (el) el.innerHTML = `<p class="empty">Could not load this page: ${esc(e.message)}. <button class="link-btn" type="button" data-reload>Try again</button></p>`; };
view.addEventListener("click", (e) => { if (e.target.closest("[data-reload]")) route(); });
function bindRowLinks(root) {
  root.addEventListener("click", (e) => {
    const tr = e.target.closest("[data-href]");
    if (tr && !e.target.closest("a,button,input,select,label")) navigate(tr.dataset.href);
  });
  root.addEventListener("keydown", (e) => {
    const tr = e.target.closest("[data-href]");
    if (tr && e.key === "Enter" && e.target === tr) navigate(tr.dataset.href);
  });
}
const loading = `<div class="loading" aria-busy="true"><span></span><span></span><span></span></div>`;

// ------------------------------------------------------------------ shared pieces (v2.20)
const PER_PAGE = 10;
/** A pole as people read it: the readable label first, the immutable pole code beside it. */
export const poleName = (r) => {
  const lab = r.label && r.label !== r.pole_code ? r.label : null;
  return lab ? `<span class="pn"><b>${esc(lab)}</b><span class="pn-code mono">${esc(r.pole_code)}</span></span>`
    : `<span class="pn"><b class="mono">${esc(r.pole_code)}</b></span>`;
};
/** Barangay / city when recorded, else coordinates, else nothing (a dash; never a "not recorded" placeholder). */
const placeHtml = (r) => {
  const p = place(r);
  if (p) return esc(p);
  if (r.location) return `<span class="mono coord">${r.location.lat.toFixed(5)}, ${r.location.lon.toFixed(5)}</span>`;
  return `<span class="muted" aria-label="No location recorded" title="No barangay or GPS recorded">–</span>`;
};
/** The one thing a person should know about a row, from recorded counts only. */
const issue = (r) => {
  const w = r.wires || {};
  if (r.risk === "failed") return "Could not be processed";
  if (r.risk === "processing") return "Still processing";
  if (w.high) return `${w.high} wire${w.high === 1 ? "" : "s"} at high risk`;
  if (w.medium) return `${w.medium} wire${w.medium === 1 ? "" : "s"} at moderate risk`;
  if (r.risk === "unassessed") return r.wires_total ? "Assessment incomplete: no wire could be assessed" : "Assessment incomplete: no wire detected";
  return "";
};
const shortDay = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
const shortTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/** Selection + bulk actions over the visible rows. Every action is a real backend operation, recorded in the
 *  audit trail; re-classifying records is for administrators and asks for confirmation. */
// S18: a click outside a column filter closes it
document.addEventListener("click", (e) => {
  if (e.target.closest?.(".th-flt-w")) return;
  document.querySelectorAll("[data-colpop]").forEach((p) => { p.hidden = true; });
  document.querySelectorAll("[data-colflt]").forEach((b) => b.setAttribute("aria-expanded", "false"));
});
document.addEventListener("keydown", (e) => { if (e.key === "Escape") document.querySelectorAll("[data-colpop]").forEach((p) => { p.hidden = true; }); });
const checkboxCell = (id, on) => `<td class="cb"><input type="checkbox" data-sel="${esc(id)}" ${on ? "checked" : ""} aria-label="Select"></td>`;
/** S18 (Phase 8): what each column's two sort directions do, in words. Column arrows only sort; they never filter.
 *  One click cycle on every table: ascending, then descending, then back to the default order. */
const SORT_DIR = { filename: ["A to Z", "Z to A"], insp: ["A to Z", "Z to A"], location: ["A to Z", "Z to A"],
  inspected: ["Oldest first", "Newest first"], submitted: ["Oldest first", "Newest first"],
  detected: ["Fewest wires first", "Most wires first"], problem: ["Fewest problem wires first", "Most problem wires first"],
  risk_level: ["Highest risk first", "Lowest risk first"], risk: ["Highest risk first", "Lowest risk first"],
  review_status: ["Not yet reviewed first", "Review complete first"], review: ["Not yet reviewed first", "Review complete first"] };
export const cycleSort = (sort, key, dflt) => (sort.key !== key ? { key, desc: false } : !sort.desc ? { key, desc: true } : { ...dflt });
const sortHead = (key, label, sort, cls = "", filter = "") => {
  const on = sort.key === key, [up, down] = SORT_DIR[key] || ["Ascending", "Descending"];
  const now = on ? (sort.desc ? down : up) : "";
  const next = !on ? `Sort: ${up}` : !sort.desc ? `Sort: ${down}` : "Clear sorting (default order)";
  return `<th class="${cls}" aria-sort="${on ? (sort.desc ? "descending" : "ascending") : "none"}"><span class="th-in"><button type="button" class="th-sort${on ? " on" : ""}" data-sort="${key}" title="${next}" aria-label="${esc(label)}${now ? `, sorted ${now.toLowerCase()}` : ""}. ${next}">${label}<i aria-hidden="true">${on ? (sort.desc ? "↓" : "↑") : "↕"}</i></button>${filter}</span></th>`;
};
/** S18: an explicit filter control in a column header (a funnel), separate from the sort arrows. */
const colFilter = (id, label, opts, cur) => `<span class="more th-flt-w"><button type="button" class="th-flt${cur ? " on" : ""}" data-colflt="${id}" aria-expanded="false" aria-haspopup="true" title="${esc(label)}" aria-label="${esc(label)}${cur ? " (active)" : ""}">
    <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16l-6 7.5V19l-4-2v-4.5z"/></svg></button>
  <span class="adm-pop th-flt-pop" data-colpop="${id}" hidden role="menu">${opts.map(([k, l]) => `<button type="button" role="menuitemradio" aria-checked="${cur === k}" data-colset="${id}" data-v="${esc(k)}">${esc(l)}</button>`).join("")}</span></span>`;
const pager = (page, pages, matched, per, what = "") => pages <= 1 ? (matched ? `<p class="pager-sum">${matched} ${what}</p>` : "")
  : `<nav class="pager" aria-label="Pages"><span class="pager-sum">${(page - 1) * per + 1}–${Math.min(page * per, matched)} of ${matched} ${what}</span>
    <button class="btn sm" type="button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <span class="num pager-n">${page} / ${pages}</span>
    <button class="btn sm" type="button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></nav>`;


/** v2.21: choose a barangay from the official list (PSGC). A free-text prompt once stored a person's name as a
 *  barangay; the server refuses anything that is not an official name, and this picker only offers those. */
export function pickBarangay(poleLabel, current = null) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "pick";
    dlg.setAttribute("aria-labelledby", "pk-h");
    // S18: Cancel, × and Escape always close without saving (Cancel was a submit button blocked by "required")
    dlg.innerHTML = `<form method="dialog" class="pick-f"><header class="pick-h"><h2 id="pk-h">${current ? "Change the barangay" : "Assign a barangay"}</h2>
        <button class="btn icon sm" type="button" data-close aria-label="Close without saving">×</button></header><p class="muted">${esc(poleLabel)}${current ? ` · now ${esc(current)}` : ""}</p>
      <label class="field"><span>Barangay</span><input id="pk-q" autocomplete="off" placeholder="Start typing, e.g. Malagasang"></label>
      <ol class="pick-res" id="pk-res"></ol><p class="form-error" id="pk-err" hidden></p>
      <div class="pick-acts"><button class="btn" type="button" data-close>Cancel</button><button class="btn primary" id="pk-ok" value="ok" type="submit" disabled>${current ? "Save" : "Assign"}</button></div></form>`;
    document.body.append(dlg);
    let chosen = null, t = 0;
    const q = dlg.querySelector("#pk-q"), res = dlg.querySelector("#pk-res"), ok = dlg.querySelector("#pk-ok");
    const choose = (name) => { chosen = name; q.value = name; res.innerHTML = ""; ok.disabled = false; };
    q.addEventListener("input", () => {
      chosen = null; ok.disabled = true; clearTimeout(t);
      t = setTimeout(async () => {
        const hits = q.value.trim() ? await api(`/api/admin/barangays?q=${encodeURIComponent(q.value.trim())}`).catch(() => []) : [];
        res.innerHTML = hits.map((h) => `<li><button type="button" data-name="${esc(h.name)}">${esc(h.name)} <span class="muted">${esc(h.city)}</span></button></li>`).join("")
          || (q.value.trim() ? `<li class="muted">Not in the official barangay list for Cavite.</li>` : "");
      }, 150);
    });
    res.addEventListener("click", (e) => { const b = e.target.closest("[data-name]"); if (b) choose(b.dataset.name); });
    dlg.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) dlg.close("cancel"); });
    dlg.addEventListener("close", () => { resolve(dlg.returnValue === "ok" ? chosen : null); dlg.remove(); });
    dlg.showModal(); q.focus();
  });
}

// ------------------------------------------------------------------ dashboard (S14: operational, register D-30)
// Distinct metrics from each pole's latest inspection (a processing failure and a flagged result are never counted
// together), a needs-attention queue with the one reason per row, readable locations (coordinates secondary), team vs
// system activity, and bulk actions the server checks (assign / unassign / mark reviewed; test records for admins).
const OPS = [["processing_failed", "Processing failed", "The pipeline did not finish"], ["flagged", "Needs review", "A power-line span exceeds the 3% sag reference, or a reviewer flagged it"],
  ["incomplete", "Incomplete", "No wire found, or some wires not fully measured"], ["awaiting_review", "Awaiting review", "Processed; nothing flagged yet"],
  ["reviewed", "Reviewed", "A reviewer recorded a decision"]];
/** S17: the screening result (what the photo showed) is its own column, separate from the queue status (where the
 *  record is in review). Tones follow the shared palette: amber exceeded, green clear, neutral otherwise. */
const RESULT_TONE = { REFERENCE_THRESHOLD_EXCEEDED: "warn", NO_THRESHOLD_EXCEEDANCE_DETECTED: "ok", INCOMPLETE_ASSESSMENT: "part",
  MEASUREMENT_UNAVAILABLE: "none", NO_APPLICABLE_REFERENCE: "none" };
export const resultCell = (r) => r.result ? `<span class="res res-${RESULT_TONE[r.result.code] || "none"}">${esc(r.result.label)}</span>`
  : `<span class="muted">${r.op_status === "processing_failed" ? "No result" : r.op_status === "processing" ? "Pending" : "Not available"}</span>`;
const opBadge = (k, label) => `<span class="ops ops-${esc(k)}"><i aria-hidden="true"></i>${esc(label)}</span>`;
const placeCell = (r) => {
  const p = r.place || {};
  const c = p.coords ? `<span class="mono coord">${p.coords[0].toFixed(5)}, ${p.coords[1].toFixed(5)}</span>` : "";
  const shared = r.shared_coords ? `<span class="tag tag-warn" title="${r.shared_coords} other pole${r.shared_coords === 1 ? " has" : "s have"} exactly these coordinates. Check the locations; they are not merged.">Same GPS as ${r.shared_coords} other${r.shared_coords === 1 ? "" : "s"}</span>` : "";
  if (p.label && p.label !== "Coordinates only" && p.label !== "Location not recorded") return `<span class="loc-l">${esc(p.label)}</span>${c ? `<span class="loc-c">${c}</span>` : ""}${shared}`;
  if (c) return `<span class="loc-l muted">Coordinates only</span><span class="loc-c">${c}</span>${shared}`;
  return `<span class="muted">Location not recorded</span>`;
};
/** S16: the fleet in one line; each number says what it counts. Poles and the map use the same rows. */
function fleetStrip(f, m) {
  if (!f) return "";
  const n = (x, one, many) => `<b class="num">${x ?? "–"}</b> ${x === 1 ? one : many}`;
  return `<section class="fleet" aria-label="Fleet overview">
    <a href="/admin/inspections?latest_only=1" data-link>${n(f.poles, "pole", "poles")}</a>
    <a href="/admin/inspections" data-link>${n(f.inspections, "inspection", "inspections")} <span class="muted">(a pole can have several)</span></a>
    <a href="/admin/map" data-link>${n(f.located, "pole", "poles")} on the map</a>
    ${f.needs_pinning ? `<a href="/admin/map?pin=1" data-link>${n(f.needs_pinning, "pole needs", "poles need")} a location</a>` : ""}
    ${f.location_mismatch ? `<a href="/admin/inspections?mismatch=1" data-link class="warn">${n(f.location_mismatch, "barangay does", "barangays do")} not match the city</a>` : ""}
    <span>${n(f.queue, "pole", "poles")} awaiting a review${m.processing ? ` · ${m.processing} processing` : ""}</span></section>`;
}

/** S18: the dashboard is the review workflow at a glance. Review status (administrative) and risk (machine triage)
 *  are separate controls; each row shows the inspection, its detected and problem wires, the risk indicator and the
 *  review status. No Assigned or Reason column: the reason lives behind the risk chip and in the inspection. */
export const REVIEW_TABS = [["", "All"], ["NOT_YET_REVIEWED", "Not yet reviewed"], ["PARTIALLY_REVIEWED", "Partially reviewed"], ["REVIEW_COMPLETE", "Review complete"]];
export const SCREEN = [["high", "High"], ["moderate", "Moderate"], ["normal", "Normal"], ["processing_issue", "Processing issue"], ["missing_location", "Missing location"]];
const SCREEN_TEST = { high: (r) => r.risk_level?.level === "high", moderate: (r) => r.risk_level?.level === "moderate",
  normal: (r) => r.risk_level?.level === "normal", processing_issue: (r) => ["failed", "processing"].includes(r.machine_state), missing_location: (r) => !r.has_location };
export const riskCell = (r) => r.machine_state === "failed" ? `<span class="rk rk-failed"><i></i>Processing failed</span>`
  : r.machine_state === "processing" ? `<span class="muted">Processing</span>` : riskChip(r.risk_level);
export const reviewCell = (r) => reviewChip(r.review_status, r.review_status_label);
export const inspName = (r) => `<span class="pn"><b class="mono pn-file">${esc(r.filename || r.pole_code)}</b><span class="pn-code">${esc(r.label && r.label !== r.pole_code ? `${r.label} · ${r.pole_code}` : r.pole_code)}</span></span>`;
const FEED_ICON = { received: "M12 5v14M5 12h14", processed: "M5 12l5 5 9-10", failure: "M12 8v5M12 16v.5M3 20 12 4l9 16z",
  review: "M3 12s3.5-6 9-6 9 6 9 6-3.5 6-9 6-9-6-9-6zM12 10a2 2 0 1 0 0 4 2 2 0 0 0 0-4z", decision: "M4 12l5 5L20 6",
  location: "M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11zM12 8.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  provider: "M4 20V8l8-4 8 4v12M9 20v-6h6v6", export: "M12 4v11M7 10l5 5 5-5M5 20h14", admin: "M12 3v18M3 12h18" };
export const feedItem = (x) => `<li class="fd-i fd-${esc(x.kind)}${x.automated ? " auto" : ""}">
    <i class="fd-ic" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="${FEED_ICON[x.kind] || FEED_ICON.admin}"/></svg></i>
    <p><b>${esc(x.actor)}</b> ${esc(x.text)}${x.target ? ` ${x.analysis_id ? `<a href="/admin/inspections/${x.analysis_id}" data-link>` : ""}<span class="${x.detail?.file ? "mono" : x.target_label ? "" : "mono"}">${esc(x.detail?.file || x.target_label || x.target)}</span>${x.analysis_id ? "</a>" : ""}` : ""}${x.automated ? "" : auditDetail(x)}</p>
    <time datetime="${esc(x.at)}" title="${esc(dayTime(x.at))}">${esc(ago(x.at))}</time></li>`;

function mountDashboard() {
  view.innerHTML = `<section class="page">
    <header class="pg-head"><div><h1 class="pg-title">Dashboard</h1>
      <p class="pg-sub">${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }))}</p></div>
      <div class="pg-actions"><a class="btn" href="/api/admin/exports/sites.pdf" target="_blank" rel="noopener">Export report</a>
        <a class="btn primary" href="/workspace" target="_blank" rel="noopener">New inspection</a></div></header>
    <div id="dash">${loading}</div>
    <footer class="pg-foot"><wf-notice variant="brief"></wf-notice></footer></section>`;
  const el = $("#dash");
  const st = { review: "NOT_YET_REVIEWED", screen: "", q: "", sort: { key: "risk", desc: false }, page: 1, act: "all" };
  let data = null, t = 0;
  const RORD = { high: 0, moderate: 1, normal: 2 };
  const SORT = { insp: (r) => (r.filename || r.pole_code).toLowerCase(), location: (r) => (r.place?.label || "~").toLowerCase(),
    risk: (r) => `${r.machine_state === "failed" ? 0 : (RORD[r.risk_level?.level] ?? 3) + 1}-${String(999 - (r.problem_count || 0)).padStart(3, "0")}-${9e12 - new Date(r.created_at).getTime()}`,
    detected: (r) => r.detected ?? -1, problem: (r) => r.problem_count ?? -1, submitted: (r) => r.created_at, review: (r) => r.review_status };

  const base = () => data.poles.filter((r) => !st.screen || SCREEN_TEST[st.screen](r));
  function rowsShown() {
    const k = st.q.trim().toLowerCase();
    let rows = base().filter((r) => (!st.review || r.review_status === st.review)
      && (!k || [r.filename, r.pole_code, r.label, r.place?.label, r.inspection_name, r.id].some((x) => (x || "").toLowerCase().includes(k))));
    const f = SORT[st.sort.key];
    return [...rows].sort((a, b) => (f(a) < f(b) ? -1 : f(a) > f(b) ? 1 : 0) * (st.sort.desc ? -1 : 1));
  }

  function drawTable() {
    const rows = rowsShown(), pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    st.page = Math.min(st.page, pages);
    const pageRows = rows.slice((st.page - 1) * PER_PAGE, st.page * PER_PAGE);
    $("#tq").innerHTML = pageRows.length ? `<div class="tbl-wrap"><table class="tbl tbl-dense tbl-ops tbl-s18">
      <thead><tr>${sortHead("insp", "Inspection", st.sort)}${sortHead("location", "Location", st.sort)}${sortHead("detected", "Wires", st.sort, "r")}${sortHead("risk", "Risk", st.sort)}${sortHead("review", "Review", st.sort)}${sortHead("submitted", "Received", st.sort)}<th class="act">Action</th></tr></thead>
      <tbody>${pageRows.map((r) => `<tr data-href="/admin/inspections/${r.id}" tabindex="0">
        <td data-th="Inspection">${inspName(r)}</td><td class="loc" data-th="Location">${placeCell(r)}</td>
        <td class="r num" data-th="Wires">${r.machine_state === "processed" ? r.detected : "–"}</td>
        <td data-th="Risk">${riskCell(r)}</td>
        <td data-th="Review">${reviewCell(r)}${r.decision ? `<small class="dec">${esc(r.decision.label)}</small>` : ""}</td>
        <td class="mono when" data-th="Received"><time datetime="${esc(r.created_at)}" title="${esc(dayTime(r.created_at))}">${esc(shortDay(r.created_at))}</time></td>
        <td class="act"><a class="go" href="/admin/inspections/${r.id}" data-link>${r.review_status === "REVIEW_COMPLETE" ? "Open" : "Review"}</a></td></tr>`).join("")}</tbody></table></div>
      ${pager(st.page, pages, rows.length, PER_PAGE, "poles")}`
      : `<p class="empty">${data.poles.length ? "No pole matches these filters." : "No inspection yet."}</p>`;
  }

  function drawActivity() {
    const list = [];  // consecutive identical events (same actor and action, no target) read as one line with a count
    for (const x of data.feed.filter((x) => st.act === "all" || (st.act === "people" ? !x.automated : x.automated))) {
      const p = list.at(-1);
      if (p && !x.target && !p.target && p.actor === x.actor && p.text === x.text) p.n = (p.n || 1) + 1; else list.push({ ...x });
      if (list.length >= 18) break;
    }
    $("#au").innerHTML = list.length ? `<ol class="feed">${list.map((x) => feedItem(x).replace("</p>", x.n ? ` <span class="num muted">×${x.n}</span></p>` : "</p>")).join("")}</ol>` : `<p class="empty">No events yet.</p>`;
    el.querySelectorAll("[data-act]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.act === st.act)));
  }

  function tabs() {
    const rows = base();
    const c = (k) => rows.filter((r) => !k || r.review_status === k).length;
    return `<div class="st-tabs" role="tablist" aria-label="Review status">${REVIEW_TABS.map(([k, l]) => `<button type="button" role="tab" data-review="${k}" aria-selected="${st.review === k}">${k ? reviewChip(k, l) : esc(l)}<b class="num">${c(k)}</b></button>`).join("")}</div>
      <div class="scr" role="group" aria-label="Screening filter"><span>Screening</span>${SCREEN.map(([k, l]) => `<button type="button" class="chip-f" data-screen="${k}" aria-pressed="${st.screen === k}">${k in RORD ? `<i class="rk rk-${k}"><i></i></i>` : ""}${esc(l)} <b class="num">${data.poles.filter(SCREEN_TEST[k]).length}</b></button>`).join("")}</div>`;
  }

  function render() {
    const sys = data.system;
    el.innerHTML = `
      ${sys.alerts.length ? `<div class="banner" role="status">${sys.alerts.map((a) => `<p>${esc(a.text)}</p>`).join("")}</div>` : ""}
      ${fleetStrip(data.fleet, data.metrics)}
      <div class="work work-stack">
        <section class="block work-main" aria-labelledby="tq-h">
          <header class="tq-head"><h2 id="tq-h">Inspections by pole</h2><span class="muted">each pole's latest inspection</span>
            <div class="tq-filters"><label class="tq-search"><span class="sr-only">Search</span><input type="search" id="tq-q" placeholder="File name, pole, place or ID" value="${esc(st.q)}"></label></div></header>
          <div id="tabs">${tabs()}</div>
          <div id="tq"></div>
        </section>
        <div class="work-below">
        <section class="block" aria-labelledby="au-h">
          <header class="block-head"><h2 id="au-h">Activity</h2>
            <div class="seg-tabs" role="tablist" aria-label="Activity source"><button role="tab" type="button" data-act="all">All</button><button role="tab" type="button" data-act="people" title="Actions people took">People</button><button role="tab" type="button" data-act="auto" title="Automated events">Automated</button></div></header>
          <div id="au"></div>
        </section>
        <section class="block" aria-labelledby="sy-h">
          <h2 class="rail-h" id="sy-h">Review progress</h2>
          <dl class="rp-bars">${REVIEW_TABS.slice(1).map(([k, l]) => { const n = data.review_counts[k] || 0, tot = data.poles.length || 1;
            return `<div><dt>${reviewChip(k, l)}</dt><dd><span class="bar"><i style="width:${(n / tot) * 100}%"></i></span><b class="num">${n}</b></dd></div>`; }).join("")}</dl>
          <p class="sys-line">${sys.ok ? "Processing available" : "Processing unavailable"}${sys.processing ? ` · ${sys.processing} in the processing queue` : ""}${sys.failed ? ` · ${sys.failed} failed inspections overall` : ""}</p>
        </section>
        </div>
      </div>`;
    drawTable(); drawActivity();
  }

  const load = () => api("/api/admin/operations").then((d) => { data = d; render(); }).catch(fail(el));
  el.addEventListener("click", (e) => {
    const rv = e.target.closest("[data-review]");
    if (rv) { st.review = rv.dataset.review; st.page = 1; $("#tabs").innerHTML = tabs(); drawTable(); return; }
    const sc = e.target.closest("[data-screen]");
    if (sc) { st.screen = st.screen === sc.dataset.screen ? "" : sc.dataset.screen; st.page = 1; $("#tabs").innerHTML = tabs(); drawTable(); return; }
    const a = e.target.closest("[data-act]"); if (a) { st.act = a.dataset.act; drawActivity(); return; }
    const sb = e.target.closest("[data-sort]");
    if (sb) { st.sort = cycleSort(st.sort, sb.dataset.sort, { key: "risk", desc: false }); st.page = 1; drawTable(); return; }
    const pb = e.target.closest("[data-page]");
    if (pb && !pb.disabled) { st.page = Number(pb.dataset.page); drawTable(); $("#tq-h").scrollIntoView({ block: "nearest" }); }
  });
  el.addEventListener("input", (e) => { if (e.target.id === "tq-q") { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; st.page = 1; drawTable(); }, 150); } });
  bindRowLinks(el);
  load();
  return () => clearTimeout(t);
}

/** S14 bulk actions on the dashboard queue. The server checks every one (assignee must be an active account; test
 *  records are administrator-only) and records it in the team activity. */
function opsBulk(el, getSelected, onDone, users, records = false) {
  const draw = () => {
    const ids = getSelected();
    el.hidden = !ids.length;
    if (!ids.length) { el.innerHTML = ""; return; }
    el.innerHTML = `<span class="bk-n"><b class="num">${ids.length}</b> selected</span>
      <label class="sel"><span class="sr-only">Assign to</span><select id="bk-who"><option value="">Assign to…</option>${users.map((u) => `<option value="${u.id}">${esc(u.name)}</option>`).join("")}</select></label>
      <button class="btn sm" type="button" data-bk="unassign">Unassign</button>
      <button class="btn sm" type="button" data-bk="mark_reviewed">Mark reviewed</button>
      <a class="btn sm" href="/api/admin/exports/inspections.csv?ids=${ids.join(",")}" download>Export CSV</a>
      ${records ? `<button class="btn sm" type="button" data-bk="assign_barangay">Set barangay</button>` : ""}
      ${isAdmin() ? `<button class="btn sm" type="button" data-bk="mark_test">Mark as test</button>${records ? `<button class="btn sm" type="button" data-bk="mark_operational">Mark as operational</button>` : ""}` : ""}
      <button class="link-btn" type="button" data-bk="clear">Clear selection</button>`;
  };
  const run = async (action, extra = {}) => {
    try {
      const r = await api("/api/admin/inspections/bulk", json("POST", { ids: getSelected(), action, ...extra }));
      toast.success(`${r.updated} inspection${r.updated === 1 ? "" : "s"} updated`); onDone(true);
    } catch (ex) { toast.error("Not changed", { description: ex.message }); }
  };
  el.addEventListener("change", (e) => { if (e.target.id === "bk-who" && e.target.value) run("assign", { assignee_id: Number(e.target.value) }); });
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-bk]"); if (!b) return;
    const n = getSelected().length;
    if (b.dataset.bk === "clear") return onDone(false);
    if (b.dataset.bk === "assign_barangay") { pickBarangay(`${n} selected inspection${n === 1 ? "" : "s"}`).then((v) => v && run("assign_barangay", { barangay: v })); return; }
    if (b.dataset.bk === "mark_operational" && !confirm(`Mark ${n} inspection${n === 1 ? "" : "s"} as operational records? They will count in the dashboard, the map and the exports.`)) return;
    if (b.dataset.bk === "mark_reviewed" && !confirm(`Record ${n} inspection${n === 1 ? "" : "s"} as reviewed by you? Failed and still-processing inspections are skipped.`)) return;
    if (b.dataset.bk === "mark_test" && !confirm(`Mark ${n} inspection${n === 1 ? "" : "s"} as test records? They leave the dashboard, lists, map and exports. Nothing is deleted.`)) return;
    run(b.dataset.bk);
  });
  return draw;
}

// ------------------------------------------------------------------ directory (S14: reporting contacts)
const ORG = { emergency: "Emergency", barangay: "Barangay", electric_utility: "Electric utility", telecom_provider: "Telecom provider", city_government: "City government", other: "Other" };
const CAT = { electricity: "Electricity", telecom: "Telecom / telephone", broadband: "Fiber / broadband", catv: "Cable TV", other_infra: "Other infrastructure", emergency: "Emergency", government: "Local government" };
const SRC = { "": "Not recorded", official_site: "Official website", government_record: "Government record", law: "Law", press: "Press report", third_party: "Third-party page" };
const COV = { "": "Not recorded", documented_area: "Documented service area", branch_office: "Branch office for the city", provincial_listing: "Province listing", national_license: "National operator" };
const VER = { verified: "Verified (official source)", secondary_source: "Secondary source", unverified: "Unverified" };
function mountDirectory() {
  view.innerHTML = `<section class="page">
    <header class="pg-head"><div><h1 class="pg-title">Reporting directory</h1>
      <p class="pg-sub" id="dir-upd"></p></div>
      <div class="pg-actions">${isAdmin() ? `<button class="btn primary" type="button" id="ct-new">Add contact</button>` : ""}</div></header>
    <div id="dir">${loading}</div></section>`;
  const el = $("#dir");
  let data = null;
  const load = () => api("/api/admin/contacts").then((d) => { data = d; draw(); }).catch(fail(el));
  // S16: readable cells (call links, site names, short coverage), search and filters; notes stay in the edit form
  // S18 (Phase 15): five columns; a phone or website is shown only from an entry verified on an official source, and
  // unverified entries (administrative tasks) appear only when that filter is chosen
  const fl = { q: "", type: "", ver: "", cat: "" };
  const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };
  const tels = (p) => (p || "").split(/;/).map((x) => x.trim()).filter(Boolean).map((x) => {
    const n = x.split("(")[0].replace(/[^\d+]/g, ""); return n.length >= 3 ? `<a class="mono" href="tel:${esc(n)}">${esc(x)}</a>` : `<span class="mono">${esc(x)}</span>`; }).join("<br>");
  const area = (c) => { const xs = (c.area_label || "").split(", ");
    return xs.length <= 3 ? esc(xs.join(", ")) : `${esc(xs.slice(0, 3).join(", "))} <button type="button" class="link-btn" data-more="${c.id}" data-n="${xs.length - 3}" aria-expanded="false">+${xs.length - 3} more</button><span class="dir-more" hidden>${esc(xs.slice(3).join(", "))}</span>`; };
  const shown = () => data.items.filter((c) => (fl.ver ? c.verification === fl.ver : c.verification !== "unverified") && (!fl.type || c.org_type === fl.type) && (!fl.cat || c.category === fl.cat)
    && (!fl.q || [c.organization, c.registered_name, ...(c.previous_names || []), c.assistance, ...(c.cities || [])].some((x) => (x || "").toLowerCase().includes(fl.q))));
  const draw = () => {
    const upd = $("#dir-upd"); if (upd) upd.textContent = `Updated as of ${data.updated}`;
    el.innerHTML = `<div class="toolbar dir-tools" role="search">
        <label class="tb-search"><span class="sr-only">Search the directory</span><input type="search" id="dir-q" placeholder="Organization or city" value="${esc(fl.q)}"></label>
        <label class="sel"><span class="sr-only">Category</span><select id="dir-cat"><option value="">All categories</option>${Object.entries(CAT).map(([k, t]) => `<option value="${k}" ${fl.cat === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="sel"><span class="sr-only">Type</span><select id="dir-type"><option value="">All types</option>${Object.entries(ORG).map(([k, t]) => `<option value="${k}" ${fl.type === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <label class="sel"><span class="sr-only">Verification</span><select id="dir-ver"><option value="">Verified and secondary</option>${Object.entries(VER).map(([k, t]) => `<option value="${k}" ${fl.ver === k ? "selected" : ""}>${t}</option>`).join("")}</select></label>
        <span class="muted num" id="dir-n"></span></div>
      <div id="dir-t"></div>`;
    drawRows();
  };
  const drawRows = () => {
    const stale = new Set(data.stale), rows = shown();
    const base = data.items.filter((c) => c.verification !== "unverified").length;
    $("#dir-n").textContent = rows.length === base && !fl.ver ? `${rows.length} organizations` : `${rows.length} shown`;
    const ok = (c) => c.verification === "verified"; // contact details only from an official source
    $("#dir-t").innerHTML = rows.length ? `<div class="tbl-wrap block"><table class="tbl tbl-dense tbl-dir"><thead><tr><th>Organization</th><th>Category</th><th>Area</th><th>Phone</th><th>Website</th></tr></thead>
      <tbody>${rows.map((c) => `<tr>
        <td data-th="Organization">${isAdmin() ? `<button class="link-btn dir-org" type="button" data-edit="${c.id}" title="Edit this entry">${esc(c.organization)}</button>` : `<b>${esc(c.organization)}</b>`}${stale.has(c.id) ? ` <span class="tag tag-warn" title="Last verified over a year ago">Recheck</span>` : ""}</td>
        <td data-th="Category">${esc(c.category_label || CAT[c.category] || ORG[c.org_type] || "")}</td>
        <td data-th="Area" class="dir-area">${area(c)}</td>
        <td data-th="Phone">${ok(c) && c.phone ? tels(c.phone) : `<span class="muted">—</span>`}</td>
        <td data-th="Website">${ok(c) && (c.reporting_url || c.url) ? `<a href="${esc(c.reporting_url || c.url)}" target="_blank" rel="noopener">${esc(host(c.reporting_url || c.url))}</a>` : `<span class="muted">—</span>`}</td></tr>`).join("")}</tbody></table></div>`
      : `<p class="empty">No contact matches these filters.</p>`;
  };
  el.addEventListener("input", (e) => { if (e.target.id === "dir-q") { fl.q = e.target.value.trim().toLowerCase(); drawRows(); } });
  el.addEventListener("change", (e) => { if (e.target.id === "dir-type") { fl.type = e.target.value; drawRows(); } if (e.target.id === "dir-ver") { fl.ver = e.target.value; drawRows(); } if (e.target.id === "dir-cat") { fl.cat = e.target.value; drawRows(); } });
  el.addEventListener("click", (e) => { const m = e.target.closest("[data-more]"); if (m) { const x = m.nextElementSibling; x.hidden = !x.hidden; m.setAttribute("aria-expanded", String(!x.hidden)); m.textContent = x.hidden ? `+${m.dataset.n} more` : "Show fewer"; } });
  const form = (c = {}) => new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "pick ct-dlg";
    const opt = (o, v) => Object.entries(o).map(([k, t]) => `<option value="${k}" ${k === v ? "selected" : ""}>${t}</option>`).join("");
    dlg.innerHTML = `<form method="dialog" class="pick-f"><h2>${c.id ? "Edit contact" : "Add contact"}</h2>
      <label class="field"><span>Organization</span><input name="organization" required value="${esc(c.organization || "")}"></label>
      <div class="ct-2"><label class="field"><span>Registered name</span><input name="registered_name" value="${esc(c.registered_name || "")}"></label>
        <label class="field"><span>Previous names (comma-separated)</span><input name="previous_names" value="${esc((c.previous_names || []).join(", "))}"></label></div>
      <div class="ct-2"><label class="field"><span>Category</span><select name="category">${opt(CAT, c.category || "electricity")}</select></label>
        <label class="field"><span>Coverage basis</span><select name="coverage_basis">${opt(COV, c.coverage_basis || "")}</select></label></div>
      <div class="ct-2"><label class="field"><span>Type</span><select name="org_type">${opt(ORG, c.org_type || "electric_utility")}</select></label>
        <label class="field"><span>Verification</span><select name="verification">${opt(VER, c.verification || "unverified")}</select></label></div>
      <label class="field"><span>Cities / municipalities (one per line, or "national")</span><textarea name="cities" rows="3">${esc((c.cities || []).join("\n"))}</textarea></label>
      <div class="ct-2"><label class="field"><span>Phone</span><input name="phone" value="${esc(c.phone || "")}"></label>
        <label class="field"><span>Reporting URL (https)</span><input name="url" value="${esc(c.url || "")}"></label></div>
      <div class="ct-2"><label class="field"><span>Email</span><input name="email" type="email" value="${esc(c.email || "")}"></label>
        <label class="field"><span>Outage / report page (https)</span><input name="reporting_url" value="${esc(c.reporting_url || "")}"></label></div>
      <label class="field"><span>What they help with</span><input name="assistance" value="${esc(c.assistance || "")}"></label>
      <label class="field"><span>Instructions for the public</span><textarea name="instructions" rows="2">${esc(c.instructions || "")}</textarea></label>
      <div class="ct-2"><label class="field"><span>Source URL (https; required unless unverified)</span><input name="source_url" value="${esc(c.source_url || "")}"></label>
        <label class="field"><span>Last verified (YYYY-MM-DD)</span><input name="last_verified" value="${esc(c.last_verified || "")}"></label></div>
      <div class="ct-2"><label class="field"><span>Source type</span><select name="source_type">${opt(SRC, c.source_type || "")}</select></label>
        <label class="field"><span>Source note</span><input name="source_note" value="${esc(c.source_note || "")}"></label></div>
      <label class="field"><span>Internal notes (staff only, never shown to the public)</span><textarea name="admin_notes" rows="2">${esc(c.admin_notes || "")}</textarea></label>
      <p class="form-error" id="ct-err" hidden></p>
      <div class="pick-acts">${c.id ? `<button class="btn danger" type="button" id="ct-off">Deactivate</button><span class="adm-spacer"></span>` : ""}<button class="btn" value="cancel" formnovalidate>Cancel</button><button class="btn primary" id="ct-save" type="button">Save</button></div></form>`;
    document.body.append(dlg);
    const err = dlg.querySelector("#ct-err");
    dlg.querySelector("#ct-save").addEventListener("click", async () => {
      const f = Object.fromEntries(new FormData(dlg.querySelector("form")));
      const body = { ...f, cities: f.cities.split("\n").map((x) => x.trim()).filter(Boolean), barangays: c.barangays || [],
        phone: f.phone || null, url: f.url || null, source_url: f.source_url || null, last_verified: f.last_verified || null, service_area: c.service_area || "",
        previous_names: f.previous_names.split(",").map((x) => x.trim()).filter(Boolean), registered_name: f.registered_name || null,
        email: f.email || null, reporting_url: f.reporting_url || null, source_type: f.source_type || null, coverage_basis: f.coverage_basis || null };
      try {
        await api(c.id ? `/api/admin/contacts/${c.id}` : "/api/admin/contacts", json(c.id ? "PUT" : "POST", body));
        toast.success("Contact saved"); dlg.close("saved");
      } catch (ex) { err.hidden = false; err.textContent = ex.message; }
    });
    dlg.querySelector("#ct-off")?.addEventListener("click", async () => {
      if (!confirm(`Deactivate ${c.organization}? It stops being suggested; reports that cited it stay unchanged.`)) return;
      try { await api(`/api/admin/contacts/${c.id}`, { method: "DELETE" }); toast.success("Contact deactivated"); dlg.close("saved"); }
      catch (ex) { err.hidden = false; err.textContent = ex.message; }
    });
    dlg.addEventListener("close", () => { resolve(dlg.returnValue === "saved"); dlg.remove(); });
    dlg.showModal();
  });
  view.querySelector(".page").addEventListener("click", async (e) => {
    if (e.target.closest("#ct-new")) { if (await form()) load(); }
    const ed = e.target.closest("[data-edit]");
    if (ed) { if (await form(data.items.find((x) => x.id === Number(ed.dataset.edit)))) load(); }
  });
  load();
}

function auditDetail(x) {
  const d = x.detail || {};
  if (x.action === "barangay.set" && d.new) return `: <span class="muted">${esc(d.new)}</span>`;
  if (x.action === "review.overridden" && d.override_level) return ` to ${esc((RISK[d.override_level] || "").toLowerCase())}`;
  if (x.action === "location.set" && Array.isArray(d.new)) return ` <span class="mono muted">${Number(d.new[0]).toFixed(5)}, ${Number(d.new[1]).toFixed(5)}</span>`;
  if (x.action === "model.activated" && d.name) return ` <span class="mono muted">${esc(d.name)}</span>`;
  return "";
}

// ------------------------------------------------------------------ inspections
// S14 (D-30): the list uses the same operational statuses as the dashboard (app/services/ops.py)
const TABS = [["all", "All"], ["attention", "Not yet reviewed"], ["flagged", "Needs review"], ["incomplete", "Incomplete"], ["failed", "Processing failed"], ["reviewed", "Reviewed"]];
const POP_FILTERS = ["barangay", "status", "assignee", "date_from", "date_to"];
const OP_LABEL = { processing_failed: "Processing failed", flagged: "Needs review", incomplete: "Incomplete", processing: "Processing", awaiting_review: "Awaiting review", reviewed: "Reviewed" };
function mountInspections() {
  const params = new URLSearchParams(location.search);
  const st = {
    tab: TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "all",
    review_status: REVIEW_TABS.some(([k]) => k && k === params.get("review_status")) ? params.get("review_status") : "",
    screen: SCREEN.some(([k]) => k === params.get("screen")) ? params.get("screen") : "",
    q: params.get("q") || "", risk: params.get("risk") || "", review: params.get("review") || "",
    f: Object.fromEntries(POP_FILTERS.map((k) => [k, params.get(k) || ""])),
    latest_only: !!params.get("latest_only"), include_test: !!params.get("include_test"), mismatch: !!params.get("mismatch"),
    sort: { key: params.get("sort") || "triage", desc: params.get("desc") === "1" }, page: Number(params.get("page")) || 1,
    per: Number(params.get("per_page")) || 25, sel: new Set(), facets: null,
  };
  view.innerHTML = `<section class="page page-wide">
    <header class="pg-head pg-head-line"><div class="pg-title-line"><h1 class="pg-title">Inspections</h1><span class="pg-count num" id="count"></span></div>
      <div class="pg-actions btn-group">
        <div class="more"><button class="btn" type="button" id="tx-btn" aria-expanded="false" aria-controls="tx-pop">Export</button>
          <div class="adm-pop tx-pop" id="tx-pop" hidden>
            <a id="csv" href="/api/admin/exports/inspections.csv"><b>This list (CSV)</b><span>The inspections matching the current filters, one row each.</span></a>
            <a href="/api/admin/exports/sites.pdf" target="_blank" rel="noopener"><b>Summary report (PDF)</b><span>Every pole's latest inspection, for sharing.</span></a>
            ${isAdmin() ? `<p class="pop-h">Technical exports</p><a href="/api/admin/exports/wires.csv"><b>Wire measurements (CSV)</b><span>Every wire of every inspection: fitted curve, residuals, sag, reference, score.</span></a>
            <a href="/api/admin/exports/manifest.csv"><b>Site manifest (CSV)</b><span>One row per pole: rank, priority score, level, clutter, review, location.</span></a>
            <a href="/api/admin/exports/sites.pdf?technical=true" target="_blank" rel="noopener"><b>Technical priority report (PDF)</b><span>The ranking with priority scores, weights and their sensitivity.</span></a>` : ""}
          </div></div>
        <button class="btn" type="button" id="batch-btn" aria-expanded="false" aria-controls="batch">Upload photos</button>
        <a class="btn primary" href="/workspace" target="_blank" rel="noopener">New inspection</a></div></header>
    <section class="batch" id="batch" hidden aria-label="Upload several photos"></section>
    <div class="toolbar" role="search">
      <div class="tabs-q" role="tablist" aria-label="Review status" id="tabs"></div>
      <label class="tb-search"><span class="sr-only">Search file name, pole ID, location or coordinates</span><input type="search" id="q" placeholder="File name, pole, place or coordinates" value="${esc(st.q)}"></label>
      <div class="more"><button class="btn" type="button" id="flt-btn" aria-expanded="false" aria-controls="flt-pop">Filters<span class="flt-n num" id="flt-n" hidden></span></button>
        <form class="adm-pop flt-pop" id="flt-pop" hidden>
          <label class="flt"><span>Barangay</span><select name="barangay"><option value="">Any</option></select></label>
          <label class="flt"><span>Status</span><select name="status"><option value="">Any</option>${Object.entries(OP_LABEL).map(([k, v]) => `<option value="${k}" ${st.f.status === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <label class="flt"><span>Assigned to</span><select name="assignee"><option value="">Anyone</option><option value="none" ${st.f.assignee === "none" ? "selected" : ""}>Unassigned</option></select></label>
          <div class="flt-dates"><label class="flt"><span>From</span><input type="date" name="date_from" value="${esc(st.f.date_from)}"></label>
            <label class="flt"><span>To</span><input type="date" name="date_to" value="${esc(st.f.date_to)}"></label></div>
          <label class="chk"><input type="checkbox" name="latest_only" ${st.latest_only ? "checked" : ""}> Latest per pole</label>
          <label class="chk"><input type="checkbox" name="mismatch" ${st.mismatch ? "checked" : ""}> Barangay does not match the city</label>
          ${isAdmin() ? `<label class="chk"><input type="checkbox" name="include_test" ${st.include_test ? "checked" : ""}> Include test records</label>` : ""}
        </form></div>
      <button class="link-btn" type="button" id="clear" hidden>Clear all</button>
    </div>
    <div class="scr" id="scr" role="group" aria-label="Screening filter"></div>
    <div class="active-flt" id="active-flt" hidden></div>
    <div class="bulk" id="bulk" hidden></div>
    <div id="list">${loading}</div></section>`;
  const fp = $("#flt-pop"), fbtn = $("#flt-btn");
  let t = 0, reqN = 0;
  const activeCount = () => (st.tab !== "all") + !!st.screen + POP_FILTERS.filter((k) => st.f[k]).length + st.latest_only + st.include_test + st.mismatch + !!st.risk + !!st.review + !!st.q;
  const qs = (forApi) => {
    const q = new URLSearchParams();
    if (st.tab !== "all") q.set(forApi ? "op_tab" : "tab", st.tab);
    if (st.review_status) q.set("review_status", st.review_status);
    if (st.screen) q.set("screen", st.screen);
    if (st.q) q.set("q", st.q);
    if (st.risk) q.set("risk", st.risk);
    if (st.review) q.set("review", st.review);
    for (const k of POP_FILTERS) if (st.f[k]) q.set(k, st.f[k]);
    if (st.latest_only) q.set("latest_only", forApi ? "true" : "1");
    if (st.include_test) q.set("include_test", forApi ? "true" : "1");
    if (st.mismatch) q.set("mismatch", forApi ? "true" : "1");
    if (st.sort.key !== "triage") { q.set("sort", st.sort.key); if (st.sort.desc) q.set("desc", forApi ? "true" : "1"); }
    if (st.page > 1) q.set("page", st.page);
    if (st.per !== 25) q.set("per_page", st.per);
    return q;
  };
  // S14: the same bulk bar as the dashboard (assign, unassign, mark reviewed, export) plus the record actions
  const users = [];
  const drawBulk = opsBulk($("#bulk"), () => [...st.sel], (changed) => { st.sel.clear(); changed ? load() : draw(lastData); }, users, true);
  api("/api/admin/users").then((u) => {
    users.push(...u); drawBulk();
    fp.assignee.insertAdjacentHTML("beforeend", u.map((x) => `<option value="${x.id}" ${st.f.assignee === String(x.id) ? "selected" : ""}>${esc(x.name)}</option>`).join(""));
  }).catch(() => {});
  let lastData = null;

  function draw(d) {
    lastData = d;
    // S18: review status (administrative) tabs and screening (machine) filters are separate; the server computes
    // both counts after the other filters, so a tab's number is the number of rows it shows
    const rt = d.review_tabs || {};
    $("#tabs").innerHTML = REVIEW_TABS.map(([k, tx]) => `<button type="button" role="tab" data-rtab="${k}" aria-selected="${st.review_status === k}">${k ? reviewChip(k, tx) : esc(tx)}<b class="num">${rt[k || "all"] ?? ""}</b></button>`).join("");
    $("#scr").innerHTML = `<span>Screening</span>${SCREEN.map(([k, l]) => `<button type="button" class="chip-f" data-screen="${k}" aria-pressed="${st.screen === k}">${["high", "moderate", "normal"].includes(k) ? `<i class="rk rk-${k}"><i></i></i>` : ""}${esc(l)} <b class="num">${(d.screen_tabs || {})[k] ?? 0}</b></button>`).join("")}`;
    $("#count").textContent = d.matched === d.total ? `${d.total}` : `${d.matched} of ${d.total}`;
    const n = activeCount(); $("#flt-n").hidden = !(n - !!st.q); $("#flt-n").textContent = n - !!st.q;
    $("#clear").hidden = !n;
    const chips = [st.tab !== "all" && `Status: ${(TABS.find(([k]) => k === st.tab) || [])[1]}`, st.risk && `Risk: ${st.risk.split(",").map((x) => RISK[x] || x).join(", ")}`, st.review && `Review: ${st.review.split(",").map((x) => REVIEW[x] || x).join(", ")}`].filter(Boolean);
    const keys = [st.tab !== "all" && "tab", st.risk && "risk", st.review && "review"].filter(Boolean);
    $("#active-flt").hidden = !chips.length;
    $("#active-flt").innerHTML = chips.map((c, i) => `<span class="fchip">${esc(c)}<button type="button" data-unset="${keys[i]}" aria-label="Remove filter">×</button></span>`).join("");
    const all = d.items.length && d.items.every((r) => st.sel.has(r.id));
    $("#list").innerHTML = d.items.length ? `<div class="tbl-scroll"><table class="tbl tbl-dense tbl-insp tbl-s18">
      <thead><tr><th class="cb"><input type="checkbox" data-all ${all ? "checked" : ""} aria-label="Select all on this page"></th>
        ${sortHead("filename", "File", st.sort)}${sortHead("location", "Location", st.sort)}${sortHead("inspected", "Date", st.sort)}${sortHead("detected", "Detected", st.sort, "r")}${sortHead("problem", "Problem", st.sort, "r")}${sortHead("risk_level", "Risk", st.sort, "", colFilter("risk", "Filter by risk level", [["", "All risk levels"], ...SCREEN.filter(([k]) => ["high", "moderate", "normal"].includes(k))], ["high", "moderate", "normal"].includes(st.screen) ? st.screen : ""))}${sortHead("review_status", "Review", st.sort, "", colFilter("review", "Filter by review status", REVIEW_TABS.map(([k, l]) => [k, k ? l : "All review statuses"]), st.review_status))}<th>Admin result</th>
        <th class="act"><span class="sr-only">Action</span></th></tr></thead>
      <tbody>${d.items.map((r) => `<tr data-href="/admin/inspections/${r.id}" tabindex="0" ${st.sel.has(r.id) ? 'aria-selected="true"' : ""}>
        ${checkboxCell(r.id, st.sel.has(r.id))}
        <td data-label="File">${inspName(r)}${r.record_kind === "test" ? '<span class="tag">Test</span>' : ""}${r.same_day > 1 ? `<span class="tag tag-dup" title="${r.same_day} inspections of this pole on the same day">Repeat</span>` : ""}</td>
        <td data-label="Location" class="loc">${r.place ? placeCell(r) : placeHtml(r)}${r.location_check?.consistent === false ? `<span class="tag tag-warn" title="${esc(r.location_check.message || "")}">Barangay / city mismatch</span>` : ""}${!place(r) ? `<button type="button" class="btn sm assign" data-assign="${esc(r.pole_code)}">Set barangay</button>` : ""}</td>
        <td data-label="Date" class="mono when"><time datetime="${esc(r.created_at)}" title="${esc(dayTime(r.created_at))}">${esc(shortDay(r.created_at))}<span class="when-t">${esc(shortTime(r.created_at))}</span></time></td>
        <td data-label="Detected" class="r num">${r.machine_state === "processed" ? r.detected : "–"}</td>
        <td data-label="Problem" class="r num${r.problem_count ? " warn" : ""}">${r.machine_state === "processed" ? r.problem_count : "–"}</td>
        <td data-label="Risk">${riskCell(r)}</td>
        <td data-label="Review">${reviewCell(r)}</td>
        <td data-label="Admin result">${r.decision ? `<span class="dec-l" title="${esc(r.decision.by || "")}">${esc(r.decision.label)}</span>` : '<span class="muted">–</span>'}</td>
        <td class="act"><button type="button" class="link-btn" data-peek="${r.id}" title="Quick review (Space)">Preview</button><a href="/admin/inspections/${r.id}" data-link class="go">Open</a></td></tr>`).join("")}</tbody></table></div>
      <div class="tbl-foot">${pager(d.page, d.pages, d.matched, d.per_page, "inspections")}
        <label class="per"><span>Rows</span><select id="per">${[25, 50, 100].map((v) => `<option ${v === d.per_page ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>`
      : `<p class="empty">${d.total ? "No inspection matches these filters." : "No inspections yet. Screen a photo in the Field Screener to start."}</p>`;
    drawBulk();
  }
  function load() {
    const n = ++reqN;
    const q = qs(false), aq = qs(true);
    history.replaceState({}, "", `/admin/inspections${q.toString() ? "?" + q : ""}`);
    const ex = new URLSearchParams(aq); ex.delete("page"); ex.delete("per_page"); ex.delete("tab"); ex.delete("sort"); ex.delete("desc"); ex.delete("include_test");
    $("#csv").href = `/api/admin/exports/inspections.csv?${ex}`;
    aq.set("per_page", st.per); aq.set("page", st.page);
    return api(`/api/admin/inspections?${aq}`).then((d) => {
      if (n !== reqN) return;
      if (!st.facets) {
        st.facets = d.facets;
        fp.barangay.innerHTML = `<option value="">Any</option>${d.facets.barangays.map((b) => `<option ${b === st.f.barangay ? "selected" : ""}>${esc(b)}</option>`).join("")}
          ${d.facets.no_barangay ? `<option value="__none__" ${st.f.barangay === "__none__" ? "selected" : ""}>No barangay</option>` : ""}`;
      }
      draw(d);
    }).catch(fail($("#list")));
  }
  const reload = () => { st.page = 1; st.sel.clear(); load(); };
  $("#q").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value.trim(); reload(); }, 220); });
  fbtn.addEventListener("click", () => { const o = fp.hidden; fp.hidden = !o; fbtn.setAttribute("aria-expanded", String(o)); });
  const txb = $("#tx-btn"), txp = $("#tx-pop");  // v2.23: technical exports, moved here from the removed System page
  txb?.addEventListener("click", () => { const o = txp.hidden; txp.hidden = !o; txb.setAttribute("aria-expanded", String(o)); });
  const closeTx = (e) => { if (txp && !txp.hidden && !e.target.closest(".pg-actions .more")) { txp.hidden = true; txb.setAttribute("aria-expanded", "false"); } };
  document.addEventListener("click", closeTx);
  const closePop = (e) => { if (!fp.hidden && !e.target.closest(".toolbar .more")) { fp.hidden = true; fbtn.setAttribute("aria-expanded", "false"); } };
  document.addEventListener("click", closePop);
  const esck = (e) => { if (e.key === "Escape" && !fp.hidden) { fp.hidden = true; fbtn.setAttribute("aria-expanded", "false"); fbtn.focus(); } };
  document.addEventListener("keydown", esck);
  fp.addEventListener("change", () => {
    for (const k of POP_FILTERS) st.f[k] = fp[k].value;
    st.latest_only = fp.latest_only.checked; st.include_test = !!fp.include_test?.checked; st.mismatch = fp.mismatch.checked;
    reload();
  });
  fp.addEventListener("submit", (e) => e.preventDefault());
  $("#clear").addEventListener("click", () => {
    st.q = ""; st.risk = ""; st.review = ""; st.tab = "all"; st.screen = ""; st.latest_only = false; st.include_test = false; st.mismatch = false;
    for (const k of POP_FILTERS) st.f[k] = "";
    fp.reset(); $("#q").value = ""; reload();
  });
  const root = view.querySelector(".page");
  const peek = quickReview(() => lastData?.items || [], () => load());
  root.addEventListener("click", async (e) => {
    const pk = e.target.closest("[data-peek]");
    if (pk) { e.preventDefault(); e.stopPropagation(); peek.open(pk.dataset.peek); return; }
    const rtb = e.target.closest("[data-rtab]");
    if (rtb) { st.review_status = rtb.dataset.rtab; return reload(); }
    const scb = e.target.closest("[data-screen]");
    if (scb) { st.screen = st.screen === scb.dataset.screen ? "" : scb.dataset.screen; return reload(); }
    const un = e.target.closest("[data-unset]");
    if (un) { st[un.dataset.unset] = un.dataset.unset === "tab" ? "all" : ""; return reload(); }
    const sb = e.target.closest("[data-sort]");
    if (sb) { st.sort = cycleSort(st.sort, sb.dataset.sort, { key: "triage", desc: false }); st.page = 1; return load(); }
    const cf = e.target.closest("[data-colflt]");
    if (cf) {
      const pop = $(`[data-colpop="${cf.dataset.colflt}"]`), open = pop.hidden;
      document.querySelectorAll("[data-colpop]").forEach((p) => { p.hidden = true; }); document.querySelectorAll("[data-colflt]").forEach((b) => b.setAttribute("aria-expanded", "false"));
      pop.hidden = !open; cf.setAttribute("aria-expanded", String(open)); return;
    }
    const cs = e.target.closest("[data-colset]");
    if (cs) { if (cs.dataset.colset === "risk") st.screen = cs.dataset.v; else st.review_status = cs.dataset.v; return reload(); }
    const pb = e.target.closest("[data-page]");
    if (pb && !pb.disabled) { st.page = Number(pb.dataset.page); await load(); $(".toolbar").scrollIntoView({ block: "nearest" }); return; }
    const as = e.target.closest("[data-assign]");
    if (as) {
      const v = await pickBarangay(`Pole ${as.dataset.assign}`);
      if (!v) return;
      try { await api(`/api/admin/poles/${encodeURIComponent(as.dataset.assign)}/barangay`, json("PUT", { barangay: v })); toast.success("Barangay assigned"); st.facets = null; load(); }
      catch (ex) { toast.error("Not assigned", { description: ex.message }); }
    }
  });
  root.addEventListener("change", (e) => {
    if (e.target.id === "per") { st.per = Number(e.target.value); return reload(); }
    const cb = e.target.closest("[data-sel]");
    if (cb) { cb.checked ? st.sel.add(cb.dataset.sel) : st.sel.delete(cb.dataset.sel); cb.closest("tr").toggleAttribute("aria-selected", cb.checked); drawBulk(); }
    if (e.target.closest("[data-all]")) {
      const on = e.target.checked;
      root.querySelectorAll("#list [data-sel]").forEach((x) => { x.checked = on; on ? st.sel.add(x.dataset.sel) : st.sel.delete(x.dataset.sel); x.closest("tr").toggleAttribute("aria-selected", on); });
      drawBulk();
    }
  });
  bindRowLinks($("#list"));
  mountBatch($("#batch"), $("#batch-btn"), load);
  load();
  return () => { peek.destroy(); clearTimeout(t); document.removeEventListener("click", closePop); document.removeEventListener("click", closeTx); document.removeEventListener("keydown", esck); };
}
/** S16 quick review: a side drawer over the list. J / K (or the arrows in the drawer) step through the rows on this
 *  page. S18: it shows the shared compact result view and records the same decision as the inspection page. */
function quickReview(rowsOf, onChanged) {
  const d = document.createElement("aside");
  d.className = "qr"; d.hidden = true; d.setAttribute("aria-label", "Quick review"); d.tabIndex = -1;
  document.body.append(d);
  let cur = null, changed = false, rvq = null;
  const close = () => { rvq?.destroy(); rvq = null; d.hidden = true; if (changed) { changed = false; onChanged(); } document.querySelector(`[data-peek="${cur}"]`)?.focus(); };
  const step = (k) => { const rows = rowsOf(); const i = rows.findIndex((r) => r.id === cur); const n = rows[i + k]; if (n) open(n.id); };
  async function open(id) {
    cur = id; d.hidden = false;
    document.querySelectorAll(".tbl-insp tr[data-current]").forEach((x) => x.removeAttribute("data-current"));
    document.querySelector(`[data-peek="${id}"]`)?.closest("tr")?.setAttribute("data-current", "");
    const row = rowsOf().find((r) => r.id === id) || {};
    d.innerHTML = `<header class="qr-h"><div><b>${inspName(row)}</b><span>${reviewCell(row)}</span></div>
      <div class="qr-nav"><button class="btn sm" type="button" data-qr="-1" aria-label="Previous (K)">↑</button><button class="btn sm" type="button" data-qr="1" aria-label="Next (J)">↓</button><button class="btn sm" type="button" data-qr="x" aria-label="Close (Esc)">Close</button></div></header>
      <div class="qr-b">${loading}</div>`;
    d.focus();
    let r;
    try { r = await api(`/api/analysis/${id}/result`); } catch (ex) { if (cur === id) d.querySelector(".qr-b").innerHTML = `<p class="empty">${esc(ex.message)}</p>`; return; }
    if (cur !== id) return;
    // S18: the same result view as the citizen page and the inspection sheet (compact), and the same review panel
    const lc = row.location_check;
    const body = d.querySelector(".qr-b");
    body.innerHTML = `${lc?.consistent === false ? `<p class="banner banner-warn">${esc(lc.message)}</p>` : ""}
      ${resultViewHtml(r, { mode: "compact", nextHtml: `<section class="rvw rvw-compact" id="qr-rvw">${loading}</section>` })}
      <p class="qr-open"><a class="btn" href="/admin/inspections/${id}" data-link>Open the full inspection</a></p>`;
    rvq?.destroy();
    rvq = r.status === "error" && !r.image?.url ? null : bindResultView(body, r, { initialLayer: "problems" });
    mountReviewPanel(body.querySelector("#qr-rvw"), id, { onChange: () => { changed = true; } });
  }
  d.addEventListener("click", (e) => {
    const b = e.target.closest("[data-qr]"); if (b) { b.dataset.qr === "x" ? close() : step(Number(b.dataset.qr)); }
    if (e.target.closest("a[data-link]")) d.hidden = true;
  });
  const key = (e) => {
    if (d.hidden && e.key === " " && e.target.matches?.(".tbl-insp tr[data-href]")) {
      const b = e.target.querySelector("[data-peek]"); if (b) { e.preventDefault(); open(b.dataset.peek); } return;
    }
    if (d.hidden || e.target.closest("textarea, input, select")) return;
    if (e.key === "Escape") close(); else if (e.key === "j" || e.key === "J") step(1); else if (e.key === "k" || e.key === "K") step(-1);
  };
  document.addEventListener("keydown", key);
  return { open, destroy: () => { document.removeEventListener("keydown", key); d.remove(); } };
}

const assessCell = (r) => {
  if (r.risk === "failed") return '<span class="badge badge-bad">Failed</span>';
  if (r.risk === "processing") return '<span class="muted">Processing</span>';
  if (!r.wires_total) return '<span class="muted">No wires</span>';
  if (r.completeness === "complete") return '<span class="badge badge-ok">Complete</span>';
  return `<span class="prog" title="${r.wires_assessed} of ${r.wires_total} wires assessed"><i style="--p:${(r.wires_assessed / r.wires_total) * 100}%"></i><span class="num">${r.wires_assessed}/${r.wires_total}</span></span>`;
};

/** Several photos at once (moved here from the retired Exports page). Each becomes its own inspection. */
function mountBatch(el, btn, onDone) {
  el.innerHTML = `<header class="block-head"><h2>Upload photos</h2><span class="muted">Each photo becomes its own inspection</span></header>
    <label class="drop"><input type="file" id="b-files" multiple accept="${ACCEPT},.txt,text/plain"><span>Choose photos <small class="muted">JPEG, HEIC, PNG, WebP, TIFF, BMP or GIF, up to ${DEFAULT_MAX_MB} MB each</small></span></label>
    <div class="b-loc"><button class="btn sm" type="button" id="b-locate">Use my current location</button>
      <span class="b-loc-st" id="b-loc-st" aria-live="polite">Optional. Saves this device's GPS position with every photo in this upload.</span>
      <button class="link-btn" type="button" id="b-loc-x" hidden>Remove location</button></div>
    <div class="batch-opts">
      <label class="field"><span>City <i>optional, for every photo</i></span><input id="b-city" maxlength="120" autocomplete="address-level2"></label>
      <label class="field"><span>Barangay <i>optional, for every photo</i></span><input id="b-brgy" maxlength="120"></label>
      <label class="field"><span>Inspection name <i>optional</i></span><input id="b-name" maxlength="120" placeholder="e.g. Rizal St. corner, morning check"></label>
      <label class="chk"><input type="checkbox" id="b-stem" checked> Use each file name as the pole ID</label></div>
    <ol class="batch-list" id="b-list"></ol>
    <wf-notice variant="upload"></wf-notice>
    <button class="btn primary" id="b-run" disabled>Screen 0 photos</button>`;
  btn.addEventListener("click", () => { el.hidden = !el.hidden; btn.setAttribute("aria-expanded", String(!el.hidden)); });
  let pairs = [];
  // S18 (Phase 10): the Field Screener's location capture (location.js). The coordinates come from the device; the
  // address lookup only pre-fills empty City / Barangay boxes, which stay editable; nothing is substituted on failure.
  let coords = null, place = null;
  const locSt = (t, cls = "") => { const s = $("#b-loc-st", el); s.textContent = t; s.className = `b-loc-st ${cls}`; };
  $("#b-locate", el).addEventListener("click", async (e) => {
    const b = e.currentTarget; b.disabled = true; locSt("Waiting for your browser…");
    try { coords = await locate((t) => locSt(t)); }
    catch (ex) { coords = null; place = null; b.disabled = false; $("#b-loc-x", el).hidden = true; locSt(ex.message, "err"); return; }
    locSt(`Got ${fixLabel(coords)}. Looking up the address…`);
    place = await reverseLookup(coords);
    const city = $("#b-city", el), brgy = $("#b-brgy", el);
    if (place?.city && !city.value.trim()) city.value = place.city;
    if (place?.barangay && !brgy.value.trim()) brgy.value = place.barangay;
    b.disabled = false; b.textContent = "Update location"; $("#b-loc-x", el).hidden = false;
    locSt(place ? `Device location ${fixLabel(coords)}. Address lookup: ${[place.barangay, place.city].filter(Boolean).join(", ")}. Check the city and barangay below.`
      : `Device location ${fixLabel(coords)}. The address could not be looked up; type the city and barangay if you know them.`, "ok");
  });
  $("#b-loc-x", el).addEventListener("click", () => { coords = null; place = null; $("#b-loc-x", el).hidden = true; $("#b-locate", el).textContent = "Use my current location"; locSt("Location removed. It will not be saved with these photos."); });
  const render = () => {
    $("#b-list", el).innerHTML = pairs.map((p) => `<li><span class="mono">${esc(p.img.name)}</span>
      <span class="b-state ${p.state}">${esc(p.msg || { ready: "Ready", uploading: "Uploading", queued: "Processing", done: "Done", failed: "Failed" }[p.state])}</span>
      ${p.id ? `<a class="link" href="/admin/inspections/${p.id}" data-link>Open</a>` : "<span></span>"}</li>`).join("");
    const n = pairs.filter((p) => p.state === "ready").length;
    const b = $("#b-run", el); b.disabled = !n; b.textContent = `Screen ${n} photo${n === 1 ? "" : "s"}`;
  };
  $("#b-files", el).addEventListener("change", (e) => {
    const files = [...e.target.files];
    const labels = new Map(files.filter((x) => x.name.toLowerCase().endsWith(".txt")).map((x) => [x.name.replace(/\.[^.]+$/, ""), x]));
    // S18: the Field Screener's rules (HEIC included even when the browser reports no MIME type)
    pairs = files.filter((x) => !x.name.toLowerCase().endsWith(".txt")).map((x) => {
      const bad = checkPhoto(x);
      return { img: x, labels: labels.get(x.name.replace(/\.[^.]+$/, "")) || null, state: bad ? "failed" : "ready", msg: bad || "" };
    });
    render();
  });
  $("#b-run", el).addEventListener("click", async () => {
    const stem = $("#b-stem", el).checked, brgy = $("#b-brgy", el).value.trim(), city = $("#b-city", el).value.trim(), name = $("#b-name", el).value.trim();
    for (const p of pairs.filter((x) => x.state === "ready")) {
      p.state = "uploading"; render();
      const fd = new FormData(); fd.append("file", p.img);
      if (p.labels) fd.append("labels", p.labels);
      if (brgy) fd.append("barangay", brgy);
      if (city) fd.append("city", city);
      if (name) fd.append("inspection_name", name);
      appendLocation(fd, coords, place);
      if (stem) fd.append("pole_code", p.img.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_.-]/g, "-").slice(0, 64));
      try {
        const r = await api("/api/analysis/upload", { method: "POST", body: fd });
        p.state = "queued"; p.id = r.id; render();
        waitForAnalysis(r.id).then((s) => { p.state = s.status === "error" ? "failed" : "done"; render(); onDone(); });
      } catch (ex) { p.state = "failed"; p.msg = ex.message; render(); }
    }
  });
}

// ------------------------------------------------------------------ inspection sheet
/** S18: deleting is a separate administrative block at the end of the page, never beside routine actions. It archives
 *  (soft delete): nothing is removed, the record leaves the operational views and can be restored. */
function recordAdminHtml(c, row) {
  if (c.archived) {
    return `<section class="block rec-adm" aria-labelledby="ra-h"><header class="block-head"><h2 id="ra-h">Deleted inspection</h2></header>
      <p>Deleted (archived) by ${esc(c.archived.by || "an administrator")} on ${esc(dayTime(c.archived.at))}. Reason: ${esc(c.archived.reason || "–")}</p>
      ${c.can.restore ? `<button class="btn" type="button" id="restore">Restore inspection</button>` : ""}</section>`;
  }
  if (!c.can.delete) return "";
  return `<section class="block rec-adm" aria-labelledby="ra-h"><header class="block-head"><h2 id="ra-h">Delete inspection</h2></header>
    <p class="muted">Removes this inspection of ${esc(row.label || row.pole_code)} from the dashboard, lists, map and exports. The photo, findings, decisions and audit history are kept and an administrator can restore it.</p>
    <button class="btn danger" type="button" id="delete">Delete inspection…</button></section>`;
}

/** S18: the confirmation for "Delete inspection": names the record, states the consequence, needs a reason. */
function confirmDelete(row, r) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "pick del-dlg";
    dlg.setAttribute("aria-labelledby", "del-h");
    dlg.innerHTML = `<form class="pick-f" id="del-f" novalidate><header class="pick-h"><h2 id="del-h">Delete this inspection?</h2>
        <button class="btn icon sm" type="button" data-close aria-label="Close">×</button></header>
      <dl class="kv"><div><dt>Pole</dt><dd>${esc(row.label || row.pole_code)} <span class="mono muted">${esc(row.pole_code)}</span></dd></div>
        <div><dt>Inspection</dt><dd class="mono">${esc(r.report_id || r.id)}</dd></div>
        <div><dt>Photo</dt><dd>${esc(r.image?.original_filename || "–")}</dd></div></dl>
      <p>It disappears from the dashboard, the inspection list, the map, counts and exports. Nothing is erased: the photo, findings, decisions and audit history stay, and it can be restored from its own page.</p>
      <label class="field"><span>Reason <i>required</i></span><textarea name="reason" rows="2" maxlength="300" placeholder="e.g. Duplicate upload of the same photo"></textarea></label>
      <p class="form-error" id="del-err" hidden>Give a reason (at least 3 characters).</p>
      <div class="pick-acts"><button class="btn" type="button" data-close>Cancel</button><button class="btn danger" type="submit">Delete inspection</button></div></form>`;
    document.body.append(dlg);
    let out = null;
    const f = dlg.querySelector("#del-f");
    dlg.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) dlg.close(); });
    f.addEventListener("submit", (e) => {
      e.preventDefault();
      const reason = f.reason.value.trim();
      if (reason.length < 3) { f.querySelector("#del-err").hidden = false; f.reason.focus(); return; }
      out = reason; dlg.close();
    });
    dlg.addEventListener("close", () => { resolve(out); dlg.remove(); });
    dlg.showModal(); f.reason.focus();
  });
}

function mountInspection(id) {
  let viewer = null, alive = true;
  view.innerHTML = `<section class="page page-wide"><div id="sheet">${loading}</div></section>`;
  const el = $("#sheet");
  const load = async () => {
    const s = await api(`/api/analysis/${id}`);
    if (s.status === "processing") {
      el.innerHTML = `<p class="empty">This photo is still being processed. The inspection opens when it is ready.</p>`;
      await waitForAnalysis(id);
      if (!alive) return;
    }
    const [r, c] = await Promise.all([api(`/api/analysis/${id}/result`), api(`/api/admin/inspections/${id}/context`)]);
    if (alive) render(r, c);
  };
  const headHtml = (r, c, row, done) => `
      <nav class="crumbs" aria-label="Breadcrumb"><a href="/admin/inspections" data-link>Inspections</a><span aria-hidden="true">/</span><span>${esc(row.label || row.pole_code)}</span></nav>
      <header class="pg-head sheet-head">
        <div><h1 class="pg-title">${esc(row.label || row.pole_code)}${row.label && row.label !== row.pole_code ? ` <span class="pg-title-code mono">${esc(row.pole_code)}</span>` : ""}</h1>
          <p class="pg-sub">${[row.inspection_name ? esc(row.inspection_name) : "", place(row) ? esc(place(row)) : "", esc(dayTime(row.created_at))].filter(Boolean).join(" · ")}</p></div>
        <div class="pg-actions">
          ${done ? `<a class="btn" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener">PDF report</a>
            <a class="btn" href="/api/admin/inspections/${r.id}/findings.csv">Findings CSV</a>` : ""}
        </div>
      </header>
      ${c.archived ? `<p class="banner banner-warn">This inspection was deleted (archived) on ${esc(dayTime(c.archived.at))}. It is kept for the record and left out of every operational view.</p>` : ""}
      ${c.record_kind === "test" ? `<p class="banner banner-quiet">Test record${c.record_kind_reason ? `: ${esc(c.record_kind_reason)}` : ""}. It is left out of the dashboard, the inspection list, the map and the exports.</p>` : ""}`;

  /** v2.20: a failed run is a compact state, not a page of empty tables. */
  const renderFailed = (r, c, row) => {
    el.innerHTML = `${headHtml(r, c, row, false)}
      <section class="fail-card" aria-labelledby="fail-h">
        <div class="fail-main">
          <p class="fail-k">Processing status</p>
          <h2 id="fail-h">${riskBadge("failed", true)}</h2>
          <p>${esc(r.error?.summary || "The photo could not be processed.")}</p>
          <p class="muted">No result was produced, so this pole has not been screened. This is not a finding that the wires are in good condition.</p>
          <p class="fail-retry">${c.can.retry ? "Processing can be retried on the stored photo." : "Retry is not available for this record. Upload the photo again from the Field Screener."}</p>
          <div class="fail-acts">${c.can.retry ? `<button class="btn primary" type="button" id="retry">Retry processing</button>` : ""}
            <a class="btn" href="/workspace" target="_blank" rel="noopener">Upload again</a></div>
        </div>
        <dl class="kv fail-kv">
          <div><dt>Submitted</dt><dd>${esc(dayTime(row.created_at))}</dd></div>
          <div><dt>Source photo</dt><dd>${esc(r.image?.original_filename || "–")}</dd></div>
          <div><dt>Submitted from</dt><dd>${r.submitted_via === "field" ? "Field Screener" : "Admin upload"}</dd></div>
        </dl>
      </section>
      <section class="rvw" id="rvw" aria-label="Administrative review">${loading}</section>
      ${historyHtml(r, c)}
      ${recordAdminHtml(c, row)}
      ${isAdmin() && r.error ? `<details class="diag"><summary><b>View system trace</b><span>Administrators: the error as the server recorded it (file paths removed)</span></summary>
        <div class="diag-body"><dl class="kv"><div><dt>Error code</dt><dd class="mono">${esc(r.error.code || "–")}</dd></div></dl>
        <pre class="trace">${esc(r.error.message || "No detail recorded.")}</pre></div></details>` : ""}`;
    mountReviewPanel($("#rvw", el), r.id); // S18: a failed inspection is reviewed and decided like any other
    bindCommon(r, c, row);
  };

  const historyHtml = (r, c) => c.history.length > 1 ? `<section class="block hist-block" aria-labelledby="hi-h">
      <header class="block-head"><h2 id="hi-h">Inspections of this pole</h2><span class="count num">${c.history.length}</span></header>
      <ol class="hist">${c.history.map((h) => `<li ${h.id === r.id ? 'aria-current="true"' : `data-href="/admin/inspections/${h.id}" tabindex="0"`}>
        <time class="mono">${esc(shortDay(h.created_at))}</time>${riskBadge(h.risk)}${h.review === "not_reviewed" ? "<span></span>" : reviewText(h.review, h)}${h.id === r.id ? '<span class="muted">This one</span>' : '<span></span>'}</li>`).join("")}</ol></section>` : "";

  const bindCommon = (r, c, row) => {
    bindRowLinks(el.querySelector(".hist") || document.createElement("div"));
    $("#retry", el)?.addEventListener("click", async (e) => {
      e.target.disabled = true;
      try { await api(`/api/analysis/${r.id}/retry`, { method: "POST" }); toast.success("Processing again"); load(); }
      catch (ex) { e.target.disabled = false; toast.error("Retry not possible", { description: ex.message }); }
    });
    $("#delete", el)?.addEventListener("click", async () => {
      const reason = await confirmDelete(row, r);
      if (!reason) return;
      try { await api(`/api/admin/inspections/${r.id}/archive`, json("POST", { reason })); toast.success("Inspection deleted", { description: "Archived; it can be restored from this page." }); load(); }
      catch (ex) { toast.error("Not deleted", { description: ex.message }); }
    });
    $("#restore", el)?.addEventListener("click", async () => {
      try { await api(`/api/admin/inspections/${r.id}/restore`, { method: "POST" }); toast.success("Inspection restored"); load(); }
      catch (ex) { toast.error("Not restored", { description: ex.message }); }
    });
  };

  /** S18: the shared result view (What we know / How did we know) with the administrative review as section B. The
   *  machine screening and the decision stay separate; the wire table, details, history and diagnostics are kept. */
  const render = (r, c) => {
    const row = c.row;
    Object.assign(ctx, { analysisId: r.id, label: `${row.label || row.pole_code}` });
    assistant?.context();
    if (r.status === "error") return renderFailed(r, c, row);
    const lims = limitations(r), recs = recommendations(r, assessment(r), lims);
    const hasWires = r.wires.length > 0;
    const loc = r.pole.location;
    const done = c.can.review;
    const scale = r.comparison?.meter_scale_max || 0.1;
    const wires = [...r.wires].sort((x, y) => (y.finding?.flagged ? 1 : 0) - (x.finding?.flagged ? 1 : 0) || (y.sag_ratio ?? -1) - (x.sag_ratio ?? -1) || x.index - y.index);
    const dg = r.diagnosis;
    el.innerHTML = `${headHtml(r, c, row, done)}
      ${r.guardrail?.requires_retake ? `<div class="banner banner-warn"><p><b>Some wires could not be identified reliably from this photo</b>${r.guardrail.choice?.action === "proceed" ? ", and the person who took it chose to proceed" : ""}. Check the evidence layers before deciding.</p></div>` : ""}
      ${resultViewHtml(r, { mode: "admin", nextHtml: `<section class="rvw" id="rvw" aria-label="Administrative review">${loading}</section>` })}

      ${hasWires ? `<details class="diag" id="wf-tbl"><summary><b>All wire measurements</b><span>${r.wires.length} wires: type, sag, reference comparison and coverage</span></summary>
        <div class="diag-body"><div class="tbl-scroll"><table class="tbl tbl-wires"><thead><tr><th>Wire</th><th>Type</th><th class="r">Estimated sag</th><th>Level</th><th>Result</th><th>${r.comparison ? `Reference ${(r.comparison.sag_ratio * 100).toFixed(1)}%` : "Reference"}</th></tr></thead><tbody>
          ${wires.map((w) => `<tr data-wire="${w.index}" tabindex="0">
            <td><span class="wno" style="--wc:${wireColor(w, "class")}">${w.index}</span></td><td>${esc(w.finding?.type || classLabel(w, true))}</td>
            <td class="r num">${w.finding?.sag ? `${esc(w.finding.sag.text)}${w.finding.sag.partial ? "*" : ""}` : "—"}</td>
            <td>${w.finding?.meter_level ? `<span class="sm2-lv lv-${esc(w.finding.meter_level)}"><span class="sm2-lv">${esc(w.finding.meter_level_label)}</span></span>` : "—"}</td>
            <td>${w.finding ? `<span class="fd fd-${esc(w.finding.category)}">${esc(w.finding.label)}</span>` : esc(finding(w))}</td>
            <td>${w.comparison?.position ? `<span class="cmp ${w.comparison.position}">${{ below: "Below", at: "At", above: "Above" }[w.comparison.position]} ${w.comparison.difference_pp > 0 ? "+" : ""}${w.comparison.difference_pp.toFixed(1)}</span>` : w.comparison || w.sag_ratio == null || w.status === "service_drop" ? "—" : `<span class="muted" title="${esc(REF_WHY[w.states?.reference_unavailable_reason] || "No applicable reference")}">Reference unavailable</span>`}</td></tr>`).join("")}</tbody></table></div>
          <p class="hint">* visible part of the span only. Select a row to show the wire in the evidence view.</p></div></details>` : ""}

      <div class="sheet-cols">
        <section class="block" aria-labelledby="ii-h">
          <header class="block-head"><h2 id="ii-h">Inspection details</h2></header>
          <dl class="kv">
            <div><dt>File</dt><dd class="mono">${esc(r.image.original_filename || "–")} <span class="muted">${r.image.width} × ${r.image.height}</span></dd></div>
            <div><dt>Pole</dt><dd>${row.label && row.label !== row.pole_code ? `${esc(row.label)} <span class="mono muted">${esc(row.pole_code)}</span>` : `<span class="mono">${esc(row.pole_code)}</span>`}</dd></div>
            ${row.inspection_name ? `<div><dt>Inspection name</dt><dd>${esc(row.inspection_name)}</dd></div>` : ""}
            <div><dt>Barangay</dt><dd class="brgy-dd">${row.barangay
              ? `<span class="brgy-chip">${esc(row.barangay)}${c.can.review !== false ? `<button type="button" class="brgy-x" id="clear-brgy" aria-label="Remove the barangay ${esc(row.barangay)}" title="Remove this barangay">×</button>` : ""}</span>${c.can.review !== false ? ` <button type="button" class="link-btn" id="set-brgy">Change</button>` : ""}`
              : `<span class="muted">Not assigned</span>${c.can.review !== false ? ` <button type="button" class="link-btn" id="set-brgy">Assign</button>` : ""}`}</dd></div>
            ${row.city ? `<div><dt>City</dt><dd>${esc(row.city)}</dd></div>` : ""}
            <div><dt>Location</dt><dd>${loc ? `<span class="mono">${loc.lat.toFixed(6)}, ${loc.lon.toFixed(6)}</span> <span class="muted">(${loc.source === "exif" ? "photo GPS" : loc.capture?.method === "device" ? `device location${loc.capture.accuracy_m != null ? `, ±${Math.round(loc.capture.accuracy_m)} m` : ""}` : loc.source === "manual" ? "entered or pinned" : "entered"})</span>${loc.capture?.geocoded ? ` <span class="muted">· address lookup: ${esc([loc.capture.geocoded.barangay, loc.capture.geocoded.city].filter(Boolean).join(", "))}</span>` : ""}
              <a class="link" href="/admin/map?pole=${encodeURIComponent(row.pole_code)}" data-link>Show on map</a>` : `<span class="muted">No GPS recorded</span>${isAdmin() ? ` <a class="link" href="/admin/map?pole=${encodeURIComponent(row.pole_code)}&tab=pinning" data-link>Drop a pin</a>` : ""}`}</dd></div>
            <div><dt>Submitted from</dt><dd>${r.submitted_via === "field" ? "Field Screener" : "Admin upload"}</dd></div>
            <div><dt>Inspection ID</dt><dd class="mono">${esc(r.report_id || "")} <span class="muted">${esc(r.id)}</span></dd></div>
          </dl>
        </section>
        ${historyHtml(r, c) || "<div></div>"}
      </div>

      <details class="diag sh-tech"><summary><b>Technical details</b><span>Photo limitations, what was observed, recommended steps and other metrics</span></summary>
        <div class="diag-body findings">
          ${lims.length ? `<h3>Photo limitations</h3><ul class="fd-list">${lims.map((l) => `<li>${esc(l.text)}${l.ids.length ? ` <span class="muted">(${SECTION(l.ids)})</span>` : ""}${l.help ? `<span class="help">${esc(l.help)}</span>` : ""}</li>`).join("")}</ul>` : ""}
          ${dg && hasWires && dg.observations.length ? `<h3>Observed in the image</h3><ul class="fd-list">${dg.observations.map((x) => `<li>${wireRefs(esc(x), r.wires)}.</li>`).join("")}</ul>` : ""}
          <h3>Recommended steps</h3>
          <ol class="fd-list">${recs.map((x) => `<li>${x}</li>`).join("")}</ol>
          ${otherMetricsHtml(r)}
          <wf-notice variant="full" class="sheet-notice"></wf-notice>
        </div></details>
      ${recordAdminHtml(c, row)}`;

    viewer?.destroy();
    const rv = bindResultView(el, r, { initialLayer: hasWires ? "problems" : "original" });
    viewer = rv.viewer;
    const table = el.querySelector(".tbl-wires");
    table?.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-wire]"); if (tr) { rv.select(Number(tr.dataset.wire)); el.querySelector(".rv-how")?.scrollIntoView({ block: "start", behavior: "smooth" }); } });
    table?.addEventListener("keydown", (e) => { const tr = e.target.closest("tr[data-wire]"); if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); rv.select(Number(tr.dataset.wire)); } });
    mountReviewPanel($("#rvw", el), r.id);
    $("#set-brgy", el)?.addEventListener("click", async () => {
      const v = await pickBarangay(`${row.label || row.pole_code} · ${row.pole_code}`, row.barangay);
      if (!v || v === row.barangay) return; // closing or cancelling the picker changes nothing
      try { await api(`/api/admin/poles/${encodeURIComponent(row.pole_code)}/barangay`, json("PUT", { barangay: v })); toast.success(row.barangay ? "Barangay changed" : "Barangay assigned"); load(); }
      catch (ex) { toast.error("Not saved", { description: ex.message }); }
    });
    $("#clear-brgy", el)?.addEventListener("click", async () => {
      try { await api(`/api/admin/poles/${encodeURIComponent(row.pole_code)}/barangay`, { method: "DELETE" }); toast.success("Barangay removed", { description: "The location and coordinates are unchanged." }); load(); }
      catch (ex) { toast.error("Not removed", { description: ex.message }); }
    });
    bindCommon(r, c, row);
  };
  load().catch(fail(el));
  return () => { alive = false; viewer?.destroy(); };
}

boot();
