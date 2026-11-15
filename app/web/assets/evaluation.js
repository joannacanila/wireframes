// Evaluation view (Sections 3.10-3.11): Table 3.10 targets against the latest evaluation runs.
// Values come only from /api/evaluation/metrics, which only reports what `ml.evaluate` computed.

import { toast } from "./toast.js";

const NAMES = {
  map50: "Mask mAP@50", precision_per_class: "Precision · per class", recall_per_class: "Recall · per class",
  f1_per_class: "F1-score · per class", h1_pearson_r: "Pearson r · VNI vs clutter (H1)",
  h3_rmse: "RMSE · CRS vs severity (H3)", h4_spearman_rho: "Spearman ρ · MSPI vs reference (H4)",
};
const SECTION_LABEL = {
  segmentation_test: "Segmentation · test set", segmentation_val: "Segmentation · validation set",
  reference: "Reference agreement · H1, H3, H4", geometry_synthetic: "Geometric layer · synthetic benchmark",
};
const f = (v, d = 3) => (v == null ? "—" : Number(v).toFixed(d));
const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(1)}%`);

export function mountEvaluation(view, { api, esc }) {
  view.innerHTML = `<section class="evalview"><div class="ev-wrap">
    <header class="ev-head"><p class="eyebrow">Evaluation · Sections 3.10–3.11</p><h1 class="title">Targets and results</h1>
      <p class="hint">Targets were fixed before any experiment. A result appears only after its evaluation run; nothing here is estimated.</p></header>
    <div id="ev-body"><div class="scan" style="margin:40px auto"></div></div></div></section>`;
  const body = view.querySelector("#ev-body");
  api("/api/evaluation/metrics").then((m) => {
    const seg = m.sections.segmentation_test, ref = m.sections.reference;
    const result = (k) => {
      if (k === "map50") return seg.status === "not_run" ? null : seg.map50;
      if (k.endsWith("_per_class")) return null; // shown in the class table
      const h = { h1_pearson_r: ref.h1?.pearson_r, h3_rmse: ref.h3?.rmse, h4_spearman_rho: ref.h4?.spearman_rho }[k];
      return ref.status === "not_run" ? null : h?.value ?? null;
    };
    const pass = (k, v) => (v == null ? "" : (m.targets[k].op === ">=" ? v >= m.targets[k].value : v <= m.targets[k].value) ? "pass" : "fail");
    const rows = Object.entries(m.targets).map(([k, t]) => {
      const v = result(k), p = pass(k, v);
      return `<tr><td>${NAMES[k]}</td><td class="mono">${t.op === ">=" ? "≥" : "≤"} ${t.value.toFixed(2)}</td><td>${esc(t.applies_to)}</td>
        <td class="mono ${p}">${v == null ? '<span class="muted">not yet measured</span>' : f(v)}${p ? ` <span class="chip ${p === "pass" ? "low" : "high"}">${p === "pass" ? "meets" : "below"}</span>` : ""}</td></tr>`;
    }).join("");
    const perClass = seg.status === "not_run" ? "" : `<h2 class="ev-h2">Per class · test set · conf ${f(seg.conf_threshold, 3)} · IoU 0.50</h2>
      <table class="ev-table"><thead><tr><th>Class</th><th>Precision</th><th>Recall</th><th>F1</th><th>AP50</th><th>AP50–95</th><th>Mask IoU</th></tr></thead><tbody>
      ${Object.entries(seg.per_class).map(([c, v]) => `<tr><td>${c.replace(/_/g, " ")}</td><td class="mono">${f(v.precision)}</td><td class="mono">${f(v.recall)}</td>
        <td class="mono">${f(v.f1)}</td><td class="mono">${f(v.ap50)}</td><td class="mono">${f(v.ap50_95)}</td><td class="mono">${f(v.mean_mask_iou)}</td></tr>`).join("")}</tbody></table>`;
    const geo = m.sections.geometry_synthetic;
    const geoBlock = geo.status === "not_run" ? "" : `<h2 class="ev-h2">Geometric layer · synthetic benchmark <span class="chip plain">supplementary</span></h2>
      <p class="hint">${esc(geo.description)} ${geo.wires} wires, ${geo.fit_failures} fit failures.</p>
      <div class="kpis ev-kpis"><div class="kpi"><div class="k">Curve RMSE · median</div><div class="v">${f(geo.curve_rmse_px.median, 2)}<small>px</small></div></div>
        <div class="kpi"><div class="k">Sag-ratio error · median</div><div class="v">${pct(geo.relative_sag_ratio_error.median)}</div></div>
        <div class="kpi"><div class="k">Sag-ratio error · p95</div><div class="v">${pct(geo.relative_sag_ratio_error.p95)}</div></div></div>`;
    const sections = Object.entries(m.sections).map(([k, s]) => `<li><span class="chip ${s.status === "complete" ? "low" : s.status === "stale" ? "medium" : "plain"}">${s.status.replace("_", " ")}</span>
      <b>${SECTION_LABEL[k]}</b>${s.status === "not_run" ? `<code>${esc(s.command)}</code><button class="icon-btn" data-copy="${esc(s.command)}" aria-label="Copy command"><svg viewBox="0 0 24 24"><path d="M8 8h11v11H8zM5 16V5h11"/></svg></button>`
      : `<span class="muted mono">${new Date(s.generated_at).toLocaleString()}${s.status === "stale" ? " · parameters changed since" : ""}</span>`}</li>`).join("");
    body.innerHTML = `<table class="ev-table"><thead><tr><th>Metric</th><th>Target</th><th>Applies to</th><th>Result</th></tr></thead><tbody>${rows}</tbody></table>
      ${perClass}${geoBlock}<h2 class="ev-h2">Evaluation runs</h2><ul class="ev-runs">${sections}</ul>
      <ul class="flags">${m.notes.map((n) => `<li class="info">${esc(n)}</li>`).join("")}</ul>`;
    body.addEventListener("click", (e) => {
      const b = e.target.closest("[data-copy]"); if (!b) return;
      navigator.clipboard?.writeText(b.dataset.copy).then(() => toast.success("Command copied"), () => toast.error("Copy failed"));
    });
  }).catch((e) => { body.innerHTML = `<p class="hint">Could not load the evaluation: ${esc(e.message)}</p>`; });
  return () => {};
}
