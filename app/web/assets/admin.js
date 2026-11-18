// WIREFRAME admin portal (v2.20; first written v2.19). Three operational destinations, Dashboard, Inspections and Map & Dispatch, plus
// (v2.23: the System page was removed; technical exports are on Inspections, diagnostics on each inspection).
// Every API it calls is guarded server-side (inspector vs admin included); this file decides presentation only.
// Operational pages show plain statuses (risk level, assessment, review); scores, fit parameters and formulas stay
// in System and in the collapsed technical diagnostics.

import { api, json, esc, ago, waitForAnalysis } from "./lib.js?v=2.23";
import { OverlayViewer, wireColor, classLabel, isMeasured } from "./viewer.js?v=2.23";
import { assessment, limitations, recommendations, finding, group, CONDITION, SECTION } from "./findings.js?v=2.23";
import { onStatus, startStatus } from "./status.js?v=2.23";
import { toast } from "./toast.js?v=2.23";
import { mountMap } from "./admin-map.js?v=2.23";
import { diagnosticsHtml, bindDiagnostics } from "./admin-diagnostics.js?v=2.23";
import { mountAssistant } from "./assistant.js?v=2.23";

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
  if (w.high) return `${w.high} wire section${w.high === 1 ? "" : "s"} at high risk`;
  if (w.medium) return `${w.medium} wire section${w.medium === 1 ? "" : "s"} at moderate risk`;
  if (r.risk === "unassessed") return r.wires_total ? "Assessment incomplete: no wire section could be assessed" : "Assessment incomplete: no wire detected";
  return "";
};
const shortDay = (iso) => new Date(iso).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
const shortTime = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

/** Selection + bulk actions over the visible rows. Every action is a real backend operation, recorded in the
 *  audit trail; re-classifying records is for administrators and asks for confirmation. */
function bulkBar(el, getSelected, onDone) {
  const draw = () => {
    const ids = getSelected();
    el.hidden = !ids.length;
    if (!ids.length) { el.innerHTML = ""; return; }
    el.innerHTML = `<span class="bk-n"><b class="num">${ids.length}</b> selected</span>
      <button class="btn sm" type="button" data-bk="assign_barangay">Assign barangay</button>
      ${isAdmin() ? `<button class="btn sm" type="button" data-bk="mark_test">Mark as test</button>
        <button class="btn sm" type="button" data-bk="mark_operational">Mark as operational</button>` : ""}
      <button class="link-btn" type="button" data-bk="clear">Clear selection</button>`;
  };
  const run = async (action, extra = {}) => {
    const ids = getSelected();
    try {
      const r = await api("/api/admin/inspections/bulk", json("POST", { ids, action, ...extra }));
      toast.success(`${r.updated} inspection${r.updated === 1 ? "" : "s"} updated`); onDone(true);
    } catch (ex) { toast.error("Not changed", { description: ex.message }); }
  };
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-bk]"); if (!b) return;
    const n = getSelected().length;
    if (b.dataset.bk === "clear") return onDone(false);
    if (b.dataset.bk === "assign_barangay") { pickBarangay(`${n} selected inspection${n === 1 ? "" : "s"}`).then((v) => v && run("assign_barangay", { barangay: v })); return; }
    if (b.dataset.bk === "mark_test" && !confirm(`Mark ${n} inspection${n === 1 ? "" : "s"} as test records? They leave the dashboard, the lists, the map and the exports. Nothing is deleted. To bring them back, turn on Filters, Include test records, select them and choose Mark as operational.`)) return;
    if (b.dataset.bk === "mark_operational" && !confirm(`Mark ${n} inspection${n === 1 ? "" : "s"} as operational records? They will count in the dashboard, the map and the exports.`)) return;
    run(b.dataset.bk);
  });
  return draw;
}
const checkboxCell = (id, on) => `<td class="cb"><input type="checkbox" data-sel="${esc(id)}" ${on ? "checked" : ""} aria-label="Select"></td>`;
const sortHead = (key, label, sort, cls = "") => {
  const on = sort.key === key;
  return `<th class="${cls}" aria-sort="${on ? (sort.desc ? "descending" : "ascending") : "none"}"><button type="button" class="th-sort" data-sort="${key}">${label}<i aria-hidden="true">${on ? (sort.desc ? "↓" : "↑") : ""}</i></button></th>`;
};
const pager = (page, pages, matched, per, what = "") => pages <= 1 ? (matched ? `<p class="pager-sum">${matched} ${what}</p>` : "")
  : `<nav class="pager" aria-label="Pages"><span class="pager-sum">${(page - 1) * per + 1}–${Math.min(page * per, matched)} of ${matched} ${what}</span>
    <button class="btn sm" type="button" data-page="${page - 1}" ${page <= 1 ? "disabled" : ""}>Previous</button>
    <span class="num pager-n">${page} / ${pages}</span>
    <button class="btn sm" type="button" data-page="${page + 1}" ${page >= pages ? "disabled" : ""}>Next</button></nav>`;


/** v2.21: choose a barangay from the official list (PSGC). A free-text prompt once stored a person's name as a
 *  barangay; the server refuses anything that is not an official name, and this picker only offers those. */
export function pickBarangay(poleLabel) {
  return new Promise((resolve) => {
    const dlg = document.createElement("dialog");
    dlg.className = "pick";
    dlg.innerHTML = `<form method="dialog" class="pick-f"><h2>Assign a barangay</h2><p class="muted">${esc(poleLabel)}</p>
      <label class="field"><span>Barangay</span><input id="pk-q" autocomplete="off" placeholder="Start typing, e.g. Malagasang" required></label>
      <ol class="pick-res" id="pk-res"></ol><p class="form-error" id="pk-err" hidden></p>
      <div class="pick-acts"><button class="btn" value="cancel" type="submit">Cancel</button><button class="btn primary" id="pk-ok" value="ok" type="submit" disabled>Assign</button></div></form>`;
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
    dlg.addEventListener("close", () => { resolve(dlg.returnValue === "ok" ? chosen : null); dlg.remove(); });
    dlg.showModal(); q.focus();
  });
}

// ------------------------------------------------------------------ dashboard
const DIST = [["high", "High"], ["medium", "Moderate"], ["low", "Low"], ["unassessed", "Assessment incomplete"], ["failed", "No result"]];
const NOTICE_KEY = "wf-dash-notice";
function mountDashboard() {
  let hideNotice = false;
  try { hideNotice = localStorage.getItem(NOTICE_KEY) === "hidden"; } catch { /* storage blocked */ }
  view.innerHTML = `<section class="page">
    <header class="pg-head"><div><h1 class="pg-title">Dashboard</h1>
      <p class="pg-sub">${esc(new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" }))}</p></div>
      <div class="pg-actions"><a class="btn" href="/api/admin/exports/sites.pdf" target="_blank" rel="noopener">Export report</a>
        <a class="btn primary" href="/workspace" target="_blank" rel="noopener">New inspection</a></div></header>
    ${hideNotice ? "" : `<div class="slim-notice" id="slim"><wf-notice variant="brief"></wf-notice><button class="slim-x" type="button" aria-label="Dismiss the notice">×</button></div>`}
    <div id="dash">${loading}</div></section>`;
  $("#slim .slim-x")?.addEventListener("click", () => { $("#slim").remove(); try { localStorage.setItem(NOTICE_KEY, "hidden"); } catch { /* ignore */ } });
  const el = $("#dash");
  const st = { quick: "all", q: "", sort: { key: "triage", desc: false }, page: 1, sel: new Set(), risk: null, rows: [], triage: [] };
  let data = null, t = 0;

  const QUICK = [["all", "All"], ["failed", "Failed"], ["flagged", "Flagged"], ["high", "High"], ["medium", "Moderate"], ["unassessed", "Assessment incomplete"]];
  const quickOk = (r) => st.quick === "all" || (st.quick === "failed" ? ["failed", "processing"].includes(r.risk) : st.quick === "flagged" ? r.review === "flagged" : r.risk === st.quick);
  const RANK = { failed: 0, processing: 0, high: 1, medium: 2, unassessed: 3, low: 4 };
  const SORT = { pole: (r) => r.label || r.pole_code, location: (r) => place(r) || "~", risk: (r) => RANK[r.risk] ?? 9, submitted: (r) => r.created_at };

  function rowsShown() {
    const k = st.q.trim().toLowerCase();
    let rows = (st.risk ? st.rows : st.triage).filter((r) => (st.risk || quickOk(r)) && (!k || [r.pole_code, r.label, r.barangay, r.city, r.inspection_name].some((x) => (x || "").toLowerCase().includes(k))));
    const f = SORT[st.sort.key];
    if (f) rows = [...rows].sort((a, b) => (f(a) < f(b) ? -1 : f(a) > f(b) ? 1 : 0) * (st.sort.desc ? -1 : 1));
    return rows;
  }

  function drawTable() {
    const rows = rowsShown(), pages = Math.max(1, Math.ceil(rows.length / PER_PAGE));
    st.page = Math.min(st.page, pages);
    const pageRows = rows.slice((st.page - 1) * PER_PAGE, st.page * PER_PAGE);
    const all = pageRows.length && pageRows.every((r) => st.sel.has(r.id));
    $("#tq").innerHTML = pageRows.length ? `<table class="tbl tbl-dense tbl-triage">
      <thead><tr><th class="cb"><input type="checkbox" data-all ${all ? "checked" : ""} aria-label="Select all on this page"></th>
        ${sortHead("pole", "Pole", st.sort)}${sortHead("location", "Location", st.sort)}${sortHead("risk", st.risk ? "Risk" : "Issue", st.sort)}${sortHead("submitted", "Submitted", st.sort)}<th class="act">Action</th></tr></thead>
      <tbody>${pageRows.map((r) => `<tr data-href="/admin/inspections/${r.id}" tabindex="0" ${st.sel.has(r.id) ? 'aria-selected="true"' : ""}>
        ${checkboxCell(r.id, st.sel.has(r.id))}
        <td>${poleName(r)}</td><td class="loc">${placeHtml(r)}</td>
        <td><div class="iss">${riskBadge(r.risk)}${r.review === "flagged" ? '<span class="tag tag-flag">Flagged</span>' : ""}</div>${issue(r) && !["failed", "processing"].includes(r.risk) ? `<span class="iss-sub">${esc(issue(r))}</span>` : ""}</td>
        <td class="mono when"><time datetime="${esc(r.created_at)}" title="${esc(dayTime(r.created_at))}">${esc(shortDay(r.created_at))}</time></td>
        <td class="act"><a class="go" href="/admin/inspections/${r.id}" data-link>${r.risk === "failed" ? "Resolve" : "Review"}</a></td></tr>`).join("")}</tbody></table>
      ${pager(st.page, pages, rows.length, PER_PAGE, st.risk ? "poles" : "items")}`
      : `<p class="empty">${st.risk ? "No pole has this result." : st.triage.length ? "Nothing matches." : "Nothing needs attention. Every pole has a finished inspection, and every high-risk, moderate-risk and not-assessed result has a recorded review."}</p>`;
    $("#tq-count").textContent = rows.length;
    drawBulk();
  }

  function render() {
    const p = data.poles, total = p.total;
    const failedN = data.triage.filter((r) => ["failed", "processing"].includes(r.risk)).length;
    const flagged = data.triage.filter((r) => r.review === "flagged").length;
    el.innerHTML = `
      ${data.system.alerts.length ? `<div class="banner" role="status">${data.system.alerts.map((a) => `<p>${esc(a.text)}</p>`).join("")}
</div>` : ""}
      <section class="kpis" aria-label="Summary">
        <a class="kpi" href="/admin/inspections?latest_only=1" data-link><span class="k">Registered poles</span><b class="num">${total}</b>
          <span class="s">Counted from each pole's latest inspection</span></a>
        <div class="kpi"><span class="k">Needs attention</span><b class="num">${data.triage_total}</b>
          <span class="s">${[data.pending_reviews ? `${data.pending_reviews} awaiting review` : "", flagged ? `${flagged} flagged` : "", failedN ? `${failedN} without a result` : ""].filter(Boolean).join(" · ") || "Queue is clear"}</span></div>
        <div class="kpi kpi-dist"><span class="k">Risk distribution</span>
          <div class="dist" role="img" aria-label="${DIST.map(([k, t]) => `${p[k]} ${t.toLowerCase()}`).join(", ")}">
            ${total ? DIST.filter(([k]) => p[k]).map(([k]) => `<i class="d-${k}" style="flex:${p[k]}"></i>`).join("") : `<i class="d-none" style="flex:1"></i>`}</div>
          <ul class="dist-key">${DIST.map(([k, t]) => `<li><button type="button" data-risk="${k}" aria-pressed="${st.risk === k}" ${p[k] ? "" : "disabled"}><i class="d-${k}"></i>${t}<b class="num">${p[k]}</b></button></li>`).join("")}</ul>
        </div>
      </section>
      <div class="work">
        <section class="block work-main" aria-labelledby="tq-h">
          <header class="tq-head">
            <h2 id="tq-h">${st.risk ? `${esc(DIST.find(([k]) => k === st.risk)[1])} ${st.risk === "failed" ? "" : "risk "}poles` : "Needs attention"}</h2><span class="count num" id="tq-count"></span>
            ${st.risk ? `<button class="link-btn" type="button" id="tq-back">Back to Needs attention</button>` : `<div class="chips" role="group" aria-label="Quick filter">${QUICK.map(([k, t]) => `<button type="button" data-quick="${k}" aria-pressed="${st.quick === k}">${t}</button>`).join("")}</div>`}
            <label class="tq-search"><span class="sr-only">Search the queue</span><input type="search" id="tq-q" placeholder="Pole or place" value="${esc(st.q)}"></label>
          </header>
          <div class="bulk" id="bulk-d" hidden></div>
          <div id="tq"></div>
        </section>
        <aside class="block rail" aria-labelledby="au-h">
          <header class="block-head"><h2 id="au-h">Activity</h2></header>
          ${data.activity.length ? `<ol class="audit">${data.activity.map((x) => `<li>
              <p><b>${esc(x.actor)}</b> ${esc(x.text)}${x.target ? ` ${x.analysis_id ? `<a href="/admin/inspections/${x.analysis_id}" data-link>` : ""}<span class="${x.target_label ? "" : "mono"}">${esc(x.target_label || x.target)}</span>${x.analysis_id ? "</a>" : ""}` : ""}${auditDetail(x)}</p>
              <time datetime="${esc(x.at)}" title="${esc(dayTime(x.at))}">${esc(ago(x.at))}</time></li>`).join("")}</ol>`
            : `<p class="empty">No actions recorded yet. Reviews, location changes and record changes appear here with who made them.</p>`}
        </aside>
      </div>`;
    const bulkEl = $("#bulk-d");
    const draw = bulkBar(bulkEl, () => [...st.sel], (changed) => { st.sel.clear(); changed ? load() : drawTable(); });
    drawBulkRef.fn = draw;
    drawTable();
  }
  const drawBulkRef = { fn: () => {} };
  function drawBulk() { drawBulkRef.fn(); }

  const load = () => api("/api/admin/dashboard").then(async (d) => {
    data = d; st.triage = d.triage;
    if (st.risk) await loadRisk();
    render();
  }).catch(fail(el));
  const loadRisk = async () => {
    const r = st.risk === "failed" ? "failed,processing" : st.risk;
    st.rows = (await api(`/api/admin/inspections?risk=${r}&latest_only=true&per_page=500`)).items;
  };

  el.addEventListener("click", async (e) => {
    const rk = e.target.closest("[data-risk]");
    if (rk) { st.risk = st.risk === rk.dataset.risk ? null : rk.dataset.risk; st.page = 1; st.sel.clear(); if (st.risk) await loadRisk().catch(() => { st.rows = []; }); render(); return; }
    if (e.target.closest("#tq-back")) { st.risk = null; st.page = 1; st.sel.clear(); render(); return; }
    const qb = e.target.closest("[data-quick]");
    if (qb) { st.quick = qb.dataset.quick; st.page = 1; el.querySelectorAll("[data-quick]").forEach((b) => b.setAttribute("aria-pressed", String(b === qb))); drawTable(); return; }
    const sb = e.target.closest("[data-sort]");
    if (sb) { const k = sb.dataset.sort; st.sort = { key: k, desc: st.sort.key === k ? !st.sort.desc : k === "submitted" }; drawTable(); return; }
    const pb = e.target.closest("[data-page]");
    if (pb && !pb.disabled) { st.page = Number(pb.dataset.page); drawTable(); $("#tq-h").scrollIntoView({ block: "nearest" }); }
  });
  el.addEventListener("change", (e) => {
    const cb = e.target.closest("[data-sel]");
    if (cb) { cb.checked ? st.sel.add(cb.dataset.sel) : st.sel.delete(cb.dataset.sel); cb.closest("tr").toggleAttribute("aria-selected", cb.checked); syncAll(); drawBulk(); }
    if (e.target.closest("[data-all]")) {
      const on = e.target.checked;
      el.querySelectorAll("#tq [data-sel]").forEach((x) => { x.checked = on; on ? st.sel.add(x.dataset.sel) : st.sel.delete(x.dataset.sel); x.closest("tr").toggleAttribute("aria-selected", on); });
      drawBulk();
    }
  });
  const syncAll = () => { const a = $("#tq [data-all]"); if (a) { const boxes = [...el.querySelectorAll("#tq [data-sel]")]; a.checked = boxes.length && boxes.every((x) => x.checked); } };
  el.addEventListener("input", (e) => { if (e.target.id === "tq-q") { clearTimeout(t); t = setTimeout(() => { st.q = e.target.value; st.page = 1; drawTable(); }, 150); } });
  bindRowLinks(el);
  load();
  return () => clearTimeout(t);
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
const TABS = [["all", "All"], ["needs_review", "Needs review"], ["failed", "Failed"], ["low", "Low risk"]];
const POP_FILTERS = ["barangay", "completeness", "date_from", "date_to"];
function mountInspections() {
  const params = new URLSearchParams(location.search);
  const st = {
    tab: TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "all",
    q: params.get("q") || "", risk: params.get("risk") || "", review: params.get("review") || "",
    f: Object.fromEntries(POP_FILTERS.map((k) => [k, params.get(k) || ""])),
    latest_only: !!params.get("latest_only"), include_test: !!params.get("include_test"),
    sort: { key: params.get("sort") || "triage", desc: params.get("desc") === "1" }, page: Number(params.get("page")) || 1,
    per: Number(params.get("per_page")) || 25, sel: new Set(), facets: null,
  };
  view.innerHTML = `<section class="page page-wide">
    <header class="pg-head pg-head-line"><div class="pg-title-line"><h1 class="pg-title">Inspections</h1><span class="pg-count num" id="count"></span></div>
      <div class="pg-actions btn-group">
        <a class="btn" id="csv" href="/api/admin/exports/inspections.csv">Download CSV</a>
        ${isAdmin() ? `<div class="more"><button class="btn" type="button" id="tx-btn" aria-expanded="false" aria-controls="tx-pop">Technical exports</button>
          <div class="adm-pop tx-pop" id="tx-pop" hidden>
            <a href="/api/admin/exports/wires.csv"><b>Wire measurements (CSV)</b><span>Every wire of every inspection: fitted curve, residuals, sag, reference, score.</span></a>
            <a href="/api/admin/exports/manifest.csv"><b>Site manifest (CSV)</b><span>One row per pole: rank, priority score, level, clutter, review, location.</span></a>
            <a href="/api/admin/exports/sites.pdf?technical=true" target="_blank" rel="noopener"><b>Technical priority report (PDF)</b><span>The ranking with priority scores, weights and their sensitivity.</span></a>
          </div></div>` : ""}
        <button class="btn" type="button" id="batch-btn" aria-expanded="false" aria-controls="batch">Upload photos</button>
        <a class="btn primary" href="/workspace" target="_blank" rel="noopener">New inspection</a></div></header>
    <section class="batch" id="batch" hidden aria-label="Upload several photos"></section>
    <div class="toolbar" role="search">
      <div class="tabs-q" role="tablist" aria-label="Status" id="tabs"></div>
      <label class="tb-search"><span class="sr-only">Search pole ID or location</span><input type="search" id="q" placeholder="Pole ID or location" value="${esc(st.q)}"></label>
      <div class="more"><button class="btn" type="button" id="flt-btn" aria-expanded="false" aria-controls="flt-pop">Filters<span class="flt-n num" id="flt-n" hidden></span></button>
        <form class="adm-pop flt-pop" id="flt-pop" hidden>
          <label class="flt"><span>Barangay</span><select name="barangay"><option value="">Any</option></select></label>
          <label class="flt"><span>Assessment</span><select name="completeness"><option value="">Any</option>${Object.entries(COMPLETE).map(([k, v]) => `<option value="${k}" ${st.f.completeness === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
          <div class="flt-dates"><label class="flt"><span>From</span><input type="date" name="date_from" value="${esc(st.f.date_from)}"></label>
            <label class="flt"><span>To</span><input type="date" name="date_to" value="${esc(st.f.date_to)}"></label></div>
          <label class="chk"><input type="checkbox" name="latest_only" ${st.latest_only ? "checked" : ""}> Latest per pole</label>
          ${isAdmin() ? `<label class="chk"><input type="checkbox" name="include_test" ${st.include_test ? "checked" : ""}> Include test records</label>` : ""}
        </form></div>
      <button class="link-btn" type="button" id="clear" hidden>Clear all</button>
    </div>
    <div class="active-flt" id="active-flt" hidden></div>
    <div class="bulk" id="bulk" hidden></div>
    <div id="list">${loading}</div></section>`;
  const fp = $("#flt-pop"), fbtn = $("#flt-btn");
  let t = 0, reqN = 0;
  const activeCount = () => POP_FILTERS.filter((k) => st.f[k]).length + st.latest_only + st.include_test + !!st.risk + !!st.review + !!st.q;
  const qs = (forApi) => {
    const q = new URLSearchParams();
    if (st.tab !== "all") q.set("tab", st.tab);
    if (st.q) q.set("q", st.q);
    if (st.risk) q.set("risk", st.risk);
    if (st.review) q.set("review", st.review);
    for (const k of POP_FILTERS) if (st.f[k]) q.set(k, st.f[k]);
    if (st.latest_only) q.set("latest_only", forApi ? "true" : "1");
    if (st.include_test) q.set("include_test", forApi ? "true" : "1");
    if (st.sort.key !== "triage") { q.set("sort", st.sort.key); if (st.sort.desc) q.set("desc", forApi ? "true" : "1"); }
    if (st.page > 1) q.set("page", st.page);
    if (st.per !== 25) q.set("per_page", st.per);
    return q;
  };
  const drawBulk = bulkBar($("#bulk"), () => [...st.sel], (changed) => { st.sel.clear(); changed ? load() : draw(lastData); });
  let lastData = null;

  function draw(d) {
    lastData = d;
    $("#tabs").innerHTML = TABS.map(([k, tx]) => `<button type="button" role="tab" data-tab="${k}" aria-selected="${st.tab === k}">${tx}<b class="num">${d.tabs[k]}</b></button>`).join("");
    $("#count").textContent = d.matched === d.total ? `${d.total}` : `${d.matched} of ${d.total}`;
    const n = activeCount(); $("#flt-n").hidden = !(n - !!st.q); $("#flt-n").textContent = n - !!st.q;
    $("#clear").hidden = !n;
    const chips = [st.risk && `Risk: ${st.risk.split(",").map((x) => RISK[x] || x).join(", ")}`, st.review && `Review: ${st.review.split(",").map((x) => REVIEW[x] || x).join(", ")}`].filter(Boolean);
    $("#active-flt").hidden = !chips.length;
    $("#active-flt").innerHTML = chips.map((c, i) => `<span class="fchip">${esc(c)}<button type="button" data-unset="${i === 0 && st.risk ? "risk" : "review"}" aria-label="Remove filter">×</button></span>`).join("");
    const all = d.items.length && d.items.every((r) => st.sel.has(r.id));
    $("#list").innerHTML = d.items.length ? `<div class="tbl-scroll"><table class="tbl tbl-dense tbl-insp">
      <thead><tr><th class="cb"><input type="checkbox" data-all ${all ? "checked" : ""} aria-label="Select all on this page"></th>
        ${sortHead("pole", "Pole ID", st.sort)}${sortHead("location", "Location", st.sort)}${sortHead("risk", "Risk", st.sort)}${sortHead("inspected", "Inspected", st.sort)}
        <th>Assessment</th>${sortHead("review", "Review", st.sort)}<th class="act">Action</th></tr></thead>
      <tbody>${d.items.map((r) => `<tr data-href="/admin/inspections/${r.id}" tabindex="0" ${st.sel.has(r.id) ? 'aria-selected="true"' : ""}>
        ${checkboxCell(r.id, st.sel.has(r.id))}
        <td data-label="Pole ID">${poleName(r)}${r.record_kind === "test" ? '<span class="tag">Test</span>' : ""}${r.same_day > 1 ? `<span class="tag tag-dup" title="${r.same_day} inspections of this pole on the same day">Duplicate</span>` : ""}</td>
        <td data-label="Location" class="loc">${place(r) || r.location ? placeHtml(r) : `<span class="muted">–</span><button type="button" class="assign" data-assign="${esc(r.pole_code)}">Assign</button>`}</td>
        <td data-label="Risk">${riskBadge(r.risk)}</td>
        <td data-label="Inspected" class="mono when"><time datetime="${esc(r.created_at)}" title="${esc(dayTime(r.created_at))}">${esc(shortDay(r.created_at))}<span class="when-t">${esc(shortTime(r.created_at))}</span></time></td>
        <td data-label="Assessment">${assessCell(r)}</td>
        <td data-label="Review">${r.review === "not_reviewed" ? '<span class="muted">–</span>' : reviewText(r.review, r)}</td>
        <td class="act"><a href="/admin/inspections/${r.id}" data-link class="go">Open</a></td></tr>`).join("")}</tbody></table></div>
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
    st.latest_only = fp.latest_only.checked; st.include_test = !!fp.include_test?.checked;
    reload();
  });
  fp.addEventListener("submit", (e) => e.preventDefault());
  $("#clear").addEventListener("click", () => {
    st.q = ""; st.risk = ""; st.review = ""; st.latest_only = false; st.include_test = false;
    for (const k of POP_FILTERS) st.f[k] = "";
    fp.reset(); $("#q").value = ""; reload();
  });
  const root = view.querySelector(".page");
  root.addEventListener("click", async (e) => {
    const tb = e.target.closest("[data-tab]");
    if (tb) { st.tab = tb.dataset.tab; st.risk = ""; st.review = ""; return reload(); }
    const un = e.target.closest("[data-unset]");
    if (un) { st[un.dataset.unset] = ""; return reload(); }
    const sb = e.target.closest("[data-sort]");
    if (sb) { const k = sb.dataset.sort; st.sort = st.sort.key === k ? { key: k, desc: !st.sort.desc } : { key: k, desc: k === "inspected" }; st.page = 1; return load(); }
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
  return () => { clearTimeout(t); document.removeEventListener("click", closePop); document.removeEventListener("click", closeTx); document.removeEventListener("keydown", esck); };
}
const assessCell = (r) => {
  if (r.risk === "failed") return '<span class="badge badge-bad">Failed</span>';
  if (r.risk === "processing") return '<span class="muted">Processing</span>';
  if (!r.wires_total) return '<span class="muted">No wires</span>';
  if (r.completeness === "complete") return '<span class="badge badge-ok">Complete</span>';
  return `<span class="prog" title="${r.wires_assessed} of ${r.wires_total} wire sections assessed"><i style="--p:${(r.wires_assessed / r.wires_total) * 100}%"></i><span class="num">${r.wires_assessed}/${r.wires_total}</span></span>`;
};

/** Several photos at once (moved here from the retired Exports page). Each becomes its own inspection. */
function mountBatch(el, btn, onDone) {
  el.innerHTML = `<header class="block-head"><h2>Upload photos</h2><span class="muted">Each photo becomes its own inspection</span></header>
    <label class="drop"><input type="file" id="b-files" multiple accept="image/jpeg,image/png,image/webp,image/tiff,.txt"><span>Choose photos</span></label>
    <div class="batch-opts">
      <label class="field"><span>Barangay <i>optional, for every photo</i></span><input id="b-brgy" maxlength="120"></label>
      <label class="chk"><input type="checkbox" id="b-stem" checked> Use each file name as the pole ID</label></div>
    <ol class="batch-list" id="b-list"></ol>
    <wf-notice variant="upload"></wf-notice>
    <button class="btn primary" id="b-run" disabled>Screen 0 photos</button>`;
  btn.addEventListener("click", () => { el.hidden = !el.hidden; btn.setAttribute("aria-expanded", String(!el.hidden)); });
  let pairs = [];
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
    pairs = files.filter((x) => x.type.startsWith("image/")).map((x) => ({ img: x, labels: labels.get(x.name.replace(/\.[^.]+$/, "")) || null, state: "ready" }));
    render();
  });
  $("#b-run", el).addEventListener("click", async () => {
    const stem = $("#b-stem", el).checked, brgy = $("#b-brgy", el).value.trim();
    for (const p of pairs.filter((x) => x.state === "ready")) {
      p.state = "uploading"; render();
      const fd = new FormData(); fd.append("file", p.img);
      if (p.labels) fd.append("labels", p.labels);
      if (brgy) fd.append("barangay", brgy);
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
const PRESETS = {
  photo: {},
  wires: { masks: 1, inferred: 1, catenary: 1, sag: 1, ends: 0, labels: 1, poles: 0 },
  mask: { wiremask: 1 },
  both: { wiremask: 1, masks: 1, inferred: 1, catenary: 1, sag: 1, labels: 1 },
};
const LAYERS = [["masks", "Wire sections"], ["inferred", "Estimated gaps (dashed)"], ["wiremask", "Model mask"], ["catenary", "Fitted curve"],
  ["sag", "Sag of the selected wire"], ["skeleton", "Centre lines"], ["ends", "Section ends"], ["labels", "Wire numbers"], ["poles", "Utility poles (context)"]];

function moreMenu(c) {
  return c.can.reclassify || c.can.delete ? `<div class="more"><button class="btn icon" type="button" id="more-btn" aria-expanded="false" aria-controls="more-menu" aria-label="More actions">
      <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg></button>
    <div class="adm-pop" id="more-menu" hidden>
      ${c.can.reclassify ? `<button type="button" id="kind">${c.record_kind === "test" ? "Mark as operational record" : "Mark as test record"}</button>` : ""}
      ${c.can.delete ? `<button type="button" id="delete" class="danger">Delete inspection</button>` : ""}</div></div>` : "";
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
          ${moreMenu(c)}
        </div>
      </header>
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
      ${historyHtml(r, c)}
      ${isAdmin() && r.error ? `<details class="diag"><summary><b>View system trace</b><span>Administrators: the error as the server recorded it (file paths removed)</span></summary>
        <div class="diag-body"><dl class="kv"><div><dt>Error code</dt><dd class="mono">${esc(r.error.code || "–")}</dd></div></dl>
        <pre class="trace">${esc(r.error.message || "No detail recorded.")}</pre></div></details>` : ""}`;
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
    const moreBtn = $("#more-btn", el), more = $("#more-menu", el);
    moreBtn?.addEventListener("click", () => { const o = more.hidden; more.hidden = !o; moreBtn.setAttribute("aria-expanded", String(o)); });
    document.addEventListener("click", (e) => { if (more && !more.hidden && !e.target.closest(".more")) { more.hidden = true; moreBtn.setAttribute("aria-expanded", "false"); } });
    $("#kind", el)?.addEventListener("click", async () => {
      const to = c.record_kind === "test" ? "operational" : "test";
      if (to === "test" && !confirm("Mark this inspection as a test record? It leaves the dashboard, the lists, the map and the exports. Nothing is deleted.")) return;
      try { await api(`/api/admin/inspections/${r.id}/record-kind`, json("PUT", { record_kind: to })); toast.success(to === "test" ? "Marked as a test record" : "Marked as an operational record"); load(); }
      catch (ex) { toast.error("Not changed", { description: ex.message }); }
    });
    $("#delete", el)?.addEventListener("click", async () => {
      if (!confirm(`Delete this inspection of pole ${row.pole_code}? Its photo, findings, reviews and report are removed permanently. To keep it out of the operational views instead, mark it as a test record.`)) return;
      try { await api(`/api/analysis/${r.id}`, { method: "DELETE" }); toast.success("Inspection deleted"); navigate("/admin/inspections"); }
      catch (ex) { toast.error("Not deleted", { description: ex.message }); }
    });
  };

  const render = (r, c) => {
    const row = c.row;
    Object.assign(ctx, { analysisId: r.id, label: `${row.label || row.pole_code}` });
    assistant?.context();
    if (r.status === "error") return renderFailed(r, c, row);
    const a = assessment(r), lims = limitations(r), recs = recommendations(r, a, lims);
    const counts = Object.fromEntries(["high", "medium", "low", "none"].map((g) => [g, r.wires.filter((w) => group(w) === g).length]));
    const rank = { high: 0, medium: 1, low: 2, none: 3 };
    const wires = [...r.wires].sort((x, y) => rank[group(x)] - rank[group(y)] || (y.crs ?? -1) - (x.crs ?? -1)
      || (isMeasured(y) - isMeasured(x)) || (y.sag_ratio ?? -1) - (x.sag_ratio ?? -1) || x.index - y.index);
    const dg = r.diagnosis, dgOn = dg && !dg.counts.assessed && dg.counts.measured + dg.counts.measured_partial > 0;
    // S6: with no assessed span, a wire's condition is its measurement state (same as the result page), never a severity
    const MST = { measured: "Measured", partial: "Visible part", unmeasured: "Not measured", drop: "Drop wire" };
    const mst = (w) => (!dgOn ? group(w) : w.status === "service_drop" ? "drop" : !isMeasured(w) ? "unmeasured"
      : w.reason_code === "endpoint_unavailable" ? "partial" : "measured");
    const mcounts = Object.fromEntries(Object.keys(MST).map((k) => [k, r.wires.filter((w) => mst(w) === k).length]));
    const cond = (w) => (dgOn ? `<span class="risk risk-unassessed m-${mst(w)}"><i></i>${MST[mst(w)]}</span>`
      : `<span class="risk risk-${group(w) === "none" ? "unassessed" : group(w)}"><i></i>${CONDITION[group(w)]}</span>`);
    const chips = dgOn ? [["all", "All"], ...Object.entries(MST)] : [["all", "All"], ["high", CONDITION.high], ["medium", CONDITION.medium], ["low", CONDITION.low], ["none", CONDITION.none]];
    const ccount = dgOn ? mcounts : counts;
    const coverage = dgOn ? `${dg.counts.measured + dg.counts.measured_partial} of ${r.wires.length} wire sections were measured in the image${dg.counts.measured_partial ? ` (${dg.counts.measured_partial} visible part only)` : ""}; ${dg.counts.assessed} assessed for severity (no validated sag reference).` : a.coverage;
    const hasWires = r.wires.length > 0, hasMask = !!r.wire_mask, hasVeg = (r.vegetation || []).length > 0;
    const loc = r.pole.location;
    const lastRev = r.reviews.at(-1);
    const done = c.can.review;
    const views = [["photo", "Original"], ...(hasWires ? [["wires", "Detected wires"]] : []), ...(hasMask ? [["mask", "Mask"]] : []), ...(hasWires && hasMask ? [["both", "Both"]] : [])];
    const layers = LAYERS.filter(([k]) => (k === "wiremask" ? hasMask : k === "poles" ? (r.scene?.poles_detected || []).length : hasWires));
    el.innerHTML = `${headHtml(r, c, row, done)}
      ${r.guardrail?.requires_retake ? `<div class="banner banner-warn"><p><b>Assessment may be inaccurate.</b> Some wires could not be identified reliably from this photo${r.guardrail.choice?.action === "proceed" ? ", and the person who took it chose to proceed" : ""}. Review the findings carefully.</p></div>` : ""}

      <section class="status-band" aria-label="Inspection status">
        <div><span class="k">Risk level</span>${riskBadge(row.risk, true)}</div>
        <div><span class="k">Assessment</span><b>${hasWires ? esc(COMPLETE[row.completeness]) : "No wire detected"}</b></div>
        <div><span class="k">Review</span>${reviewText(row.review, row)}</div>
        <div><span class="k">Inspection order</span><b>${c.priority ? `${c.priority.position} of ${c.priority.of}` : '<span class="muted">Not ranked</span>'}</b></div>
      </section>
      <wf-notice variant="full" class="sheet-notice"></wf-notice>

      <div class="sheet-grid">
        <section class="viewer-card" aria-label="Visual inspection">
          <div class="v-bar" role="toolbar" aria-label="Photo view">
            ${views.length > 1 ? `<div class="seg" role="radiogroup" aria-label="Show">
              ${views.map(([k, t]) => `<button type="button" role="radio" data-view="${k}" aria-checked="${k === (hasWires ? "wires" : "photo")}">${t}</button>`).join("")}
            </div>` : `<span class="v-only">Original photo</span>`}
            <span class="v-spacer"></span>
            <div class="zoom"><button class="btn sm icon" type="button" data-zoom="out" aria-label="Zoom out">−</button>
              <output id="zoom-pct" class="num">100%</output>
              <button class="btn sm icon" type="button" data-zoom="in" aria-label="Zoom in">+</button>
              <button class="btn sm" type="button" data-zoom="fit">Fit</button></div>
            ${hasWires || hasVeg ? `<div class="more"><button class="btn sm" type="button" id="lay-btn" aria-expanded="false" aria-controls="lay-pop">Layers</button>
              <div class="adm-pop lay-pop" id="lay-pop" hidden>
                <label class="v-op"><span>Overlay opacity</span><input type="range" id="opacity" min="0" max="100" value="100"><output class="num">100%</output></label>
                ${hasVeg ? `<label class="chk"><input type="checkbox" id="veg" checked> Vegetation</label>` : ""}
                ${layers.length ? `<fieldset class="v-layers"><legend>Layers</legend>${layers.map(([k, t]) => `<label class="chk"><input type="checkbox" data-l="${k}"> ${t}</label>`).join("")}</fieldset>` : ""}
              </div></div>` : ""}
          </div>
          <div class="v-canvas" style="--ar:${r.image.width} / ${r.image.height}"><div class="canvas-wrap" id="canvas"></div></div>
          ${hasWires && dgOn ? `<ul class="legend"><li><i style="background:var(--cls-power)"></i>Power line</li><li><i style="background:var(--cls-telecom)"></i>Telecom / fibre</li><li><i style="background:var(--cls-dead)"></i>Abandoned</li><li><i style="background:var(--cls-unknown)"></i>Type uncertain</li><li><i class="l-dash"></i>Estimated</li><li class="legend-hint">Colour shows wire type, not severity · scroll to zoom · click a wire to highlight it</li></ul>`
            : hasWires ? `<ul class="legend"><li><i class="l-high"></i>${CONDITION.high}</li><li><i class="l-medium"></i>${CONDITION.medium}</li><li><i class="l-low"></i>${CONDITION.low}</li><li><i class="l-none"></i>${CONDITION.none}</li><li><i class="l-dash"></i>Estimated</li><li class="legend-hint">Scroll to zoom · click a wire to highlight it</li></ul>` : ""}
        </section>

        <section class="findings" aria-labelledby="fd-h">
          <h2 id="fd-h">Findings</h2>
          <p class="fd-title">${esc(a.title)}</p>
          ${a.lines.map((l) => `<p class="fd-line">${l}</p>`).join("")}
          ${hasWires && dgOn ? `<dl class="fd-counts">${[["Measured", dg.counts.measured], ["Visible part only", dg.counts.measured_partial], ["Not measured", dg.counts.not_measured], ["Assessed", dg.counts.assessed]].map(([t, n]) => `<div><dt><i class="l-none"></i>${t}</dt><dd class="num">${n}</dd></div>`).join("")}</dl>`
            : hasWires ? `<dl class="fd-counts">${["high", "medium", "low", "none"].map((g) => [g, CONDITION[g]]).map(([g, t]) => `<div><dt><i class="l-${g}"></i>${t}</dt><dd class="num">${counts[g]}</dd></div>`).join("")}</dl>` : ""}
          ${coverage ? `<p class="hint">${esc(coverage)}</p>` : ""}
          ${lims.length ? `<h3>Photo limitations</h3><ul class="fd-list">${lims.map((l) => `<li>${esc(l.text)}${l.ids.length ? ` <span class="muted">(${SECTION(l.ids)})</span>` : ""}${l.help ? `<span class="help">${esc(l.help)}</span>` : ""}</li>`).join("")}</ul>` : ""}
          ${dg && hasWires ? `${dg.observations.length ? `<h3>Observed in the image</h3><ul class="fd-list">${dg.observations.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>` : ""}
            ${dg.unavailable.length ? `<h3>Not available from one photo</h3><ul class="fd-list">${dg.unavailable.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>` : ""}
            ${dg.needed.length ? `<h3>What would complete the assessment</h3><ul class="fd-list">${dg.needed.map((x) => `<li>${esc(x)}.</li>`).join("")}</ul>` : ""}` : ""}
          <h3>Recommended next steps</h3>
          <ol class="fd-list">${recs.map((x) => `<li>${x}</li>`).join("")}</ol>
        </section>
      </div>

      ${hasWires ? `<section class="block" aria-labelledby="wf-h">
        <header class="block-head"><h2 id="wf-h">Wire findings</h2><span class="count num">${r.wires.length}</span>
          <div class="chips" role="group" aria-label="Show">${chips.filter(([k]) => k === "all" || ccount[k]).map(([k, t]) => `<button type="button" data-filter="${k}" aria-pressed="${k === "all"}">${t}${k !== "all" ? ` <b class="num">${ccount[k]}</b>` : ""}</button>`).join("")}</div></header>
        <div class="tbl-scroll"><table class="tbl tbl-wires"><thead><tr><th>Wire</th><th>Type</th><th>Condition</th><th title="Image sag ratio compared with the configured reference (provisional; not an assessment)">${r.comparison ? `vs ${(r.comparison.sag_ratio * 100).toFixed(1)}% (${esc(r.comparison.status)})` : "vs reference"}</th><th>Finding</th></tr></thead><tbody>
          ${wires.map((w) => `<tr data-wire="${w.index}" data-group="${mst(w)}" tabindex="0" aria-selected="false">
            <td><span class="wno" style="--wc:${wireColor(w, dgOn ? "class" : "risk")}">${w.index}</span></td><td>${esc(classLabel(w, true))}</td>
            <td>${cond(w)}</td>
            <td>${w.comparison?.position ? `<span class="cmp ${w.comparison.position}">${{ below: "Below", at: "At", above: "Above" }[w.comparison.position]} ${w.comparison.difference_pp > 0 ? "+" : ""}${w.comparison.difference_pp.toFixed(1)}</span>` : "—"}</td>
            <td>${esc(finding(w))}</td></tr>`).join("")}</tbody></table></div>
      </section>` : `<section class="block nowire"><p><b>No wire was detected in this photo.</b> The photo was processed, but the model found no wire it could trace. That does not mean there are no wires or no hazard: wires can blend into the sky or background. See the recommended next steps.</p></section>`}

      <div class="sheet-cols">
        <section class="block" aria-labelledby="rv-h">
          <header class="block-head"><h2 id="rv-h">Professional review</h2>${lastRev ? reviewText(lastRev.decision, lastRev) : ""}</header>
          ${r.reviews.length ? `<ol class="rev-log">${r.reviews.map((x) => `<li><p><b>${esc(x.by)}</b> ${esc({ confirmed: "confirmed the finding", flagged: "flagged it for further inspection", overridden: `changed the risk level to ${(RISK[x.override_level] || "").toLowerCase()}`, dismissed: "dismissed the finding" }[x.decision])}</p>
              <time datetime="${esc(x.at)}">${esc(dayTime(x.at))}</time>${x.note ? `<blockquote>${esc(x.note)}</blockquote>` : ""}</li>`).join("")}</ol>`
            : `<p class="empty">Not reviewed yet.</p>`}
          ${done ? `<form class="rev-form" id="rev">
            <fieldset><legend>Decision</legend>
              ${[["confirmed", "Confirm finding", "You agree with the automated result."], ["flagged", "Flag for further inspection", "The pole needs a field visit or a closer check."],
                 ["overridden", "Change risk level", "Record a different level. A note is required."], ["dismissed", "Dismiss finding", "The finding does not apply, for example a misdetection. A note is required."]]
                .map(([v, t, h], i) => `<label class="opt"><input type="radio" name="decision" value="${v}" ${i === 0 ? "checked" : ""}><span><b>${t}</b><small>${h}</small></span></label>`).join("")}
            </fieldset>
            <label class="field" id="ov-level" hidden><span>Reviewed risk level</span><select name="override_level">
              ${["high", "medium", "low"].map((l) => `<option value="${l}">${RISK[l]}</option>`).join("")}</select></label>
            <label class="field"><span>Note <i id="note-req">optional</i></span><textarea name="note" rows="3" maxlength="2000" placeholder="What you checked, and why"></textarea></label>
            <button class="btn primary" type="submit">Record review</button>
            <p class="hint">The automated findings stay as computed. Your decision is recorded beside them with your name and the time, and printed in the PDF report.</p>
          </form>` : ""}
        </section>
        <section class="block" aria-labelledby="ii-h">
          <header class="block-head"><h2 id="ii-h">Inspection details</h2></header>
          <dl class="kv">
            <div><dt>Pole</dt><dd>${row.label && row.label !== row.pole_code ? `${esc(row.label)} <span class="mono muted">${esc(row.pole_code)}</span>` : `<span class="mono">${esc(row.pole_code)}</span>`}</dd></div>
            ${row.inspection_name ? `<div><dt>Inspection name</dt><dd>${esc(row.inspection_name)}</dd></div>` : ""}
            <div><dt>Barangay</dt><dd>${row.barangay ? esc(row.barangay) : `<span class="muted">–</span> <button type="button" class="link-btn" id="set-brgy">Assign</button>`}</dd></div>
            ${row.city ? `<div><dt>City</dt><dd>${esc(row.city)}</dd></div>` : ""}
            <div><dt>Date and time</dt><dd>${esc(dayTime(row.created_at))}</dd></div>
            <div><dt>Location</dt><dd>${loc ? `<span class="mono">${loc.lat.toFixed(5)}, ${loc.lon.toFixed(5)}</span> <span class="muted">(${loc.source === "exif" ? "photo GPS" : loc.source === "manual" ? "pinned by an administrator" : "entered"})</span>
              <a class="link" href="/admin/map?pole=${encodeURIComponent(row.pole_code)}" data-link>Show on map</a>` : `<span class="muted">No GPS recorded</span>${isAdmin() ? ` <a class="link" href="/admin/map?pole=${encodeURIComponent(row.pole_code)}&tab=pinning" data-link>Drop a pin</a>` : ""}`}</dd></div>
            <div><dt>Source photo</dt><dd>${esc(r.image.original_filename || "–")} <span class="muted">${r.image.width} × ${r.image.height}</span></dd></div>
            <div><dt>Submitted from</dt><dd>${r.submitted_via === "field" ? "Field Screener" : "Admin upload"}</dd></div>
          </dl>
        </section>
      </div>
      ${historyHtml(r, c)}

      ${c.can.diagnostics ? `<details class="diag" id="diag"><summary><b>Technical diagnostics</b><span>Administrators: metrics, fit parameters and acquisition checks</span></summary>
        <div class="diag-body">${diagnosticsHtml(r)}</div></details>` : ""}`;

    // ---- viewer
    viewer?.destroy();
    viewer = new OverlayViewer($("#canvas"), { onSelect: (i) => mark(i, true) });
    $("#canvas").addEventListener("zoom", (e) => { $("#zoom-pct").textContent = `${Math.round(e.detail * 100)}%`; });
    const worst = r.wires.filter((w) => w.crs != null).sort((x, y) => y.crs - x.crs)[0];
    // S5: with no assessed span, open on the measured wire with the largest image-derived sag ratio (a measurement)
    const firstMeasured = worst ? null : r.wires.filter(isMeasured).sort((x, y) => y.sag_ratio - x.sag_ratio)[0];
    viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => {
      if (hasMask) viewer.setWireMask(r.wire_mask.url);
      viewer.setPoles(r.scene?.poles_detected || []);
      if (worst && worst.level !== "low") { viewer.selected = worst.index; viewer.draw(); mark(worst.index, false); }
      else if (firstMeasured) { viewer.setMode("class"); viewer.selected = firstMeasured.index; viewer.draw(); mark(firstMeasured.index, false); }
    }).catch(() => toast.error("The photo could not be displayed"));
    const table = el.querySelector(".tbl-wires");
    const mark = (i, scroll) => table?.querySelectorAll("tr[data-wire]").forEach((tr) => {
      const on = Number(tr.dataset.wire) === i;
      tr.setAttribute("aria-selected", String(on));
      if (on && scroll) tr.scrollIntoView({ block: "nearest", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    });
    const select = (i) => { const next = viewer.selected === i ? null : i; viewer.select(next); mark(next, false); };
    table?.addEventListener("click", (e) => { const tr = e.target.closest("tr[data-wire]"); if (tr) select(Number(tr.dataset.wire)); });
    table?.addEventListener("keydown", (e) => {
      const tr = e.target.closest("tr[data-wire]"); if (!tr) return;
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(Number(tr.dataset.wire)); }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const rows = [...table.querySelectorAll("tr[data-wire]:not([hidden])")];
        const n = rows[rows.indexOf(tr) + (e.key === "ArrowDown" ? 1 : -1)];
        if (n) { n.focus(); viewer.select(Number(n.dataset.wire)); mark(Number(n.dataset.wire), false); }
      }
    });
    el.addEventListener("click", (e) => { // "(section 3)" references in the findings select the wire
      const b = e.target.closest("[data-goto]"); if (!b) return;
      const i = Number(b.dataset.goto); viewer.select(i); mark(i, true);
      $(".viewer-card", el).scrollIntoView({ block: "nearest" });
    });
    const syncLayers = () => el.querySelectorAll("[data-l]").forEach((cb) => { cb.checked = !!viewer.layers[cb.dataset.l] && viewer.layers.overlays; });
    const setView = (name) => {
      el.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.view === name)));
      for (const [k] of LAYERS) viewer.layers[k] = !!PRESETS[name][k];
      viewer.layers.overlays = name !== "photo";
      viewer.draw(); syncLayers();
    };
    el.querySelector(".seg")?.addEventListener("click", (e) => { const b = e.target.closest("[data-view]"); if (b) setView(b.dataset.view); });
    el.querySelector(".v-layers")?.addEventListener("change", (e) => {
      const cb = e.target.closest("[data-l]"); if (!cb) return;
      viewer.layers.overlays = true; viewer.layers[cb.dataset.l] = cb.checked; viewer.draw();
      el.querySelectorAll("[data-view]").forEach((b) => b.setAttribute("aria-checked", "false"));
    });
    const layBtn = $("#lay-btn", el), layPop = $("#lay-pop", el);
    layBtn?.addEventListener("click", () => { const o = layPop.hidden; layPop.hidden = !o; layBtn.setAttribute("aria-expanded", String(o)); });
    document.addEventListener("click", (e) => { if (layPop && !layPop.hidden && !e.target.closest(".v-bar .more")) { layPop.hidden = true; layBtn.setAttribute("aria-expanded", "false"); } });
    const op = $("#opacity", el);
    op?.addEventListener("input", () => { viewer.setOpacity(op.value / 100); op.nextElementSibling.textContent = `${op.value}%`; });
    $("#veg", el)?.addEventListener("change", (e) => viewer.set("vegetation", e.target.checked));
    el.querySelectorAll("[data-zoom]").forEach((b) => b.addEventListener("click", () => ({ in: () => viewer.zoomIn(), out: () => viewer.zoomOut(), fit: () => viewer.reset() })[b.dataset.zoom]()));
    viewer.layers.vegetation = hasVeg;
    setView(hasWires ? "wires" : "photo");

    // ---- wire filter
    el.querySelector(".chips")?.addEventListener("click", (e) => {
      const b = e.target.closest("[data-filter]"); if (!b) return;
      el.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      table?.querySelectorAll("tr[data-wire]").forEach((tr) => { tr.hidden = b.dataset.filter !== "all" && tr.dataset.group !== b.dataset.filter; });
    });

    // ---- review
    const rev = $("#rev", el);
    if (rev) {
      rev.addEventListener("change", () => {
        const d = rev.decision.value;
        $("#ov-level", el).hidden = d !== "overridden";
        $("#note-req", el).textContent = d === "overridden" || d === "dismissed" ? "required" : "optional";
      });
      rev.addEventListener("submit", async (e) => {
        e.preventDefault();
        const d = rev.decision.value;
        if ((d === "overridden" || d === "dismissed") && !rev.note.value.trim()) {
          toast.error("Add a note", { description: "Explain a level change or a dismissal in the note." }); rev.note.focus(); return;
        }
        const btn = rev.querySelector("button[type=submit]"); btn.disabled = true;
        try {
          await api(`/api/admin/analyses/${r.id}/reviews`, json("POST", { decision: d, note: rev.note.value, override_level: d === "overridden" ? rev.override_level.value : null }));
          toast.success("Review recorded"); load();
        } catch (ex) { toast.error("Review not recorded", { description: ex.message }); btn.disabled = false; }
      });
    }
    $("#set-brgy", el)?.addEventListener("click", async () => {
      const v = await pickBarangay(`${row.label || row.pole_code} · ${row.pole_code}`);
      if (!v) return;
      try { await api(`/api/admin/poles/${encodeURIComponent(row.pole_code)}/barangay`, json("PUT", { barangay: v })); toast.success("Barangay assigned"); load(); }
      catch (ex) { toast.error("Not assigned", { description: ex.message }); }
    });
    bindCommon(r, c, row);
    if (c.can.diagnostics) bindDiagnostics(el, viewer);
  };
  load().catch(fail(el));
  return () => { alive = false; viewer?.destroy(); };
}

boot();
