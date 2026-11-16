// Side B: admin command centre. Signed-in engineers and administrators only; every API it
// calls is guarded server-side, so this file decides presentation, never access.

import { api, json, esc, fmt, pct, ago, levelChip, LEVEL_LABEL, STATUS_LABEL, waitForAnalysis } from "./lib.js?v=2.16";
import { OverlayViewer, CLASS_LABEL, wireColor } from "./viewer.js?v=2.16";
import { mountSites } from "./sites.js?v=2.16";
import { mountEvaluation } from "./evaluation.js?v=2.16";
import { onStatus, startStatus } from "./status.js?v=2.16";
import { toast, update } from "./toast.js?v=2.16";

const view = document.getElementById("view");
const $ = (s, r = document) => r.querySelector(s);
let me = null;
let cleanup = () => {};

// Plain-language explanations shown on hover/focus next to technical labels.
const TIP = {
  vni: "Visual Noise Index: share of image power at high spatial frequencies (2D FFT, Eq. 3–4). Scene clutter, not wire condition.",
  vni_raw: "Before quantile rescaling: Σ power beyond r_L ÷ Σ non-zero power.",
  a: "Catenary parameter a = horizontal tension ÷ weight per length, in pixels. Larger a = tauter wire.",
  bc: "b: x of the lowest point; c: vertical offset (y-up frame, pixels).",
  rms: "Root-mean-square vertical distance between the centerline points and the fitted catenary.",
  rel: "RMS residual ÷ chord length. The fit is rejected above the residual limit τ.",
  chord: "L: straight distance between the two ends of the fitted curve.",
  sag: "δ: largest perpendicular distance from the fitted curve to the chord.",
  ratio: "δ/L, the sag ratio (Eq. 8). Unchanged by image scale.",
  lim: "Allowable sag ratio for this class (Eq. 9); development placeholder until calibrated.",
  crs: "Catenary Risk Score = min{10, 10·(δ/L ÷ δ_lim)·w_class} (Eq. 10). Low < 3 ≤ Medium ≤ 6 < High.",
  pruned: "Skeleton pixels removed as spur branches shorter than L_min = 30 px (Zhang-Suen + pruning).",
  pts: "Centerline points used in the Levenberg–Marquardt fit.",
  mspi: "Multi-Site Priority Index (Eq. 11): orders poles for inspection.",
};
const tip = (k, label) => `<span class="tipped" tabindex="0" data-tip="${esc(TIP[k])}">${label}</span>`;

// ------------------------------------------------------------------ session
async function boot() {
  try { me = (await api("/api/auth/me")).user; } catch { me = null; }  // null when signed out
  if (!me) return showLogin();
  $("#top").hidden = false;
  $("#who").textContent = me.display_name;
  if (location.pathname === "/admin/login") history.replaceState({}, "", "/admin");
  startStatus({ retryFailed: true });
  onStatus(({ mode, health }) => {
    const pill = $("#mode-pill");
    pill.dataset.mode = mode;
    pill.textContent = { field: "Model active", validation: "Reference masks only", offline: "Offline", checking: "Checking…" }[mode];
    pill.title = mode === "field" ? `Segmentation: ${health?.model?.weights}` : health?.model?.error || "";
  });
  route();
}

function showLogin() {
  $("#top").hidden = true;
  if (location.pathname !== "/admin/login") history.replaceState({}, "", "/admin/login");
  view.innerHTML = `<section class="login">
    <form class="login-card" id="login" novalidate>
      <img src="/assets/logo.svg" alt="" width="40" height="40">
      <h1>Admin portal</h1>
      <p class="hint">For city engineers and administrators. Field screening needs no account.</p>
      <label class="field"><span>Username</span><input name="username" autocomplete="username" required autofocus></label>
      <label class="field"><span>Password</span><input name="password" type="password" autocomplete="current-password" required></label>
      <p class="form-error" id="login-error" role="alert" hidden></p>
      <button class="btn primary lg" type="submit">Sign in</button>
      <a class="back" href="/">← Back to WIREFRAME</a>
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
  me = null; showLogin();
});
addEventListener("wf:unauthorized", () => {
  if (!me) return;
  me = null; toast("Session ended", { description: "Sign in again to continue." }); showLogin();
});

// ------------------------------------------------------------------ router
const ROUTES = [
  [/^\/admin\/?$/, "overview", () => mountOverview()],
  [/^\/admin\/analyses\/?$/, "analyses", () => mountAnalyses()],
  [/^\/admin\/analyses\/([0-9a-f]{32})$/, "analyses", (m) => mountAnalysis(m[1])],
  [/^\/admin\/sites\/?$/, "sites", () => mountSites(view, { api, esc, navigate })],
  [/^\/admin\/poles\/?$/, "poles", () => mountPoles()],
  [/^\/admin\/poles\/(.+)$/, "poles", (m) => mountPole(decodeURIComponent(m[1]))],
  [/^\/admin\/models\/?$/, "models", () => mountModels()],
  [/^\/admin\/exports\/?$/, "exports", () => mountExports()],
  [/^\/admin\/evaluation\/?$/, "evaluation", () => mountEvaluation(view, { api, esc })],
];
function route() {
  if (!me) return;
  cleanup(); cleanup = () => {};
  const p = location.pathname;
  for (const [re, nav, fn] of ROUTES) {
    const m = p.match(re);
    if (m) {
      document.querySelectorAll("[data-nav]").forEach((a) => a.toggleAttribute("aria-current", a.dataset.nav === nav));
      cleanup = fn(m) || (() => {});
      view.focus({ preventScroll: true });
      return;
    }
  }
  view.innerHTML = `<section class="page"><h1 class="page-title">Not found</h1><a class="btn" href="/admin" data-link>Back to overview</a></section>`;
}
export function navigate(href) { history.pushState({}, "", href); route(); }
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-link]");
  if (!a || e.metaKey || e.ctrlKey || e.shiftKey || a.target) return;
  e.preventDefault(); navigate(a.getAttribute("href"));
});
addEventListener("popstate", route);

const page = (title, sub, actions = "") => `<header class="page-head"><div><h1 class="page-title">${title}</h1>${sub ? `<p class="page-sub">${sub}</p>` : ""}</div>${actions}</header>`;

// ------------------------------------------------------------------ overview
function mountOverview() {
  view.innerHTML = `<section class="page">${page("Overview", "What needs attention now.")}<div id="ov"></div></section>`;
  api("/api/admin/overview").then((o) => {
    const st = o.analyses_by_status;
    const kpi = (k, v, cls = "") => `<div class="kpi ${cls}"><div class="k">${k}</div><div class="v">${v}</div></div>`;
    $("#ov").innerHTML = `
      <div class="kpis kpis-5">${kpi("Poles", o.poles)}${kpi("High-risk poles", o.poles_by_level.high, "level-high")}
        ${kpi("Medium", o.poles_by_level.medium, "level-medium")}${kpi("Low", o.poles_by_level.low, "level-low")}
        ${kpi("In queue", o.queue.depth + o.queue.processing.length)}</div>
      <div class="cols">
        <section class="panel"><header class="panel-head"><h2>Needs professional review</h2><span class="muted">High risk, not yet reviewed</span></header>
          ${o.needs_review.length ? `<table class="tbl"><tbody>${o.needs_review.map((r) => `<tr data-href="/admin/analyses/${r.id}">
            <td class="strong">${esc(r.pole_code)}</td><td class="num">CRS ${fmt(r.max_crs, 1)}</td><td class="muted">${ago(r.created_at)}</td>
            <td class="right"><span class="chip high">high</span></td></tr>`).join("")}</tbody></table>`
            : `<p class="empty-line">Nothing waiting. Every high-risk pole has a recorded review.</p>`}</section>
        <section class="panel"><header class="panel-head"><h2>Failed analyses</h2><a class="link" href="/admin/models" data-link>Queue →</a></header>
          ${o.queue.errors.length ? `<table class="tbl"><tbody>${o.queue.errors.slice(0, 6).map((e) => `<tr data-href="/admin/analyses/${e.id}">
            <td class="strong">${esc(e.pole_code)}</td><td class="muted">${esc((e.code || "").replace(/_/g, " "))}</td><td class="muted right">${ago(e.at)}</td></tr>`).join("")}</tbody></table>`
            : `<p class="empty-line">No failures.</p>`}
          <p class="hint">${["complete", "no_wires_detected", "fit_failed"].map((k) => `${st[k] || 0} ${STATUS_LABEL[k].toLowerCase()}`).join(" · ")}</p></section>
      </div>`;
    bindRowLinks($("#ov"));
  }).catch(fail("#ov"));
}

const fail = (sel) => (e) => { const el = $(sel); if (el) el.innerHTML = `<p class="empty-line">Could not load: ${esc(e.message)}</p>`; };
function bindRowLinks(root) {
  root.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]");
    if (tr && !e.target.closest("a,button")) navigate(tr.dataset.href);
  });
}

// ------------------------------------------------------------------ analyses
function mountAnalyses() {
  const params = new URLSearchParams(location.search);
  view.innerHTML = `<section class="page">${page("Analyses", "Every screening, newest first.",
    `<a class="btn" href="/admin/exports" data-link>Batch upload</a>`)}
    <form class="filters" id="filters">
      <input type="search" name="q" placeholder="Pole code, barangay or file" value="${esc(params.get("q") || "")}" aria-label="Search">
      <select name="level" aria-label="Risk level"><option value="">Any level</option>${["high", "medium", "low"].map((l) => `<option ${params.get("level") === l ? "selected" : ""} value="${l}">${LEVEL_LABEL[l]}</option>`).join("")}</select>
      <select name="status" aria-label="Status"><option value="">Any status</option>${Object.entries(STATUS_LABEL).map(([k, v]) => `<option ${params.get("status") === k ? "selected" : ""} value="${k}">${v}</option>`).join("")}</select>
      <label class="check"><input type="checkbox" name="unreviewed" ${params.get("unreviewed") ? "checked" : ""}> Unreviewed only</label>
    </form>
    <div id="list"></div></section>`;
  const f = $("#filters");
  const load = () => {
    const q = new URLSearchParams();
    for (const k of ["q", "level", "status"]) if (f[k].value) q.set(k, f[k].value);
    if (f.unreviewed.checked) q.set("unreviewed", "true");
    history.replaceState({}, "", `/admin/analyses${q.toString() ? "?" + q : ""}`);
    q.set("limit", "200");
    api(`/api/analysis?${q}`).then((d) => {
      $("#list").innerHTML = d.items.length ? `<table class="tbl tbl-lg">
        <thead><tr><th>Pole</th><th>Barangay</th><th>Analysed</th><th>Source</th><th class="num">${tip("crs", "Max CRS")}</th><th class="num">${tip("ratio", "Max δ/L")}</th><th>Level</th><th>Review</th></tr></thead>
        <tbody>${d.items.map((a) => `<tr data-href="/admin/analyses/${a.id}">
          <td class="strong"><img class="thumb-sm" src="${a.thumbnail_url}" alt="" loading="lazy">${esc(a.pole_code)}</td>
          <td>${esc(a.barangay || "—")}</td><td class="muted">${ago(a.created_at)}</td>
          <td class="muted">${a.segmentation_source === "model" ? "model" : a.segmentation_source === "annotation" ? "reference" : "—"} · ${a.submitted_via}</td>
          <td class="num">${fmt(a.max_crs, 1)}</td><td class="num">${pct(a.max_sag_ratio)}</td>
          <td>${levelChip(a.level, a.status)}</td>
          <td>${a.review ? `<span class="rev ${a.review.decision}">${a.review.decision}${a.review.override_level ? ` → ${a.review.override_level}` : ""}</span>` : `<span class="muted">—</span>`}</td></tr>`).join("")}</tbody></table>
        <p class="hint">${d.matched} of ${d.total} analyses</p>`
        : `<p class="empty-line">No analyses match.</p>`;
    }).catch(fail("#list"));
  };
  let t = 0;
  f.addEventListener("input", () => { clearTimeout(t); t = setTimeout(load, 180); });
  f.addEventListener("submit", (e) => e.preventDefault());
  bindRowLinks($("#list"));
  load();
  return () => clearTimeout(t);
}

// ------------------------------------------------------------------ analysis detail
function mountAnalysis(id) {
  let viewer = null, alive = true;
  view.innerHTML = `<section class="page page-wide"><div id="detail"><p class="empty-line">Loading…</p></div></section>`;
  const load = async () => {
    const s = await api(`/api/analysis/${id}`);
    if (s.status === "processing") {
      $("#detail").innerHTML = `<p class="empty-line">Analysis in progress…</p>`;
      await waitForAnalysis(id);
      if (!alive) return;
    }
    render(await api(`/api/analysis/${id}/result`));
  };
  const render = (r) => {
    const sm = r.summary;
    const worst = r.wires.filter((w) => w.crs != null).sort((a, b) => b.crs - a.crs)[0];
    const lastRev = r.reviews.at(-1);
    $("#detail").innerHTML = `
      <header class="page-head">
        <div><p class="crumbs"><a href="/admin/analyses" data-link>Analyses</a> / ${esc(r.pole.code)}</p>
          <h1 class="page-title">${esc(r.pole.code)} ${levelChip(sm.level, r.status)}</h1>
          <p class="page-sub">${esc(r.pole.barangay || "No barangay")} · ${new Date(r.created_at).toLocaleString()} ·
            ${r.segmentation_source === "model" ? "model masks" : r.segmentation_source === "annotation" ? "reference masks" : "no masks"} · via ${r.submitted_via}
            · <a href="/admin/poles/${encodeURIComponent(r.pole.code)}" data-link>pole history</a></p></div>
        <div class="row-actions">
          ${r.status !== "error" ? `<a class="btn" href="/api/reports/${r.id}/pdf" target="_blank" rel="noopener">Report PDF</a>` : ""}
          ${r.status === "error" ? `<button class="btn primary" id="retry">Retry</button>` : ""}
          <button class="btn danger" id="delete">Delete</button>
        </div>
      </header>
      ${r.status === "error" ? `<div class="notice-err"><b>${esc((r.error?.code || "error").replace(/_/g, " "))}</b> ${esc(r.error?.message || "")}</div>` : ""}
      <div class="detail-grid">
        <div class="stage-card">
          <div class="stage-bar">
            ${["masks", "catenary", "sag", "skeleton"].map((l) => `<button class="toggle" data-layer="${l}" aria-pressed="${l !== "skeleton"}">${{ masks: "Masks", catenary: "Catenary", sag: "Chord & δ", skeleton: "Skeleton" }[l]}</button>`).join("")}
            <span class="bar-spacer"></span>
            <div class="seg"><button data-mode="risk" aria-pressed="true">Risk</button><button data-mode="class" aria-pressed="false">Class</button></div>
          </div>
          <div class="canvas-wrap" id="canvas"></div>
        </div>
        <aside class="side-col">
          <div class="kpis kpis-3">
            <div class="kpi ${sm.level ? "level-" + sm.level : ""}"><div class="k">${tip("crs", "Max CRS")}</div><div class="v">${fmt(sm.max_crs, 1)}<small>/10</small></div></div>
            <div class="kpi"><div class="k">${tip("mspi", "MSPI")}</div><div class="v">${r.ranking?.mspi == null ? "—" : fmt(r.ranking.mspi, 3)}</div></div>
            <div class="kpi"><div class="k">Rank</div><div class="v">${r.ranking?.rank ? `${r.ranking.rank}<small>/${r.ranking.of}</small>` : "—"}</div></div>
          </div>
          <section class="panel">
            <header class="panel-head"><h2>Professional review</h2>${lastRev ? `<span class="rev ${lastRev.decision}">${lastRev.decision}${lastRev.override_level ? ` → ${lastRev.override_level}` : ""}</span>` : `<span class="muted">Not reviewed</span>`}</header>
            ${r.reviews.length ? `<ol class="rev-log">${r.reviews.map((x) => `<li><b>${esc(x.by)}</b> ${x.decision}${x.override_level ? ` → <span class="chip ${x.override_level}">${x.override_level}</span>` : ""}
              <span class="muted">${ago(x.at)}</span>${x.note ? `<p>${esc(x.note)}</p>` : ""}</li>`).join("")}</ol>` : ""}
            ${["complete", "fit_failed", "no_wires_detected"].includes(r.status) ? `<form class="rev-form" id="rev">
              <div class="seg" role="radiogroup" aria-label="Decision">
                ${["confirmed", "overridden", "dismissed"].map((d, i) => `<label><input type="radio" name="decision" value="${d}" ${i === 0 ? "checked" : ""}><span>${{ confirmed: "Confirm", overridden: "Override", dismissed: "Dismiss" }[d]}</span></label>`).join("")}
              </div>
              <label class="field" id="ov-level" hidden><span>Reviewed level</span><select name="override_level">
                ${["low", "medium", "high"].map((l) => `<option value="${l}">${LEVEL_LABEL[l]}</option>`).join("")}</select></label>
              <label class="field"><span>Note <i id="note-req">optional</i></span><textarea name="note" rows="2" maxlength="2000" placeholder="What you checked, and why"></textarea></label>
              <button class="btn primary" type="submit">Record review</button>
              <p class="hint">Computed scores are never changed; the decision is recorded beside them and printed in the report.</p>
            </form>` : ""}
          </section>
        </aside>
      </div>

      <nav class="tabs" role="tablist">
        ${["Geometry", "Spectrum", "Acquisition"].map((t, i) => `<button role="tab" data-tab="${t.toLowerCase()}" aria-selected="${i === 0}">${t}</button>`).join("")}
      </nav>
      <section class="tab-panel" data-panel="geometry">${geometryTable(r)}</section>
      <section class="tab-panel" data-panel="spectrum" hidden>
        <div class="spec">
          <img src="/api/admin/analyses/${r.id}/spectrum.png" alt="Centred log power spectrum of the Hann-windowed grayscale image, with the low-frequency radius marked" loading="lazy">
          <dl class="spec-dl">
            <div><dt>${tip("vni", "VNI")}</dt><dd>${fmt(r.vni?.value, 3)} <span class="muted">${esc(r.vni?.band || "")}</span></dd></div>
            <div><dt>${tip("vni_raw", "VNI raw")}</dt><dd>${fmt(r.vni?.raw, 5)}</dd></div>
            <div><dt>Radius factor</dt><dd>${r.vni?.radius_factor ?? "—"} <span class="muted">r_L = factor · min(M,N)/2</span></dd></div>
            <div><dt>Parameters</dt><dd class="mono">${esc(r.parameters.fingerprint)}${r.parameters.fingerprint !== r.parameters.current_fingerprint ? ' <span class="chip medium">changed since</span>' : ""}</dd></div>
          </dl>
          <p class="hint">Brightness = log(1 + P(u,v)); the zero frequency is at the centre. Power outside the white circle counts toward the VNI.</p>
        </div></section>
      <section class="tab-panel" data-panel="acquisition" hidden>
        <table class="tbl"><thead><tr><th>Envelope check</th><th>Value</th><th>Expected</th><th>Result</th></tr></thead><tbody>
          ${r.acquisition.envelope.map((c) => `<tr><td>${esc(c.name.replace(/_/g, " "))}</td><td class="num">${c.value ?? "—"}</td><td class="num">${c.expected[0]} – ${c.expected[1]}</td><td><span class="env-${c.status}">${c.status}</span></td></tr>`).join("")}
        </tbody></table>
        ${r.acquisition.metadata.segmentation_input ? (() => {
          const si = r.acquisition.metadata.segmentation_input, q = si.quality || {};
          return `<p class="hint">Segmentation input: ${si.enhancement.length ? `enhanced with ${si.enhancement.join(" → ")}` : "no enhancement needed"}
            · lightness ${fmt(q.mean_l, 0)} · contrast ${fmt(q.contrast, 1)} · noise σ ${fmt(q.noise_sigma, 1)} · sharpness ${fmt(q.sharpness, 0)}
            <span class="muted">(the VNI is always computed on the unenhanced photo)</span></p>`;
        })() : ""}
        <p class="hint">Flags: ${r.flags.map((f) => `<code>${esc(f)}</code>`).join(" ") || "none"}</p>
        <p class="hint">Engineering context: ${Object.keys(r.engineering_context).length ? Object.entries(r.engineering_context).map(([k, v]) => `${esc(k)} ${v}`).join(" · ") : "none supplied (reference sag limit used)"} ·
          location ${r.pole.location ? `${r.pole.location.lat.toFixed(5)}, ${r.pole.location.lon.toFixed(5)} (${r.pole.location.source})` : "not recorded"}</p>
      </section>`;

    viewer?.destroy();
    viewer = new OverlayViewer($("#canvas"), { onSelect: (i) => markRow(i) });
    viewer.load(r.image.url, r.wires, r.vegetation || []).then(() => { if (worst) { viewer.selected = worst.index; viewer.draw(); markRow(worst.index); } }).catch(() => {});
    const markRow = (i) => view.querySelectorAll("tr[data-wire]").forEach((tr) => tr.toggleAttribute("aria-selected", Number(tr.dataset.wire) === i));
    view.querySelectorAll("[data-layer]").forEach((b) => b.addEventListener("click", () => {
      const on = b.getAttribute("aria-pressed") !== "true"; b.setAttribute("aria-pressed", String(on)); viewer.set(b.dataset.layer, on);
    }));
    view.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => {
      view.querySelectorAll("[data-mode]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); viewer.setMode(b.dataset.mode);
    }));
    view.querySelectorAll("tr[data-wire]").forEach((tr) => tr.addEventListener("click", () => {
      const i = Number(tr.dataset.wire); const next = viewer.selected === i ? null : i; viewer.select(next); markRow(next);
    }));
    view.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => {
      view.querySelectorAll("[data-tab]").forEach((x) => x.setAttribute("aria-selected", String(x === b)));
      view.querySelectorAll("[data-panel]").forEach((p) => (p.hidden = p.dataset.panel !== b.dataset.tab));
    }));
    const rev = $("#rev");
    if (rev) {
      rev.addEventListener("change", () => {
        const d = rev.decision.value;
        $("#ov-level").hidden = d !== "overridden";
        $("#note-req").textContent = d === "confirmed" ? "optional" : "required";
      });
      rev.addEventListener("submit", async (e) => {
        e.preventDefault();
        const d = rev.decision.value;
        const body = { decision: d, note: rev.note.value, override_level: d === "overridden" ? rev.override_level.value : null };
        try { await api(`/api/admin/analyses/${r.id}/reviews`, json("POST", body)); toast.success("Review recorded"); load(); }
        catch (ex) { toast.error("Review not recorded", { description: ex.message.includes("note") || ex.status === 422 ? "An override or dismissal needs a note." : ex.message }); }
      });
    }
    $("#retry")?.addEventListener("click", async () => {
      try { await api(`/api/analysis/${r.id}/retry`, { method: "POST" }); toast.success("Re-queued"); load(); }
      catch (ex) { toast.error("Retry not possible", { description: ex.message }); }
    });
    $("#delete").addEventListener("click", async () => {
      if (!confirm(`Delete this analysis of pole ${r.pole.code}? Its wires, reviews and report are removed with it.`)) return;
      try { await api(`/api/analysis/${r.id}`, { method: "DELETE" }); toast.success("Analysis deleted"); navigate("/admin/analyses"); }
      catch (ex) { toast.error("Delete failed", { description: ex.message }); }
    });
  };
  load().catch(fail("#detail"));
  return () => { alive = false; viewer?.destroy(); };
}

function geometryTable(r) {
  if (!r.wires.length) return `<p class="empty-line">No wires were detected.</p>`;
  const n = (v, d) => (v == null ? "—" : fmt(v, d));
  return `<div class="tbl-scroll"><table class="tbl tbl-num">
    <thead><tr><th>#</th><th>Class</th><th>Status</th><th>${tip("a", "a")}</th><th>${tip("bc", "b")}</th><th>${tip("bc", "c")}</th>
      <th>${tip("rms", "RMS px")}</th><th>${tip("rel", "RMS / L")}</th><th>${tip("chord", "L px")}</th><th>${tip("sag", "δ px")}</th>
      <th>${tip("ratio", "δ/L")}</th><th>${tip("lim", "δ_lim")}</th><th>${tip("crs", "CRS")}</th><th>${tip("pruned", "Pruned")}</th><th>${tip("pts", "Points")}</th></tr></thead>
    <tbody>${r.wires.map((w) => {
      const f = w.fit || {};
      return `<tr data-wire="${w.index}" style="--wc:${wireColor(w)}">
        <td><span class="wno">${w.index}</span></td><td class="txt">${CLASS_LABEL[w.class]}</td>
        <td class="txt">${w.status === "scored" ? levelChip(w.level) : `<span class="chip plain">${esc((w.fail_reason || w.status).replace(/_/g, " "))}</span>`}</td>
        <td>${n(f.a, 0)}</td><td>${n(f.b, 0)}</td><td>${n(f.c, 0)}</td><td>${n(f.rms_residual_px, 2)}</td><td>${f.rel_residual == null ? "—" : pct(f.rel_residual, 2)}</td>
        <td>${n(w.chord_px, 0)}</td><td>${n(w.sag_px, 1)}</td><td>${pct(w.sag_ratio, 2)}</td><td>${pct(w.delta_lim, 2)}</td>
        <td class="strong">${n(w.crs, 2)}</td><td>${f.pruned_px ?? "—"}</td><td>${f.n_points ?? "—"}</td></tr>`;
    }).join("")}</tbody></table></div>
    <p class="hint">Fit: Levenberg–Marquardt on y = a·cosh((x − b)/a) + c in the y-up image frame; chord and δ from the fitted curve. Click a row to focus the wire.</p>`;
}

// ------------------------------------------------------------------ poles & temporal tracking
function mountPoles() {
  view.innerHTML = `<section class="page">${page("Poles", "Latest inspection per pole, worst first. Open one to see how it has changed.")}
    <form class="filters" id="pf"><input type="search" name="q" placeholder="Pole code or barangay" aria-label="Search poles"></form><div id="poles"></div></section>`;
  const load = (q = "") => api(`/api/admin/poles${q ? `?q=${encodeURIComponent(q)}` : ""}`).then((rows) => {
    $("#poles").innerHTML = rows.length ? `<table class="tbl tbl-lg"><thead><tr><th>Pole</th><th>Barangay</th><th class="num">Inspections</th>
      <th class="num">${tip("crs", "Latest max CRS")}</th><th class="num">Change since first</th><th>Level</th><th>Location</th></tr></thead>
      <tbody>${rows.map((p) => {
        const t = p.trend_max_crs;
        return `<tr data-href="/admin/poles/${encodeURIComponent(p.pole_code)}"><td class="strong">${esc(p.pole_code)}</td><td>${esc(p.barangay || "—")}</td>
          <td class="num">${p.inspections}</td><td class="num">${fmt(p.latest?.max_crs, 1)}</td>
          <td class="num">${t == null ? '<span class="muted">—</span>' : `<span class="trend ${t > 0.05 ? "up" : t < -0.05 ? "down" : ""}">${t > 0 ? "+" : ""}${fmt(t, 1)}</span>`}</td>
          <td>${levelChip(p.latest?.level, p.latest?.status)}</td><td class="muted">${p.has_location ? "recorded" : "—"}</td></tr>`;
      }).join("")}</tbody></table>` : `<p class="empty-line">No poles yet.</p>`;
  }).catch(fail("#poles"));
  let t = 0;
  $("#pf").addEventListener("input", (e) => { clearTimeout(t); t = setTimeout(() => load(e.target.value.trim()), 180); });
  $("#pf").addEventListener("submit", (e) => e.preventDefault());
  bindRowLinks($("#poles"));
  load();
  return () => clearTimeout(t);
}

function mountPole(code) {
  view.innerHTML = `<section class="page"><div id="pole"><p class="empty-line">Loading…</p></div></section>`;
  api(`/api/admin/poles/${encodeURIComponent(code)}`).then((p) => {
    const h = p.history.filter((x) => x.max_crs != null);
    $("#pole").innerHTML = `${page(esc(p.pole_code), `<a href="/admin/poles" data-link>Poles</a> / ${esc(p.barangay || "No barangay")} · ${p.history.length} inspection${p.history.length === 1 ? "" : "s"}`)}
      <section class="panel"><header class="panel-head"><h2>Worst wire over time</h2><span class="muted">${tip("crs", "max CRS")} per inspection</span></header>
        ${h.length >= 1 ? trendChart(h) : `<p class="empty-line">No scored inspection yet.</p>`}
        <p class="hint">${esc(p.note)}</p></section>
      <table class="tbl tbl-lg"><thead><tr><th>Inspected</th><th class="num">Max CRS</th><th class="num">Mean CRS</th><th class="num">Max δ/L</th><th class="num">VNI</th><th>Level</th><th>Source</th><th>Review</th></tr></thead>
        <tbody>${p.history.slice().reverse().map((x) => `<tr data-href="/admin/analyses/${x.analysis_id}"><td>${new Date(x.at).toLocaleString()}</td>
          <td class="num strong">${fmt(x.max_crs, 2)}</td><td class="num">${fmt(x.mean_crs, 2)}</td><td class="num">${pct(x.max_sag_ratio)}</td><td class="num">${fmt(x.vni, 2)}</td>
          <td>${levelChip(x.level, x.status)}</td><td class="muted">${x.source || "—"}</td>
          <td>${x.review ? `<span class="rev ${x.review.decision}">${x.review.decision}</span>` : '<span class="muted">—</span>'}</td></tr>`).join("")}</tbody></table>`;
    bindRowLinks($("#pole"));
  }).catch(fail("#pole"));
}

function trendChart(h) {
  const W = 880, H = 220, L = 40, R = 16, T = 14, B = 30;
  const t0 = new Date(h[0].at).getTime(), t1 = new Date(h.at(-1).at).getTime();
  const X = (t) => (h.length === 1 ? L + (W - L - R) / 2 : L + ((new Date(t).getTime() - t0) / (t1 - t0 || 1)) * (W - L - R));
  const Y = (v) => T + (1 - v / 10) * (H - T - B);
  const bands = `<rect x="${L}" y="${Y(10)}" width="${W - L - R}" height="${Y(6) - Y(10)}" class="band high"/>
    <rect x="${L}" y="${Y(6)}" width="${W - L - R}" height="${Y(3) - Y(6)}" class="band medium"/>
    <rect x="${L}" y="${Y(3)}" width="${W - L - R}" height="${Y(0) - Y(3)}" class="band low"/>`;
  const ticks = [0, 3, 6, 10].map((v) => `<text x="${L - 8}" y="${Y(v) + 4}" text-anchor="end">${v}</text>`).join("");
  const path = h.map((x, i) => `${i ? "L" : "M"}${X(x.at).toFixed(1)} ${Y(x.max_crs).toFixed(1)}`).join(" ");
  const dots = h.map((x) => `<circle cx="${X(x.at).toFixed(1)}" cy="${Y(x.max_crs).toFixed(1)}" r="4.5" class="pt ${x.level}"><title>${new Date(x.at).toLocaleString()} · CRS ${fmt(x.max_crs, 2)} · δ/L ${pct(x.max_sag_ratio)}</title></circle>`).join("");
  const sameDay = new Date(h[0].at).toDateString() === new Date(h.at(-1).at).toDateString();
  const when = (x) => (sameDay ? new Date(x.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : new Date(x.at).toLocaleDateString());
  const labels = (h.length === 1 ? [[h[0], "middle"]] : [[h[0], "start"], [h.at(-1), "end"]])
    .map(([x, anchor]) => `<text x="${X(x.at)}" y="${H - 8}" text-anchor="${anchor}">${when(x)}</text>`).join("");
  return `<svg class="trend-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Maximum CRS per inspection over time">${bands}${ticks}<path d="${path}" class="ln"/>${dots}${labels}</svg>`;
}

// ------------------------------------------------------------------ models & queue
function mountModels() {
  view.innerHTML = `<section class="page">${page("Models & queue", "Which segmentation source runs, and what is waiting.")}<div id="mq"></div></section>`;
  const load = () => Promise.all([api("/api/admin/models"), api("/api/admin/queue")]).then(([m, q]) => {
    $("#mq").innerHTML = `
      <div class="cols">
        <section class="panel"><header class="panel-head"><h2>Segmentation source</h2><span class="mode-pill" data-mode="${m.mode}">${m.mode === "field" ? "Model active" : "Reference masks only"}</span></header>
          <dl class="kv">
            <div><dt>Active checkpoint</dt><dd class="mono">${esc(m.ready ? m.active : "none loaded")}</dd></div>
            <div><dt>Confidence threshold</dt><dd class="mono">${m.conf_min}${m.conf_provisional ? ' <span class="chip medium">provisional</span>' : ' <span class="chip low">tuned on validation</span>'}</dd></div>
            <div><dt>NMS IoU</dt><dd class="mono">${m.iou_nms}</dd></div>
            ${!m.ready && m.error ? `<div><dt>Status</dt><dd class="muted">${esc(m.error)}</dd></div>` : ""}
          </dl>
          <label class="switch"><input type="checkbox" id="reqref" ${m.require_reference_masks ? "checked" : ""}>
            <span class="sw" aria-hidden="true"></span>
            <span><b>Require reference masks</b><small>Even with a model loaded, accept only uploads that carry polygons: for validation runs against ground truth.</small></span></label>
        </section>
        <section class="panel"><header class="panel-head"><h2>Checkpoints in models/</h2></header>
          ${m.checkpoints.length ? `<table class="tbl"><tbody>${m.checkpoints.map((c) => `<tr><td class="strong mono">${esc(c.name)}</td><td class="muted">${c.size_mb} MB · ${ago(c.modified)}</td>
            <td class="right">${c.active ? '<span class="chip low">active</span>' : `<button class="btn" data-activate="${esc(c.name)}">Activate</button>`}</td></tr>`).join("")}</tbody></table>`
            : `<p class="empty-line">No checkpoints. Train with <code>python -m ml.train</code>; it installs <code>models/wireframe-yolo11m-seg.pt</code>.</p>`}
          <p class="hint">A checkpoint must load as a segmentation model with the four WIREFRAME classes before it replaces the active one.</p>
        </section>
      </div>
      <section class="panel"><header class="panel-head"><h2>Queue</h2><span class="muted">${q.depth} waiting · ${q.processing.length} running · ${q.processed_since_start} processed since start</span></header>
        ${q.processing.length ? `<p class="hint">Running: ${q.processing.map((p) => esc(p.pole_code)).join(", ")}</p>` : ""}
        ${q.errors.length ? `<table class="tbl"><thead><tr><th>Pole</th><th>Error</th><th>When</th><th></th></tr></thead><tbody>${q.errors.map((e) => `<tr>
          <td class="strong"><a href="/admin/analyses/${e.id}" data-link>${esc(e.pole_code)}</a></td><td class="muted">${esc((e.code || "").replace(/_/g, " "))}</td>
          <td class="muted">${ago(e.at)}</td><td class="right"><button class="btn" data-retry="${e.id}">Retry</button></td></tr>`).join("")}</tbody></table>`
          : `<p class="empty-line">No failed analyses.</p>`}
      </section>`;
    $("#reqref").addEventListener("change", async (e) => {
      try { await api("/api/admin/settings", json("PUT", { require_reference_masks: e.target.checked })); toast.success(e.target.checked ? "Reference masks now required" : "Model segmentation re-enabled"); load(); }
      catch (ex) { e.target.checked = !e.target.checked; toast.error("Setting not saved", { description: ex.message }); }
    });

  }).catch(fail("#mq"));
  $("#mq").addEventListener("click", async (e) => {
      const act = e.target.closest("[data-activate]"), rt = e.target.closest("[data-retry]");
      if (act) {
        const id = toast.loading(`Loading ${act.dataset.activate}…`);
        try { await api("/api/admin/models/active", json("POST", { name: act.dataset.activate })); update(id, "Checkpoint active", { type: "success" }); load(); }
        catch (ex) { update(id, "Checkpoint rejected", { type: "error", description: ex.message }); }
      }
      if (rt) {
        try { await api(`/api/analysis/${rt.dataset.retry}/retry`, { method: "POST" }); toast.success("Re-queued"); load(); }
        catch (ex) { toast.error("Retry not possible", { description: ex.message }); }
      }
    });
  load();
}

// ------------------------------------------------------------------ exports & batch
function mountExports() {
  view.innerHTML = `<section class="page">${page("Exports", "Manifests for spreadsheets, an executive report for the LGU, and batch screening.")}
    <div class="cards">
      <a class="card-link" href="/api/admin/exports/manifest.csv"><b>Site manifest</b><span>One row per pole: rank, MSPI, level, review, location. CSV, opens in Excel.</span><em>Download CSV</em></a>
      <a class="card-link" href="/api/admin/exports/wires.csv"><b>Wire measurements</b><span>Every wire: class, fit parameters a, b, c, residuals, L, δ, δ/L, δ_lim, CRS.</span><em>Download CSV</em></a>
      <a class="card-link" href="/api/admin/exports/sites.pdf" target="_blank" rel="noopener"><b>Executive priority report</b><span>Ranked poles, review state and weight sensitivity, with the screening disclaimer on every page.</span><em>Open PDF</em></a>
    </div>
    <section class="panel"><header class="panel-head"><h2>Batch screening</h2><span class="muted">Pair each photo with an optional same-named .txt of polygons</span></header>
      <label class="batch-drop"><input type="file" id="batch" multiple accept="image/jpeg,image/png,image/webp,image/tiff,.txt">
        <span>Choose photos (and their .txt polygons)</span></label>
      <label class="check"><input type="checkbox" id="stem-code" checked> Use the file name as the pole code</label>
      <ol class="batch-list" id="batch-list"></ol>
      <button class="btn primary" id="batch-run" disabled>Screen 0 photos</button>
    </section></section>`;
  let pairs = [];
  $("#batch").addEventListener("change", (e) => {
    const files = [...e.target.files];
    const labels = new Map(files.filter((f) => f.name.toLowerCase().endsWith(".txt")).map((f) => [f.name.replace(/\.[^.]+$/, ""), f]));
    pairs = files.filter((f) => f.type.startsWith("image/")).map((f) => ({ img: f, labels: labels.get(f.name.replace(/\.[^.]+$/, "")) || null, state: "ready" }));
    renderBatch();
  });
  const renderBatch = () => {
    $("#batch-list").innerHTML = pairs.map((p, i) => `<li data-i="${i}"><span class="mono">${esc(p.img.name)}</span>
      <span class="muted">${p.labels ? "with polygons" : "photo only"}</span><span class="b-state ${p.state}">${esc(p.msg || p.state)}</span></li>`).join("");
    const btn = $("#batch-run"); btn.disabled = !pairs.some((p) => p.state === "ready"); btn.textContent = `Screen ${pairs.filter((p) => p.state === "ready").length} photos`;
  };
  $("#batch-run").addEventListener("click", async () => {
    const useStem = $("#stem-code").checked;
    for (const p of pairs.filter((x) => x.state === "ready")) {
      p.state = "uploading"; renderBatch();
      const fd = new FormData(); fd.append("file", p.img);
      if (p.labels) fd.append("labels", p.labels);
      if (useStem) fd.append("pole_code", p.img.name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9_.-]/g, "-").slice(0, 64));
      try {
        const r = await api("/api/analysis/upload", { method: "POST", body: fd });
        p.state = "queued"; p.id = r.id; renderBatch();
        waitForAnalysis(r.id).then((s) => { p.state = s.status === "complete" ? "done" : "failed"; p.msg = STATUS_LABEL[s.status]; renderBatch(); });
      } catch (ex) { p.state = "failed"; p.msg = ex.message; renderBatch(); }
    }
    toast.success("Batch submitted", { description: "Results appear under Analyses as they finish." });
  });
}

boot();
