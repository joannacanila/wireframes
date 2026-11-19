/** S18 review panel: the administrative decision beside the machine screening, never mixed into it.
 *  Opening an inspection records the reviewer (NOT_YET_REVIEWED -> PARTIALLY_REVIEWED, server side, audited);
 *  finalizing stores a separate decision (REVIEW_COMPLETE); amending supersedes it with history kept; reopening needs a
 *  reason. Options, critical-hazard types and clearance references come from the server's configuration. */
import { api, esc } from "./lib.js?v=2.23";
import { toast } from "./toast.js?v=2.23";
import { reviewChip } from "./result-view.js?v=2.36";

const OTHER_MIN = 5; // the server enforces the same minimum (review_flow.OTHER_MIN)
const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const when = (iso) => (iso ? new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" }) : "");

function historyHtml(v) {
  if (!v.history.length) return "";
  return `<details class="rvw-hist"><summary>Decision history <span class="num">${v.history.length}</span></summary><ol>
    ${[...v.history].reverse().map((d) => `<li class="${d.superseded_at ? "old" : "cur"}"><p><b>${esc(d.label)}</b> · ${esc(d.by)}${d.source === "legacy_review" ? ` <span class="muted">(earlier review record)</span>` : ""}</p>
      <time datetime="${esc(d.at)}">${esc(when(d.at))}</time>${d.superseded_at ? ` <span class="muted">superseded ${esc(when(d.superseded_at))}</span>` : ""}
      ${d.rationale ? `<blockquote>${esc(d.rationale)}</blockquote>` : ""}${d.critical?.length ? `<p class="crit">${d.critical.map((c) => esc(c.label)).join("; ")}</p>` : ""}</li>`).join("")}</ol></details>`;
}

function formHtml(v, opts, rubric, cur) {
  const active = opts.filter((o) => o.active !== false);
  const refs = rubric.clearance_references || [];
  const crit = Object.entries(rubric.critical_type_labels || {}).filter(([k]) => (rubric.critical_types || []).includes(k));
  const has = (t) => (cur?.critical || []).some((c) => c.type === t);
  return `<form class="rvw-f" id="rvw-f">
    <fieldset><legend>Administrative decision</legend>
      ${active.map((o) => `<label class="opt"><input type="radio" name="decision" value="${esc(o.code)}" ${cur?.code === o.code ? "checked" : ""} required><span><b>${esc(o.label)}</b></span></label>`).join("")}
      <label class="field rvw-other" ${cur?.code === "other" ? "" : "hidden"}><span>Describe the decision <i>required</i></span>
        <input name="other_text" maxlength="110" autocomplete="off" value="${esc(cur?.code === "other" ? (cur.label || "").replace(/^Other: /, "") : "")}" placeholder="e.g. Refer to the city engineering office for a joint inspection" aria-describedby="rvw-other-err">
        <small class="hint">What was decided, in a few words. It is saved as the decision and in the audit history.</small>
        <small class="form-error" id="rvw-other-err" hidden>Describe the decision (at least ${OTHER_MIN} characters) before finalizing.</small></label>
    </fieldset>
    <label class="field"><span>Rationale <i>required</i></span><textarea name="rationale" rows="3" maxlength="4000" required placeholder="What you checked and why you decided this">${esc(cur?.rationale || "")}</textarea></label>
    <label class="field"><span>Follow-up <i>optional</i></span><input name="follow_up" maxlength="2000" value="${esc(cur?.follow_up || "")}" placeholder="Who does what next"></label>
    <label class="field"><span>Field observations <i>optional</i></span><textarea name="observations" rows="2" maxlength="4000">${esc(cur?.observations || "")}</textarea></label>
    <details class="rvw-crit" ${cur?.critical?.length ? "open" : ""}><summary>Confirmed critical hazard <span class="muted">overrides the risk level</span></summary>
      <p class="hint">Record only what you confirmed. A photograph alone cannot confirm low clearance.</p>
      ${crit.map(([k, l]) => `<label class="chk"><input type="checkbox" name="crit" value="${esc(k)}" ${has(k) ? "checked" : ""} ${k === "low_clearance_measured" && !refs.length ? "disabled" : ""}> ${esc(l)}${k === "low_clearance_measured" && !refs.length ? ` <span class="muted">(no documented clearance reference is configured)</span>` : ""}</label>`).join("")}
      ${refs.length ? `<div class="rvw-meas" hidden><label class="field"><span>Clearance reference</span><select name="ref">${refs.map((r) => `<option value="${esc(r.id)}">${esc(r.label || r.id)} (${esc(r.min_height_m)} m)</option>`).join("")}</select></label>
        <label class="field"><span>Measured height (m)</span><input name="height_m" type="number" step="0.01" min="0" max="30"></label>
        <label class="field"><span>Measured with</span><input name="method" maxlength="120" placeholder="e.g. measuring pole"></label></div>` : ""}
    </details>
    <div class="rvw-acts"><button class="btn primary" type="submit">${cur ? "Save amended decision" : "Finalize review"}</button>${cur ? `<button class="btn ghost" type="button" data-cancel>Cancel</button>` : ""}</div>
    <p class="hint">The machine screening stays as computed. Your decision is stored separately with your name and time, together with what the screening showed when you decided.</p>
  </form>`;
}

export async function mountReviewPanel(box, id, { onChange = () => {}, start = true } = {}) {
  let v, opts, rubric, editing = false;
  const draw = () => {
    const cur = v.decision;
    const done = v.status === "REVIEW_COMPLETE";
    box.innerHTML = `<header class="rvw-h"><h2>Administrative review</h2>${reviewChip(v.status, v.label)}</header>
      ${v.reviewers.length ? `<p class="rvw-who">Reviewers: ${v.reviewers.map((x) => esc(x.name || x)).join(", ")}${v.started_at ? ` · opened ${esc(when(v.started_at))}` : ""}</p>` : ""}
      ${done && cur && !editing ? `<div class="rvw-cur"><p class="rvw-k">Decision</p><p class="rvw-dec">${esc(cur.label)}</p>
          ${cur.rationale ? `<p>${esc(cur.rationale)}</p>` : ""}${cur.follow_up ? `<p><b>Follow-up:</b> ${esc(cur.follow_up)}</p>` : ""}
          ${cur.critical?.length ? `<p class="crit"><b>Critical:</b> ${cur.critical.map((c) => esc(c.label)).join("; ")}</p>` : ""}
          <p class="muted">${esc(cur.by)} · ${esc(when(cur.at))}</p>
          ${v.can_decide !== false ? `<div class="rvw-acts"><button class="btn" type="button" data-amend>Amend decision</button><button class="btn ghost" type="button" data-reopen>Reopen review</button></div>` : ""}</div>`
        : v.can_decide === false ? `<p class="muted">The decision can be recorded when processing has finished.</p>` : formHtml(v, opts, rubric, editing ? cur : null)}
      ${historyHtml(v)}`;
    const f = box.querySelector("#rvw-f");
    if (!f) return;
    const meas = f.querySelector(".rvw-meas");
    const other = f.querySelector(".rvw-other"), otherIn = f.other_text, otherErr = f.querySelector("#rvw-other-err");
    const otherOk = () => { const t = otherIn.value.trim().replace(/\s+/g, " "); return t.length >= OTHER_MIN && /[\p{L}\p{N}]/u.test(t); };
    const syncMeas = () => {
      if (meas) meas.hidden = !f.querySelector('[name=crit][value="low_clearance_measured"]')?.checked;
      const isOther = f.decision.value === "other";
      other.hidden = !isOther; otherIn.required = isOther;
      if (!isOther) { otherErr.hidden = true; otherIn.removeAttribute("aria-invalid"); }
    };
    f.addEventListener("change", syncMeas); syncMeas();
    otherIn.addEventListener("input", () => { if (!otherErr.hidden && otherOk()) { otherErr.hidden = true; otherIn.removeAttribute("aria-invalid"); } });
    f.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (f.decision.value === "other" && !otherOk()) { otherErr.hidden = false; otherIn.setAttribute("aria-invalid", "true"); otherIn.focus(); return; }
      const critical = [...f.querySelectorAll("[name=crit]:checked")].map((c) => (c.value === "low_clearance_measured"
        ? { type: c.value, measurement: { reference_id: f.ref?.value, height_m: f.height_m?.value, method: f.method?.value } } : { type: c.value }));
      const b = f.querySelector("[type=submit]"); b.disabled = true;
      try {
        v = { ...v, ...(await api(`/api/admin/inspections/${id}/decision`, json("POST", { decision: f.decision.value, rationale: f.rationale.value,
          follow_up: f.follow_up.value, observations: f.observations.value, critical,
          other_text: f.decision.value === "other" ? otherIn.value.trim() : "" }))) };
        toast.success(editing ? "Decision amended" : "Review complete"); editing = false; draw(); onChange(v);
      } catch (ex) { toast.error("Decision not saved", { description: ex.message }); b.disabled = false; }
    });
  };
  box.addEventListener("click", async (e) => {
    if (e.target.closest("[data-amend]")) { editing = true; draw(); return; }
    if (e.target.closest("[data-cancel]")) { editing = false; draw(); return; }
    if (e.target.closest("[data-reopen]")) {
      const reason = prompt("Why are you reopening this review? (recorded in the audit trail)");
      if (!reason || !reason.trim()) return;
      try { v = { ...v, ...(await api(`/api/admin/inspections/${id}/review/reopen`, json("POST", { reason }))) }; toast.success("Review reopened"); draw(); onChange(v); }
      catch (ex) { toast.error("Not reopened", { description: ex.message }); }
    }
  });
  try {
    const g = await api(`/api/admin/inspections/${id}/review`);
    ({ options: opts, rubric } = g); v = g;
    if (start && g.can_decide) {
      const s = await api(`/api/admin/inspections/${id}/review/start`, { method: "POST" });
      v = { ...v, ...s };
      if (s.recorded) onChange(v);
    }
  } catch (ex) { box.innerHTML = `<p class="empty">The review could not be loaded: ${esc(ex.message)}</p>`; return null; }
  draw();
  return { get view() { return v; } };
}
