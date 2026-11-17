// Multi-site priority workspace: GeoJSON map (RFC 7946, from /api/dashboard/map),
// MSPI ranking with live weights (Equation 11), and the weight-sensitivity summary.

import { toast } from "./toast.js?v=2.17";

const LEVEL_VAR = { low: "--risk-low", medium: "--risk-med", high: "--risk-high" };
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmt = (v, d = 3) => (v == null ? "—" : Number(v).toFixed(d));

export function mountSites(view, { api, esc, navigate }) {
  view.innerHTML = `
  <section class="sites">
    <div class="map-wrap"><div id="map" role="region" aria-label="Pole priority map"></div><div class="map-empty" id="map-empty" hidden></div></div>
    <aside class="priority">
      <div class="insp-head">
        <p class="eyebrow">Multi-site priority comparison</p>
        <h2>Inspection order</h2>
        <div class="sub" id="ranked-sub">—</div>
      </div>
      <div class="section">
        <p class="eyebrow"><span>MSPI weights</span><button class="btn" id="reset" style="height:24px;padding:0 8px;font-size:11px">Reset ⅓·⅓·⅓</button></p>
        <div class="weights">
          <label class="weight"><span>α · VNI</span><input type="range" min="0" max="1" step="0.05" id="w-alpha"><output id="o-alpha"></output></label>
          <label class="weight"><span>β · mean CRS</span><input type="range" min="0" max="1" step="0.05" id="w-beta"><output id="o-beta"></output></label>
          <label class="weight"><span>γ · max CRS</span><input type="range" min="0" max="1" step="0.05" id="w-gamma" disabled><output id="o-gamma"></output></label>
        </div>
        <p class="hint">γ = 1 − α − β. MSPI = α·VNI + β·mean(CRS)/10 + γ·max(CRS)/10 — it orders poles only; each pole keeps the risk level of its worst wire.</p>
      </div>
      <div class="section" id="sens"></div>
      <div class="scroll"><ol class="ranks" id="ranks" role="listbox" aria-label="Poles by priority"></ol></div>
    </aside>
  </section>`;

  const $ = (s) => view.querySelector(s);
  const wA = $("#w-alpha"), wB = $("#w-beta"), wG = $("#w-gamma");
  let base = { alpha: 1 / 3, beta: 1 / 3 };
  let rows = [], geo = null, map = null, layer = null, markers = new Map(), active = null, timer = 0;

  // --- map
  if (window.L) {
    map = L.map($("#map"), { zoomControl: true, attributionControl: true, preferCanvas: false }).setView([14.33, 120.94], 13);
    // OpenStreetMap standard tiles (keyless, attribution required). The dark theme is a CSS
    // filter on the tile pane only, so markers keep their true risk colours.
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
  } else {
    showMapNote("Map library could not be loaded (offline?). The priority list still works.");
  }

  function showMapNote(html) { const n = $("#map-empty"); n.hidden = !html; n.innerHTML = html ? `<div>${html}</div>` : ""; }

  function pin(f) {
    const p = f.properties;
    const color = css(LEVEL_VAR[p.level] || "--risk-none");
    const icon = L.divIcon({ className: "", iconSize: [18, 18], iconAnchor: [9, 9],
      html: `<div class="pin" style="--pc:${color}">${p.rank ?? ""}</div>` });
    const [lon, lat] = f.geometry.coordinates;
    const m = L.marker([lat, lon], { icon, title: p.pole_code, keyboard: true, riseOnHover: true });
    m.bindPopup(`<b>${esc(p.pole_code)}</b>${p.barangay ? " · " + esc(p.barangay) : ""}<br>
      <span class="mono">MSPI ${fmt(p.mspi)} · max CRS ${fmt(p.max_crs, 1)} · VNI ${fmt(p.vni, 2)}</span><br>
      <a href="/admin/analyses/${p.analysis_id}" data-link>Open analysis →</a>`);
    m.on("click", () => setActive(p.pole_code, false));
    return m;
  }

  function drawMap() {
    if (!map || !geo) return;
    layer?.remove(); markers.clear();
    layer = L.layerGroup().addTo(map);
    // draw low first so high-risk pins sit on top
    const order = { low: 0, medium: 1, high: 2 };
    [...geo.features].sort((a, b) => (order[a.properties.level] ?? -1) - (order[b.properties.level] ?? -1))
      .forEach((f) => { const m = pin(f).addTo(layer); markers.set(f.properties.pole_code, m); });
    if (geo.features.length) {
      const b = L.latLngBounds(geo.features.map((f) => [f.geometry.coordinates[1], f.geometry.coordinates[0]]));
      map.fitBounds(b.pad(0.25), { maxZoom: 17, animate: false });
      showMapNote(geo.metadata.unlocated_poles.length ? "" : "");
    } else {
      showMapNote(`No pole has a location yet. Coordinates come from photo EXIF GPS or the latitude/longitude fields on upload.${
        rows.length ? ` ${rows.length} pole(s) are ranked in the list.` : ""}`);
    }
  }

  function setActive(code, pan = true) {
    active = code;
    view.querySelectorAll(".rank").forEach((li) => li.setAttribute("aria-selected", String(li.dataset.code === code)));
    view.querySelector(`.rank[data-code="${CSS.escape(code)}"]`)?.scrollIntoView({ block: "nearest" });
    markers.forEach((m, k) => m.getElement()?.firstElementChild?.toggleAttribute("data-active", k === code));
    const m = markers.get(code);
    if (m && pan) { map.panTo(m.getLatLng(), { animate: !matchMedia("(prefers-reduced-motion: reduce)").matches }); m.openPopup(); }
  }

  function renderRanks(data, sens) {
    rows = data.sites;
    $("#ranked-sub").textContent = `${data.ranked_count} ranked · ${rows.length - data.ranked_count} not ranked${data.provisional ? " · provisional weights" : ""}`;
    const range = sens?.rank_range || {};
    $("#ranks").innerHTML = rows.map((r) => {
      const color = css(LEVEL_VAR[r.level] || "--risk-none");
      const rr = range[r.pole_code];
      return `<li class="rank" role="option" tabindex="0" data-code="${esc(r.pole_code)}" aria-selected="${r.pole_code === active}">
        <span class="rank-no">${r.rank ?? "–"}</span>
        <div><div class="rank-title">${esc(r.pole_code)} ${r.has_location ? "" : '<span class="muted mono" title="No location">· no GPS</span>'}</div>
          <div class="rank-sub">${r.n_wires} wires · VNI ${fmt(r.vni, 2)} · ${esc(r.barangay || "—")}</div></div>
        <div class="rank-score">${r.rank ? `<b style="color:${color}">${fmt(r.mspi)}</b>
          <span class="chip ${r.level}">${r.level} · ${fmt(r.max_crs, 1)}</span>
          ${rr ? `<span class="spread" title="Best and worst rank over the weight grid">rank ${rr.best}–${rr.worst} over grid</span>` : ""}`
          : `<span class="chip plain">no valid wires</span>`}</div>
      </li>`;
    }).join("") || `<li class="hint" style="padding:12px">No finished analyses yet.</li>`;
  }

  // Triangular grid: one cell per (alpha, beta) on the 0.1 simplex, gamma = 1 - alpha - beta,
  // coloured by Spearman rho of that cell's ranking against the ranking at the current sliders.
  function rhoColor(r) {
    if (r == null) return "var(--surface-3)";
    const t = Math.max(0, Math.min(1, (r + 1) / 2));
    const stops = [[229, 72, 77], [230, 180, 34], [47, 191, 113]];
    const x = t * 2, i = Math.min(1, Math.floor(x)), f = x - i;
    const c = stops[i].map((v, k) => Math.round(v + (stops[i + 1][k] - v) * f));
    return `rgb(${c.join(",")})`;
  }
  function renderSens(s) {
    // the grid point nearest to the sliders (sliders move in 0.05, the grid in 0.1)
    const near = s.grid.reduce((best, g) => {
      const d = Math.hypot(g.alpha - Number(wA.value), g.beta - Number(wB.value));
      return d < best.d ? { g, d } : best;
    }, { g: null, d: Infinity }).g;
    const cells = s.grid.map((g) => {
      const here = g === near;
      return `<button class="sg-cell${here ? " here" : ""}" style="grid-row:${11 - Math.round(g.alpha * 10)};grid-column:${Math.round(g.beta * 10) + 1};--c:${rhoColor(g.rho)}"
        data-a="${g.alpha}" data-b="${g.beta}" aria-label="alpha ${g.alpha.toFixed(1)}, beta ${g.beta.toFixed(1)}, gamma ${g.gamma.toFixed(1)}: rho ${fmt(g.rho, 2)}"
        data-tip="α ${g.alpha.toFixed(1)} · β ${g.beta.toFixed(1)} · γ ${g.gamma.toFixed(1)} → ρ ${fmt(g.rho, 2)}"></button>`;
    }).join("");
    $("#sens").innerHTML = `<p class="eyebrow"><span>Weight sensitivity</span><span>${s.grid.length} settings · vs current</span></p>
      ${s.n_sites < 2 ? '<p class="hint">Needs at least two ranked poles.</p>' : `
      <div class="sens"><div><div class="k">min ρ</div><div class="v">${fmt(s.min_rho, 2)}</div></div>
        <div><div class="k">mean ρ</div><div class="v">${fmt(s.mean_rho, 2)}</div></div>
        <div><div class="k">poles</div><div class="v">${s.n_sites}</div></div></div>
      <div class="sgrid-wrap"><span class="sg-axis y">α ↑</span><div class="sgrid">${cells}</div><span class="sg-axis x">β →</span></div>
      <p class="hint">Each cell is one weight setting (γ = 1 − α − β), coloured by Spearman ρ between its ranking and the ranking at the current sliders: green agrees, red reverses. Click a cell to try it.</p>`}`;
    $("#sens .sgrid")?.addEventListener("click", (e) => {
      const c = e.target.closest(".sg-cell"); if (!c) return;
      wA.value = c.dataset.a; wB.value = c.dataset.b; syncWeights(wA);
    });
  }

  async function load() {
    try {
      const [g, s] = await Promise.all([api("/api/dashboard/map"), api("/api/dashboard/sensitivity")]);
      geo = g; renderSens(s); view._sens = s;
      await rank();
      drawMap();
    } catch (e) { toast.error("Could not load sites", { description: e.message }); }
  }

  async function rank() {
    const a = Number(wA.value), b = Number(wB.value), g = Math.max(0, 1 - a - b);
    const data = await api(`/api/dashboard/priority?alpha=${a}&beta=${b}&gamma=${g.toFixed(6)}`);
    const sens = await api(`/api/dashboard/sensitivity?alpha=${a}&beta=${b}&gamma=${g.toFixed(6)}`);
    view._sens = sens; renderSens(sens);
    renderRanks(data, sens);
    if (geo) {
      const byCode = new Map(data.sites.map((r) => [r.pole_code, r]));
      geo.features.forEach((f) => Object.assign(f.properties, { rank: byCode.get(f.properties.pole_code)?.rank, mspi: byCode.get(f.properties.pole_code)?.mspi }));
      drawMap();
      if (active) setActive(active, false);
    }
  }

  function syncWeights(changed) {
    let a = Number(wA.value), b = Number(wB.value);
    if (a + b > 1) { if (changed === wA) b = 1 - a; else a = 1 - b; wA.value = a; wB.value = b; }
    wG.value = Math.max(0, 1 - a - b);
    $("#o-alpha").textContent = a.toFixed(2); $("#o-beta").textContent = b.toFixed(2); $("#o-gamma").textContent = (1 - a - b).toFixed(2);
    clearTimeout(timer); timer = setTimeout(() => rank().catch((e) => toast.error(e.message)), 120);
  }
  [wA, wB].forEach((el) => el.addEventListener("input", () => syncWeights(el)));
  $("#reset").addEventListener("click", () => { wA.value = base.alpha; wB.value = base.beta; syncWeights(wA); });
  $("#ranks").addEventListener("click", (e) => { const li = e.target.closest(".rank"); if (li) setActive(li.dataset.code); });
  $("#ranks").addEventListener("dblclick", (e) => {
    const li = e.target.closest(".rank"); const r = rows.find((x) => x.pole_code === li?.dataset.code);
    if (r) navigate(`/admin/analyses/${r.analysis_id}`);
  });
  $("#ranks").addEventListener("keydown", (e) => {
    const li = e.target.closest(".rank"); if (!li) return;
    if (e.key === "Enter") { const r = rows.find((x) => x.pole_code === li.dataset.code); if (r) navigate(`/admin/analyses/${r.analysis_id}`); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = e.key === "ArrowDown" ? li.nextElementSibling : li.previousElementSibling; n?.focus(); n && setActive(n.dataset.code); }
  });

  api("/api/config").then((c) => { base = { alpha: c.mspi.alpha, beta: c.mspi.beta }; wA.value = base.alpha; wB.value = base.beta;
    wG.value = c.mspi.gamma; $("#o-alpha").textContent = base.alpha.toFixed(2); $("#o-beta").textContent = base.beta.toFixed(2);
    $("#o-gamma").textContent = c.mspi.gamma.toFixed(2); load(); }).catch((e) => toast.error("Could not load configuration", { description: e.message }));

  return () => { clearTimeout(timer); map?.remove(); };
}
