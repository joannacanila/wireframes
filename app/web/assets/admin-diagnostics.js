// v2.23 technical diagnostics on the inspection page (administrators): metric cards (CRS, MSPI, VNI), quality,
// configuration version, the 2x2 acquisition envelope and the per-wire geometry table. Moved here from the removed
// System page module; nothing else of that page remains in the front end.

import { fmt, pct } from "./lib.js?v=2.23";
import { CLASS_LABEL, wireColor } from "./viewer.js?v=2.23";

// ------------------------------------------------------------------ per-inspection technical diagnostics
const TIP = {
  a: "Catenary parameter a = horizontal tension ÷ weight per length, in pixels. Larger a = tauter wire.",
  bc: "b: x of the lowest point; c: vertical offset (y-up frame, pixels).",
  rms: "Root-mean-square vertical distance between the centerline points and the fitted catenary.",
  rel: "RMS residual ÷ chord length. The fit is rejected above the residual limit.",
  chord: "L: straight distance between the two ends of the fitted curve.",
  sag: "δ: largest perpendicular distance from the fitted curve to the chord.",
  ratio: "δ/L, the sag ratio. Unchanged by image scale.",
  lim: "Allowable sag ratio for this class; development placeholder until calibrated.",
  crs: "Catenary Risk Score = min{10, 10·(δ/L ÷ δ_lim)·w_class}. Low < 3 ≤ Medium ≤ 6 < High.",
  vni: "Visual Noise Index: share of image power at high spatial frequencies. Scene clutter, not wire condition.",
  mspi: "Multi-Site Priority Index: orders poles for inspection.",
};
const tip = (k, label) => `<span class="tipped" tabindex="0" data-tip="${TIP[k]}">${label}</span>`;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const ENV = { focal_length_35mm: ["Focal length", "mm (35 mm equiv.)"], distance_m: ["Capture distance", "m"],
  elevation_deg: ["Camera elevation", "°"], daylight_hour: ["Daylight window", "h"] };
const hour = (v) => `${String(Math.floor(v)).padStart(2, "0")}:${String(Math.round((v % 1) * 60)).padStart(2, "0")}`;
const envStatus = (c) => c.status === "pass" ? ["ok", "Verified"] : c.status === "fail" ? ["bad", "Out of range"]
  : c.source === "exif" ? ["none", "Missing EXIF"] : ["none", "Not entered"];
const VNI_BAND = { low: "Low clutter", medium: "Moderate clutter", moderate: "Moderate clutter", high: "High clutter" };

export function diagnosticsHtml(r) {
  const n = (v, d) => (v == null ? "–" : fmt(v, d));
  const geo = r.wires.length ? `<div class="tbl-scroll"><table class="tbl tbl-num">
    <thead><tr><th>#</th><th>Class</th><th>Status</th><th>${tip("a", "a")}</th><th>${tip("bc", "b")}</th><th>${tip("bc", "c")}</th>
      <th>${tip("rms", "RMS px")}</th><th>${tip("rel", "RMS / L")}</th><th>${tip("chord", "L px")}</th><th>${tip("sag", "δ px")}</th>
      <th>${tip("ratio", "δ/L")}</th><th>${tip("lim", "δ_lim")}</th><th>${tip("crs", "CRS")}</th><th>Points</th></tr></thead>
    <tbody>${r.wires.map((w) => { const f = w.fit || {}; return `<tr data-dwire="${w.index}">
      <td><span class="wno" style="--wc:${wireColor(w)}">${w.index}</span></td><td class="txt">${esc(CLASS_LABEL[w.class] || w.class)}</td>
      <td class="txt">${esc(w.status === "scored" ? w.level : (w.fail_reason || w.status).replace(/_/g, " "))}</td>
      <td>${n(f.a, 0)}</td><td>${n(f.b, 0)}</td><td>${n(f.c, 0)}</td><td>${n(f.rms_residual_px, 2)}</td><td>${f.rel_residual == null ? "–" : pct(f.rel_residual, 2)}</td>
      <td>${n(w.chord_px, 0)}</td><td>${n(w.sag_px, 1)}</td><td>${pct(w.sag_ratio, 2)}</td><td>${pct(w.delta_lim, 2)}</td><td class="strong">${n(w.crs, 2)}</td><td>${f.n_points ?? "–"}</td></tr>`; }).join("")}</tbody></table></div>
    <p class="hint">Levenberg–Marquardt fit of y = a·cosh((x − b)/a) + c in the y-up image frame; chord and δ from the fitted curve.</p>` : "";
  const q = r.quality || {};
  const crs = r.summary?.max_crs, mspi = r.ranking?.mspi, vni = r.vni?.value;
  const card = (k, label, value, sub, help) => `<div class="mcard"><span class="k">${tip(k, label)}</span>
    <b class="num">${value}</b><span class="s">${sub}</span><p class="hint">${help}</p></div>`;
  const cards = [
    crs != null ? card("crs", "CRS", fmt(crs, 2), `Highest of ${r.wires.filter((w) => w.crs != null).length} scored wire${r.wires.filter((w) => w.crs != null).length === 1 ? "" : "s"}${r.summary?.mean_crs != null ? ` · mean ${fmt(r.summary.mean_crs, 2)}` : ""}`, "Catenary Risk Score, 0–10: measured sag against the reference limit, per wire.") : "",
    mspi != null ? card("mspi", "MSPI", fmt(mspi, 3), r.ranking?.rank ? `Rank ${r.ranking.rank} of ${r.ranking.of}` : "Not ranked", "Multi-Site Priority Index: orders poles for inspection.") : "",
    vni != null ? card("vni", "VNI", fmt(vni, 2), esc(VNI_BAND[r.vni?.band] || r.vni?.band || ""), "Visual Noise Index: how cluttered the photo is. Describes the scene, not the wires.") : "",
  ].filter(Boolean);
  const p = r.parameters || {};
  const outdated = p.fingerprint && p.current_fingerprint && p.fingerprint !== p.current_fingerprint;
  const env = r.acquisition?.envelope || [];
  return `${cards.length ? `<div class="mcards">${cards.join("")}</div>` : `<p class="empty">No metric was computed for this inspection.</p>`}
    ${cards.length && outdated ? `<p class="hint">These values were computed with an earlier configuration and may not compare directly with newer inspections.</p>` : ""}
    <div class="diag-grid">
      <dl class="kv">
        <div><dt>Quality grade</dt><dd>${esc(q.grade || "–")} ${q.reliability != null ? `· R ${fmt(q.reliability, 3)}` : ""} ${q.reason ? `· ${esc(q.reason)}` : ""}</dd></div>
        <div><dt>Hazard verdict</dt><dd>${esc(r.hazard?.verdict || "–")}</dd></div>
        <div><dt>Segmentation</dt><dd>${esc(r.segmentation_source || "–")} · ${esc((r.segmentation_input?.enhancement || []).join(", ") || "no enhancement")}</dd></div>
        <div><dt>Configuration</dt><dd>${outdated ? `<span class="tag tag-warn">Outdated configuration</span>` : p.config_version != null ? `Version ${esc(p.config_version)}` : "–"}
          <details class="hash"><summary>Exact parameters</summary><span class="mono">${esc(p.fingerprint || "")}${outdated ? ` (current ${esc(p.current_fingerprint)})` : ""}</span></details></dd></div>
        <div><dt>Flags</dt><dd class="mono">${esc((r.flags || []).join(", ") || "none")}</dd></div>
      </dl>
      ${env.length ? `<div><h3 class="env-h">Acquisition envelope</h3><div class="env">${env.map((c) => {
        const [lab, unit] = ENV[c.name] || [c.name.replace(/_/g, " "), ""];
        const [cls, txt] = envStatus(c);
        const val = c.value == null ? "–" : c.name === "daylight_hour" ? hour(c.value) : `${fmt(c.value, c.name === "focal_length_35mm" ? 0 : 1)} ${unit}`;
        const exp = c.name === "daylight_hour" ? `${hour(c.expected[0])}–${hour(c.expected[1])}` : `${c.expected[0]}–${c.expected[1]} ${unit}`;
        return `<div class="env-c"><span class="k">${esc(lab)}</span><b class="${c.value == null ? "muted" : ""}">${esc(val)}</b>
          <span class="env-s env-${cls}">${txt}</span><span class="hint">Expected ${esc(exp)}</span></div>`; }).join("")}</div></div>` : ""}
    </div>
    ${(r.hazard?.reasons || []).length ? `<ul class="hint diag-reasons">${r.hazard.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}
    ${geo ? `<h3>Geometry</h3>${geo}` : ""}`;
}

export function bindDiagnostics(root, viewer) {
  root.querySelector("#diag")?.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-dwire]"); if (tr) viewer.select(Number(tr.dataset.dwire));
  });
}
