// v2.21 Map & Dispatch.
// Map: keyless base maps (CARTO's basemaps now return an "API KEY REQUIRED" image, so they are not used):
//   Light = OpenStreetMap, Dark = Esri Dark Gray Canvas + labels, Satellite = Esri World Imagery + place labels.
//   If a base map fails to load, the page falls back to OpenStreetMap and says so.
// Markers: priority pins only, coloured by the pole's latest inspection (red high, amber moderate, green low, grey
//   not assessed); poles that share a recorded GPS point are clustered and spiderfied, never moved.
// Sidebar: (1) tabs + filters that combine, (2) the dispatch route drawer once a pole is ticked, (3) pole cards.
// Route: ordered stops with remove / move up / move down, "Shortest straight-line order" (nearest neighbour + 2-opt
//   on straight-line distance, labelled as such), a printable route PDF and a shareable link (sign-in required).
//   Road distance and travel time come from Google Maps; nothing here invents them.
// Needs pinning: poles without a usable location; administrators pin them by clicking the map or typing
//   coordinates, validated on the server and recorded in the audit trail.

import { toast } from "./toast.js?v=2.23";

const RISK_VAR = { high: "--risk-high", medium: "--risk-med", low: "--risk-low", unassessed: "--risk-none" };
const RISK_ORDER = { high: 0, medium: 1, unassessed: 2, low: 3 };
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const ll = (l) => `${l.lat.toFixed(6)},${l.lon.toFixed(6)}`;
const gmaps = {
  walk: (l) => `https://www.google.com/maps/dir/?api=1&destination=${ll(l)}&travelmode=walking`,
  street: (l) => `https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${ll(l)}`,
  route: (stops) => `https://www.google.com/maps/dir/?api=1&origin=${ll(stops[0])}&destination=${ll(stops.at(-1))}`
    + (stops.length > 2 ? `&waypoints=${stops.slice(1, -1).map(ll).join("%7C")}` : "") + "&travelmode=driving",
};
const OSM = ["https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }];
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const BASE = {
  light: [OSM],
  dark: [[`${ESRI}/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, maxNativeZoom: 16, attribution: "Tiles &copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors" }],
    [`${ESRI}/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 16, maxNativeZoom: 16 }]],
  satellite: [[`${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, maxNativeZoom: 18, attribution: "Imagery &copy; Esri, Maxar, Earthstar Geographics" }],
    [`${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 19, maxNativeZoom: 18 }]],
};
const MAX_STOPS = 25;
const PH = { lat: [4.2, 21.3], lon: [116, 127] };
const locProblem = (lat, lon) => {
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return "Enter both latitude and longitude as numbers.";
  if (lat === 0 && lon === 0) return "0, 0 is not a real location.";
  if (lat < PH.lat[0] || lat > PH.lat[1] || lon < PH.lon[0] || lon > PH.lon[1]) return "That point is outside the Philippines. Check that latitude and longitude are not swapped.";
  return null;
};
const usable = (l) => !!l && !locProblem(l.lat, l.lon);
const km = (a, b) => {
  const r = Math.PI / 180, h = Math.sin((b.lat - a.lat) * r / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin((b.lon - a.lon) * r / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.sqrt(h));
};
const pathKm = (pts) => pts.reduce((s, p, i) => (i ? s + km(pts[i - 1], p) : 0), 0);
/** Shortest straight-line visiting order from the first stop: nearest neighbour, then 2-opt until no gain. */
function shortestOrder(pts) {
  if (pts.length < 3) return pts.map((_, i) => i);
  const left = pts.map((_, i) => i).slice(1), ord = [0];
  while (left.length) { const last = pts[ord.at(-1)]; left.sort((a, b) => km(last, pts[a]) - km(last, pts[b])); ord.push(left.shift()); }
  for (let improved = true, n = 0; improved && n < 50; n++) {
    improved = false;
    for (let i = 1; i < ord.length - 1; i++) for (let k = i + 1; k < ord.length; k++) {
      const cand = [...ord.slice(0, i), ...ord.slice(i, k + 1).reverse(), ...ord.slice(k + 1)];
      if (pathKm(cand.map((j) => pts[j])) + 1e-9 < pathKm(ord.map((j) => pts[j]))) { ord.splice(0, ord.length, ...cand); improved = true; }
    }
  }
  return ord;
}

export function mountMap(view, { api, json, esc, navigate, riskBadge, RISK, REVIEW, isAdmin }) {
  const params = new URLSearchParams(location.search);
  let base = (() => { try { return localStorage.getItem("wf-map-base"); } catch { return null; } })() || (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  if (!BASE[base]) base = "light";
  view.innerHTML = `<section class="dispatch">
    <header class="pg-head pg-head-line dsp-head"><div class="pg-title-line"><h1 class="pg-title">Map &amp; Dispatch</h1><span class="pg-count" id="dsp-count"></span></div>
      <div class="pg-actions"><a class="btn" href="/api/admin/exports/sites.pdf" target="_blank" rel="noopener">Priority report (PDF)</a></div></header>
    <div class="dsp-body">
      <div class="dsp-map">
        <div id="map" class="wf-map" role="region" aria-label="Pole map"></div>
        <form class="place-search" id="place" role="search"><label class="sr-only" for="place-q">Find a street, barangay or pole</label>
          <input id="place-q" type="search" placeholder="Find a street or barangay" autocomplete="off">
          <button class="btn sm" type="submit">Find</button><ol class="place-res" id="place-res" hidden></ol></form>
        <ul class="map-legend" aria-label="Marker colours"><li><i style="background:var(--risk-high)"></i>High</li><li><i style="background:var(--risk-med)"></i>Moderate</li><li><i style="background:var(--risk-low)"></i>Low</li><li><i style="background:var(--risk-none)"></i>Not assessed</li></ul>
        <div class="seg base-seg" role="radiogroup" aria-label="Base map">${[["light", "Light"], ["dark", "Dark"], ["satellite", "Satellite"]].map(([k, t]) => `<button type="button" role="radio" data-base="${k}" aria-checked="${k === base}">${t}</button>`).join("")}</div>
        <div class="pin-bar" id="pin-bar" hidden role="status"></div>
        <div class="map-note" id="map-note" hidden></div>
      </div>
      <aside class="dsp-list" aria-label="Poles">
        <div class="dsp-zone">
          <div class="dsp-tabs" role="tablist"><button type="button" role="tab" data-tab="map" aria-selected="true">On map <b class="num" id="n-map"></b></button>
            <button type="button" role="tab" data-tab="pinning" aria-selected="false">Needs pinning <b class="num pin-n" id="n-pin"></b></button></div>
          <form class="dsp-filters" id="df">
            <input type="search" name="q" placeholder="Search pole ID, code or barangay" value="${esc(params.get("q") || "")}" aria-label="Search the list">
            <div class="chips" role="group" aria-label="Risk level">${[["", "All"], ["high", "High"], ["medium", "Moderate"], ["low", "Low"], ["unassessed", "Not assessed"]].map(([k, t]) => `<button type="button" data-risk="${k}" aria-pressed="${(params.get("risk") || "") === k}">${t}</button>`).join("")}</div>
            <select name="barangay" aria-label="Barangay"><option value="">All barangays</option></select>
          </form>
        </div>
        <section class="route-card" id="route" hidden aria-label="Dispatch route"></section>
        <p class="pin-explain" id="pin-explain" hidden></p>
        <ol class="ranks" id="ranks"></ol>
      </aside>
    </div>
  </section>`;
  const $ = (s) => view.querySelector(s);
  const f = $("#df");
  let rows = [], map = null, tiles = [], cluster = null, markers = new Map(), fitted = false, placeLayer = null, routeLine = null, pinLayer = null;
  let active = params.get("pole"), risk = params.get("risk") || "", tab = params.get("tab") === "pinning" ? "pinning" : "map";
  let stops = (params.get("route") || "").split(",").filter(Boolean).slice(0, MAX_STOPS), routeOpen = true, pinning = null, tileErr = 0, tileOk = 0;

  const note = (t) => { const n = $("#map-note"); n.hidden = !t; n.textContent = t || ""; };
  if (window.L) {
    map = L.map($("#map"), { zoomControl: false, worldCopyJump: true }).setView([14.36, 120.94], 13);
    L.control.zoom({ position: "topright" }).addTo(map);
    setBase(base);
    map.on("click", onMapClick);
  } else note("The map could not be loaded (no internet connection?). The lists still work.");

  function setBase(k, fallback = false) {
    base = k; if (!fallback) { try { localStorage.setItem("wf-map-base", k); } catch { /* ignore */ } }
    tiles.forEach((t) => t.remove()); tileErr = 0; tileOk = 0;
    tiles = (fallback ? [OSM] : BASE[k]).map(([u, o]) => L.tileLayer(u, o).addTo(map));
    tiles[0].on("tileload", () => { tileOk++; });
    tiles[0].on("tileerror", () => { // a provider that stops serving tiles: fall back to OpenStreetMap
      if (++tileErr >= 6 && !tileOk && k !== "light") { setBase("light", true); note(`The ${k} base map is unavailable; showing OpenStreetMap.`); setTimeout(() => note(""), 6000); }
    });
    tiles.forEach((t) => t.bringToBack());
    view.querySelectorAll("[data-base]").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.base === (fallback ? "light" : k))));
    $("#map").dataset.base = fallback ? "light" : k;
  }

  const located = (r) => usable(r.location);
  const byCode = (c) => rows.find((r) => r.pole_code === c);
  const placeLine = (r) => r.barangay ? esc([r.barangay, r.city].filter(Boolean).join(", ")) : located(r) ? `<span class="mono">${r.location.lat.toFixed(4)}, ${r.location.lon.toFixed(4)}</span>` : '<span class="muted">No location</span>';
  const matches = (r) => {
    const q = (f.q.value || "").trim().toLowerCase(), b = f.barangay.value;
    return (!risk || r.risk === risk) && (!b || (b === "__none__" ? !r.barangay : r.barangay === b))
      && (!q || [r.pole_code, r.label, r.barangay, r.inspection_name, r.city].some((x) => (x || "").toLowerCase().includes(q)));
  };
  const order = (a, b) => (a.rank ?? 1e9) - (b.rank ?? 1e9) || (RISK_ORDER[a.risk] ?? 4) - (RISK_ORDER[b.risk] ?? 4);

  function render() {
    const vis = rows.filter(matches).sort(order);
    const onMap = vis.filter(located), pin = vis.filter((r) => !located(r));
    const nPinAll = rows.filter((r) => !located(r)).length;
    $("#n-map").textContent = rows.filter(located).length; $("#n-pin").textContent = nPinAll;
    $("#n-pin").classList.toggle("has", nPinAll > 0);
    view.querySelectorAll("[data-tab]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $("#dsp-count").textContent = `${rows.length} pole${rows.length === 1 ? "" : "s"}`;
    const pe = $("#pin-explain");
    pe.hidden = tab !== "pinning" || !nPinAll;
    pe.innerHTML = `<b>${nPinAll} pole${nPinAll === 1 ? " has" : "s have"} no usable GPS location</b>, so ${nPinAll === 1 ? "it is" : "they are"} not on the map and cannot be added to a route. Their place in the inspection order is unchanged.${isAdmin() ? " Choose <b>Pin location</b>, then click the map or type the coordinates." : " An administrator can pin them."}`;
    const list = tab === "map" ? onMap : pin;
    $("#ranks").innerHTML = list.map((r) => {
      const sel = stops.includes(r.pole_code);
      return `<li class="pcard${sel ? " is-stop" : ""}" data-code="${esc(r.pole_code)}" tabindex="0" aria-selected="${r.pole_code === active}">
        ${tab === "map" ? `<input type="checkbox" class="pc-cb" data-stop="${esc(r.pole_code)}" ${sel ? "checked" : ""} aria-label="Add ${esc(r.label || r.pole_code)} to the dispatch route">` : `<span class="pc-cb"></span>`}
        <div class="pc-main"><p class="pc-l1"><b>${esc(r.label || r.pole_code)}</b><span class="mono">${esc(r.pole_code)}</span></p><p class="pc-l2">${placeLine(r)}</p></div>
        <div class="pc-side">${riskBadge(r.risk)}
          ${tab === "pinning" && isAdmin() ? `<button class="btn sm" type="button" data-pin="${esc(r.pole_code)}">${pinning === r.pole_code ? "Cancel" : "Pin location"}</button>` : `<a class="go" href="/admin/inspections/${r.analysis_id}" data-link>Open</a>`}</div></li>`;
    }).join("") || `<li class="empty">${!rows.length ? "No inspected poles yet." : tab === "pinning" ? (nPinAll ? "No pole matches these filters." : "Every pole has a location.") : "No pole on the map matches these filters."}</li>`;
    drawMarkers(rows.filter(located));
    drawRoute();
  }

  function icon(r) {
    const n = stops.indexOf(r.pole_code);
    return L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
      html: `<span class="pin pin-${esc(r.risk)}${r.pole_code === active ? " is-active" : ""}${n >= 0 ? " is-stop" : ""}" style="--pc:${css(RISK_VAR[r.risk] || "--risk-none")}">${n >= 0 ? n + 1 : ""}</span>` });
  }
  function drawMarkers(all) {
    if (!map) return;
    const shown = all.filter(matches);
    cluster?.remove(); markers.clear();
    cluster = L.markerClusterGroup ? L.markerClusterGroup({ showCoverageOnHover: false, spiderfyOnMaxZoom: true, zoomToBoundsOnClick: true, maxClusterRadius: 34,
      iconCreateFunction: (c) => { const k = c.getAllChildMarkers().map((m) => m.options.risk).sort((a, b) => (RISK_ORDER[a] ?? 4) - (RISK_ORDER[b] ?? 4))[0];
        return L.divIcon({ className: "", iconSize: [34, 34], html: `<span class="clu" style="--pc:${css(RISK_VAR[k] || "--risk-none")}">${c.getChildCount()}</span>` }); } }) : L.layerGroup();
    for (const r of shown) {
      const m = L.marker([r.location.lat, r.location.lon], { icon: icon(r), title: `${r.label || r.pole_code} · ${RISK[r.risk]}`, keyboard: true, riseOnHover: true, risk: r.risk,
        zIndexOffset: { high: 300, medium: 200 }[r.risk] || 0 });
      m.bindTooltip(`<b>${esc(r.label || r.pole_code)}</b> · ${esc(RISK[r.risk])}`, { direction: "top", offset: [0, -12] });
      m.on("click", () => select(r.pole_code, { pan: false }));
      cluster.addLayer(m); markers.set(r.pole_code, m);
    }
    cluster.addTo(map);
    if (!fitted && rows.length) {
      fitted = true;
      const pts = all.map((r) => [r.location.lat, r.location.lon]);
      if (pts.length === 1) map.setView(pts[0], 17, { animate: false });
      else if (pts.length) map.fitBounds(L.latLngBounds(pts).pad(0.12), { maxZoom: 17, animate: false });
    }
    if (rows.length && !all.length) note("No pole has a GPS location yet. They are listed under Needs pinning.");
  }

  function popupHtml(r) {
    const l = r.location;
    return `<div class="pp"><p class="pp-top"><b>${esc(r.label || r.pole_code)}</b><span class="mono muted">${esc(r.pole_code)}</span>${riskBadge(r.risk)}</p>
      <p class="pp-sub">${r.barangay ? esc(r.barangay) + " · " : ""}<span class="mono">${l.lat.toFixed(6)}, ${l.lon.toFixed(6)}</span></p>
      <p class="pp-sub">${r.rank ? `Number ${r.rank} in the inspection order` : "Not ranked"} · ${esc(REVIEW[r.review] || "")}</p>
      <div class="pp-acts"><a class="btn sm" href="/admin/inspections/${r.analysis_id}" data-link>Open inspection</a>
        <a class="btn sm" href="${gmaps.walk(l)}" target="_blank" rel="noopener">Directions</a>
        <a class="btn sm" href="${gmaps.street(l)}" target="_blank" rel="noopener">Street View</a></div></div>`;
  }
  function select(code, { pan = true } = {}) {
    const prev = active; active = code;
    const r = byCode(code); if (!r) return;
    const want = located(r) ? "map" : "pinning";
    if (tab !== want) { tab = want; render(); }
    view.querySelectorAll(".pcard").forEach((li) => li.setAttribute("aria-selected", String(li.dataset.code === code)));
    view.querySelector(`.pcard[data-code="${CSS.escape(code)}"]`)?.scrollIntoView({ block: "nearest", behavior: reduced() ? "auto" : "smooth" });
    for (const k of [prev, code]) { const m = markers.get(k), x = byCode(k); if (m && x) m.setIcon(icon(x)); }
    const m = markers.get(code);
    if (!m || !map) return;
    const open = () => L.popup({ offset: [0, -10], className: "wf-pop", maxWidth: 330 }).setLatLng(m.getLatLng()).setContent(popupHtml(r)).openOn(map);
    if (!pan) return open();
    const go = () => { if (reduced()) { map.setView(m.getLatLng(), Math.max(map.getZoom(), 18)); open(); } else { map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 18), { duration: 0.6 }); map.once("moveend", open); } };
    cluster?.zoomToShowLayer ? cluster.zoomToShowLayer(m, go) : go();
  }

  // ---- dispatch route drawer
  function drawRoute() {
    stops = stops.filter((c) => { const r = byCode(c); return !rows.length || (r && located(r)); });
    const el = $("#route"), pts = stops.map(byCode).filter(Boolean);
    routeLine?.remove(); routeLine = null;
    el.hidden = !pts.length;
    const q = new URLSearchParams(location.search);
    stops.length ? q.set("route", stops.join(",")) : q.delete("route");
    history.replaceState({}, "", `/admin/map${q.toString() ? "?" + q : ""}`);
    if (!pts.length) return;
    if (map && pts.length > 1) routeLine = L.polyline(pts.map((r) => [r.location.lat, r.location.lon]), { color: css("--accent"), weight: 3, dashArray: "6 7", opacity: 0.95, interactive: false }).addTo(map);
    const dist = pathKm(pts.map((r) => r.location));
    el.innerHTML = `<header class="rc-head"><button type="button" class="rc-toggle" aria-expanded="${routeOpen}" aria-controls="rc-body"><b>Dispatch route</b><span class="muted">${pts.length} stop${pts.length === 1 ? "" : "s"}${pts.length > 1 ? ` · ${dist.toFixed(2)} km straight-line` : ""}</span><i aria-hidden="true">${routeOpen ? "−" : "+"}</i></button></header>
      <div id="rc-body" ${routeOpen ? "" : "hidden"}>
        <ol class="rc-stops">${pts.map((r, i) => `<li><span class="num">${i + 1}.</span><button type="button" class="rc-name" data-goto="${esc(r.pole_code)}">${esc(r.label || r.pole_code)}</button>${riskBadge(r.risk)}
          <span class="rc-ops"><button type="button" data-move="${i}:-1" aria-label="Move up" ${i ? "" : "disabled"}>↑</button><button type="button" data-move="${i}:1" aria-label="Move down" ${i < pts.length - 1 ? "" : "disabled"}>↓</button><button type="button" data-unstop="${esc(r.pole_code)}" aria-label="Remove ${esc(r.label || r.pole_code)}">×</button></span></li>`).join("")}</ol>
        <div class="rc-acts">
          ${pts.length > 2 ? `<button class="btn sm" type="button" id="rc-opt" title="Reorders the stops after the first for the shortest straight-line path. It does not use roads.">Shortest straight-line order</button>` : ""}
          <a class="btn sm" href="/api/admin/routes/pdf?stops=${encodeURIComponent(stops.join(","))}" target="_blank" rel="noopener">Route PDF</a>
          <button class="btn sm" type="button" id="rc-link">Copy link</button>
          ${pts.length > 1 ? `<a class="btn sm" href="${gmaps.route(pts.map((r) => r.location))}" target="_blank" rel="noopener">Google Maps</a>` : ""}
          <button class="link-btn" type="button" id="rc-clear">Clear</button></div>
        <p class="hint rc-note">Distances are straight lines between recorded points. Road distance and travel time: open the route in Google Maps.</p></div>`;
  }
  const setStops = (next) => { stops = next.slice(0, MAX_STOPS); render(); };

  // ---- pin location
  function startPin(code) {
    pinning = pinning === code ? null : code;
    const r = byCode(code), bar = $("#pin-bar");
    pinLayer?.remove(); pinLayer = null; map?.closePopup();
    bar.hidden = !pinning; $("#map").classList.toggle("picking", !!pinning);
    if (pinning) {
      bar.innerHTML = `<p>Click the map where <b>${esc(r.label || r.pole_code)}</b> stands, or enter coordinates:</p>
        <form class="pin-form" id="pin-form"><label><span class="sr-only">Latitude</span><input name="lat" inputmode="decimal" placeholder="Latitude, e.g. 14.36771"></label>
          <label><span class="sr-only">Longitude</span><input name="lon" inputmode="decimal" placeholder="Longitude, e.g. 120.93059"></label>
          <button class="btn sm" type="submit">Preview</button><button class="link-btn" type="button" id="pin-x">Cancel</button></form>`;
      bar.querySelector("input").focus();
    }
    render();
  }
  function onMapClick(e) { if (pinning) confirmPin(+e.latlng.lat.toFixed(6), +e.latlng.lng.toFixed(6)); }
  function confirmPin(lat, lon) {
    const bad = locProblem(lat, lon);
    if (bad) { toast.error("Location not usable", { description: bad }); return; }
    const code = pinning, r = byCode(code);
    pinLayer?.remove();
    pinLayer = L.circleMarker([lat, lon], { radius: 9, color: css("--accent"), weight: 3, fillOpacity: 0.2 }).addTo(map);
    map.setView([lat, lon], Math.max(map.getZoom(), 17));
    const box = L.popup({ className: "wf-pop", maxWidth: 330 }).setLatLng([lat, lon]).setContent(`<div class="pp">
      <p class="pp-top"><b>Pin ${esc(r.label || r.pole_code)} here?</b></p><p class="pp-sub mono">${lat.toFixed(6)}, ${lon.toFixed(6)}</p>
      <p class="pp-sub" id="pp-near">Looking up the nearest place…</p>
      <div class="pp-acts"><button class="btn sm primary" type="button" id="pp-save">Save location</button><button class="btn sm" type="button" id="pp-cancel">Choose again</button></div></div>`).openOn(map);
    const root = box.getElement();
    let near = null;
    reverse(lat, lon).then((n) => {
      const p = root?.querySelector("#pp-near"); if (!p) return;
      near = n?.barangay || null;
      p.innerHTML = n ? `Nearest named area (OpenStreetMap): <b>${esc(n.label)}</b>${near && !r.barangay ? `<label class="chk sm"><input type="checkbox" id="pp-brgy"> Also set ${esc(near)} as the barangay</label>` : ""}` : "No reliable place name here; the barangay is left as it is.";
    });
    root.querySelector("#pp-cancel").addEventListener("click", () => { map.closePopup(); pinLayer?.remove(); });
    root.querySelector("#pp-save").addEventListener("click", async (ev) => {
      ev.target.disabled = true;
      try {
        await api(`/api/admin/poles/${encodeURIComponent(code)}/location`, json("PUT", { latitude: lat, longitude: lon }));
        if (near && root.querySelector("#pp-brgy")?.checked) {
          try { await api(`/api/admin/poles/${encodeURIComponent(code)}/barangay`, json("PUT", { barangay: near })); }
          catch (ex) { toast.error("Barangay not set", { description: ex.message }); }
        }
        toast.success("Location saved", { description: `${r.label || r.pole_code} is now on the map.` });
        map.closePopup(); pinLayer?.remove(); pinning = null; $("#pin-bar").hidden = true; $("#map").classList.remove("picking");
        await load(); select(code);
      } catch (ex) { ev.target.disabled = false; toast.error("Location not saved", { description: ex.message }); }
    });
  }
  /** OpenStreetMap's nearest named area; offered as a barangay only when it is an official one (the server checks). */
  async function reverse(lat, lon) {
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/reverse?format=jsonv2&zoom=16&lat=${lat}&lon=${lon}`, { headers: { Accept: "application/json" } });
      if (!r.ok) return null;
      const a = (await r.json()).address || {};
      const label = a.quarter || a.suburb || a.village || a.neighbourhood;
      if (!label) return null;
      const hits = await api(`/api/admin/barangays?q=${encodeURIComponent(label)}`).catch(() => []);
      const exact = hits.find((h) => h.name.toLowerCase() === label.toLowerCase());
      return { label, barangay: exact?.name || null };
    } catch { return null; }
  }

  // ---- events
  const list = $("#ranks");
  list.addEventListener("click", (e) => {
    const pb = e.target.closest("[data-pin]"); if (pb) { e.stopPropagation(); startPin(pb.dataset.pin); return; }
    if (e.target.closest("a, input, button")) return; // the checkbox and Open do their own thing
    const li = e.target.closest(".pcard"); if (li) select(li.dataset.code);
  });
  list.addEventListener("change", (e) => {
    const cb = e.target.closest("[data-stop]"); if (!cb) return;
    if (cb.checked && stops.length >= MAX_STOPS) { cb.checked = false; toast.error(`A route holds up to ${MAX_STOPS} stops`); return; }
    setStops(cb.checked ? [...stops, cb.dataset.stop] : stops.filter((c) => c !== cb.dataset.stop));
  });
  const hover = (code, on) => markers.get(code)?.getElement()?.firstElementChild?.classList.toggle("is-hover", on);
  list.addEventListener("mouseover", (e) => { const li = e.target.closest(".pcard"); if (li) hover(li.dataset.code, true); });
  list.addEventListener("mouseout", (e) => { const li = e.target.closest(".pcard"); if (li && !li.contains(e.relatedTarget)) hover(li.dataset.code, false); });
  list.addEventListener("keydown", (e) => {
    const li = e.target.closest(".pcard"); if (!li || e.target !== li) return;
    if (e.key === "Enter") { e.preventDefault(); select(li.dataset.code); }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); const n = e.key === "ArrowDown" ? li.nextElementSibling : li.previousElementSibling; n?.focus?.(); }
  });
  $("#route").addEventListener("click", async (e) => {
    if (e.target.closest(".rc-toggle")) { routeOpen = !routeOpen; drawRoute(); return; }
    const u = e.target.closest("[data-unstop]"); if (u) return setStops(stops.filter((c) => c !== u.dataset.unstop));
    const mv = e.target.closest("[data-move]");
    if (mv) { const [i, d] = mv.dataset.move.split(":").map(Number), s = [...stops]; [s[i], s[i + d]] = [s[i + d], s[i]]; return setStops(s); }
    const g = e.target.closest("[data-goto]"); if (g) return select(g.dataset.goto);
    if (e.target.closest("#rc-clear")) return setStops([]);
    if (e.target.closest("#rc-opt")) {
      const pts = stops.map(byCode), before = pathKm(pts.map((r) => r.location));
      const ord = shortestOrder(pts.map((r) => r.location)); setStops(ord.map((i) => stops[i]));
      const after = pathKm(stops.map((c) => byCode(c).location));
      toast.success(after < before - 1e-6 ? `Reordered: ${before.toFixed(2)} → ${after.toFixed(2)} km straight-line` : "This order is already the shortest straight-line path");
      return;
    }
    if (e.target.closest("#rc-link")) {
      const url = `${location.origin}/admin/map?route=${encodeURIComponent(stops.join(","))}`;
      try { await navigator.clipboard.writeText(url); toast.success("Route link copied", { description: "Anyone opening it must sign in to WIREFRAME." }); }
      catch { prompt("Copy this link:", url); }
    }
  });
  $("#pin-bar").addEventListener("click", (e) => { if (e.target.closest("#pin-x")) startPin(pinning); });
  $("#pin-bar").addEventListener("submit", (e) => {
    e.preventDefault();
    const fm = e.target, lat = parseFloat(fm.lat.value), lon = parseFloat(fm.lon.value);
    const bad = locProblem(lat, lon); if (bad) { toast.error("Location not usable", { description: bad }); return; }
    confirmPin(lat, lon);
  });
  view.querySelector(".dsp-tabs").addEventListener("click", (e) => { const b = e.target.closest("[data-tab]"); if (b) { if (pinning) startPin(pinning); tab = b.dataset.tab; render(); } });
  view.querySelector(".base-seg").addEventListener("click", (e) => { const b = e.target.closest("[data-base]"); if (b && map) { note(""); setBase(b.dataset.base); } });
  view.addEventListener("click", (e) => { const a = e.target.closest(".wf-pop a[data-link]"); if (a) { e.preventDefault(); navigate(a.getAttribute("href")); } });
  f.addEventListener("input", render);
  f.addEventListener("submit", (e) => e.preventDefault());
  f.addEventListener("click", (e) => {
    const b = e.target.closest("[data-risk]"); if (!b) return;
    risk = b.dataset.risk; f.querySelectorAll("[data-risk]").forEach((x) => x.setAttribute("aria-pressed", String(x === b))); render();
  });
  const onTheme = (e) => { if (map && base !== "satellite") setBase(e.detail === "dark" ? "dark" : "light"); };
  addEventListener("wf:theme", onTheme);

  // ---- search: poles first (label, code, barangay), then streets and barangays from OpenStreetMap (on submit only)
  $("#place").addEventListener("submit", async (e) => {
    e.preventDefault();
    const q = $("#place-q").value.trim(), res = $("#place-res");
    if (!q) return;
    const k = q.toLowerCase();
    const poles = rows.filter((r) => [r.label, r.pole_code, r.barangay].some((x) => (x || "").toLowerCase().includes(k))).slice(0, 5);
    let places = [];
    try {
      const r = await fetch(`https://nominatim.openstreetmap.org/search?format=jsonv2&limit=5&countrycodes=ph&q=${encodeURIComponent(q)}`, { headers: { Accept: "application/json" } });
      places = r.ok ? await r.json() : [];
    } catch { /* offline: poles only */ }
    res.hidden = false;
    res.innerHTML = [...poles.map((r) => `<li><button type="button" data-pole="${esc(r.pole_code)}"><b>${esc(r.label || r.pole_code)}</b> <span class="muted">pole · ${esc(r.barangay || (located(r) ? "on map" : "needs pinning"))}</span></button></li>`),
      ...places.map((x, i) => `<li><button type="button" data-i="${i}">${esc(x.display_name)}</button></li>`)].join("") || `<li class="muted">Nothing found.</li>`;
    res.onclick = (ev) => {
      const pb = ev.target.closest("[data-pole]"); if (pb) { res.hidden = true; select(pb.dataset.pole); return; }
      const b = ev.target.closest("[data-i]"); if (!b || !map) return;
      const x = places[Number(b.dataset.i)];
      placeLayer?.remove();
      placeLayer = L.circleMarker([+x.lat, +x.lon], { radius: 7, color: css("--text"), weight: 2, fillOpacity: 0 }).addTo(map);
      map.setView([+x.lat, +x.lon], 17); res.hidden = true;
    };
  });
  const closeRes = (e) => { if (!e.target.closest("#place")) $("#place-res").hidden = true; };
  document.addEventListener("click", closeRes);

  const load = () => api("/api/dashboard/priority").then((d) => {
    rows = d.sites;
    const brgys = [...new Set(rows.map((r) => r.barangay).filter(Boolean))].sort();
    const keep = f.barangay.value;
    f.barangay.innerHTML = `<option value="">All barangays</option>${brgys.map((b) => `<option ${b === keep ? "selected" : ""}>${esc(b)}</option>`).join("")}${rows.some((r) => !r.barangay) ? `<option value="__none__" ${keep === "__none__" ? "selected" : ""}>Unassigned</option>` : ""}`;
    render();
  });
  load().then(() => {
    const r = active && byCode(active);
    if (r) { if (located(r)) select(active); else { tab = "pinning"; render(); if (isAdmin() && params.get("tab") === "pinning") startPin(active); } }
  }).catch((e) => { $("#ranks").innerHTML = `<li class="empty">Could not load the poles: ${esc(e.message)}</li>`; });

  return () => { removeEventListener("wf:theme", onTheme); document.removeEventListener("click", closeRes); map?.remove(); };
}
