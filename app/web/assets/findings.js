// v2.19: the plain-language reading of a screening result, shared by the Field Screener and the admin inspection
// sheet so both say the same thing. Moved unchanged from field.js (v2.15); never presents a screening as a
// safety certification, and a wire that could not be assessed is never shown as low risk.

import { esc, fmt, pct } from "./lib.js?v=2.23";

export const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const group = (w) => w.level || "none";
export const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
export const list = (xs) => (xs.length <= 1 ? String(xs[0] ?? "") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);
export const pc = (x, d = 1) => `${(x * 100).toFixed(d)}%`;
export function sagFactors(r) { return r.hazard?.factors?.sag || {}; }
export const SECTION = (ids) => `${ids.length === 1 ? "section" : "sections"} ${list(ids.map((i) => `<button type="button" class="fx-ref" data-goto="${i}">${i}</button>`))}`;
// v2.21: ONE wire-severity vocabulary for the result page, the admin inspection page and the PDF
// (app/services/plain.py SEVERITY). It names the stored wire level; it never recomputes it.
export const SEVERITY = { high: "Critical", medium: "Minor sag", low: "Within reference", none: "Not assessed" };
export const CONDITION = SEVERITY;
export const spanId = (i) => `SP-${String(i).padStart(3, "0")}`;
/** Why a wire was not assessed, in a few words, for the collapsed summary ("40 overlapping, 30 too distant"). */
export function skipReason(w) {
  if (w.status === "service_drop") return "drop wire, not checked";
  const r = { chord_below_precision_gate: "too small or distant", too_short: "too small or distant", residual_too_large: "overlapping",
    non_positive_a: "overlapping", empty_mask: "too faint", endpoint_unavailable: "endpoint off the photo",
    class_uncertain: "uncertain wire type", no_reference_for_class: "power line, no sag reference",
    no_validated_reference: "measured, no validated sag reference", withdrawn_reference: "measured, earlier reference withdrawn" }[w.reason_code];
  if (r) return r;
  return (w.flags || []).includes("truncated") ? "outside the photo" : "unclear in the photo";
}
// S4 (register D-02..D-05): reasons for which the sag ratio is a measurement only (no validated reference exists)
export const MEASURED_ONLY = new Set(["no_validated_reference", "withdrawn_reference", "no_reference_for_class"]);
/** The image-derived sag ratio with its 1 px resolution ("2.1% ±0.3"); a measurement, never a severity. */
export const measuredText = (w) => `${pc(w.sag_ratio)}${w.sag_ratio_resolution ? ` ±${(w.sag_ratio_resolution * 100).toFixed(1)}` : ""}`
  // S5: a ratio below its own 1 px resolution is "straight within resolution", not a small sag
  + (w.sag_ratio_resolution && w.sag_ratio < w.sag_ratio_resolution ? " (within resolution)" : "");
export const SCORE_HELP = "Sag score, 0 to 10: how far the wire hangs compared with the sag allowed for its type. "
  + "10 means it hangs as low as the allowance or lower. Low below 3, moderate 3 to 6, high above 6.";

/** One plain sentence on what the photo shows for this wire section. Measurements only; never a guess. */
export function finding(w, short = false) {
  const veg = w.veg_clearance;
  // S4 (register D-15): a pixel gap is an image observation, not a clearance; no pixel distance is called "close"
  const vegTxt = veg && veg.gap_px === 0 ? (short ? "; vegetation overlaps in photo" : "; vegetation overlaps it in the photo (check clearance on site)") : "";
  const edge = (w.flags || []).includes("truncated") ? (short ? "; runs past photo edge" : "; it runs past the edge of the photo, so the full span may not be shown") : "";
  if (short && w.status === "scored") {
    return `${{ high: "Hangs low", medium: "Some sag", low: "Normal sag" }[w.level]} (${pc(w.sag_ratio)})${vegTxt}${edge}`;
  }
  // S6: a measured wire is described by its measurement in the short (table) form too; checked before the
  // not-measured reasons, which previously caught it first and read "Could not be measured"
  if (w.status === "not_assessed" && w.sag_ratio != null && w.reason_code === "endpoint_unavailable") {
    return `${short ? "Visible part" : "Not assessed (runs off the photo); visible part measured"}: image-derived sag ratio ${measuredText(w)}` + vegTxt;
  }
  if (w.status === "not_assessed" && w.sag_ratio != null && (MEASURED_ONLY.has(w.reason_code) || w.reason_code === "class_uncertain")) {
    return `${short ? "Measured" : "Measured, not assessed"}: image-derived sag ratio ${measuredText(w)}${short ? "" : "; no validated sag reference"}` + vegTxt + edge;
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
export function limitations(r) {
  const out = [];
  const ws = r.wires;
  const short = Math.min(r.image.width, r.image.height);
  const ids = (pred) => ws.filter(pred).map((w) => w.index).sort((a, b) => a - b);
  const small = ids((w) => ["chord_below_precision_gate", "too_short"].includes(w.reason_code));
  const overlap = ids((w) => ["residual_too_large", "non_positive_a"].includes(w.reason_code));
  // S5: a wire measured but not assessed for lack of a reference is not a photo limitation
  const other = ids((w) => w.status !== "scored" && w.status !== "service_drop" && !MEASURED_ONLY.has(w.reason_code) && !(w.reason_code === "class_uncertain" && w.sag_ratio != null) && !["chord_below_precision_gate", "too_short", "residual_too_large", "non_positive_a", "endpoint_unavailable"].includes(w.reason_code));
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
export function assessment(r) {
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
    // v2.23: INCONCLUSIVE also follows from low coverage or a sagging partial span; state the system's own reason
    const why = (hz?.reasons || []).find((x) => x.startsWith("INCONCLUSIVE"));
    const d = r.diagnosis;
    if (d?.why && d.counts.measured + d.counts.measured_partial > 0) { // S5: what was measured, and why no verdict
      return { cls: "none", pill: "Assessment incomplete", title: "The assessment of this pole is incomplete.",
        lines: [`${why && /withdrawn/.test(why) ? why.replace(/^INCONCLUSIVE - RETAKE PHOTO:\s*/, "") + " " : ""}${d.why}`], coverage };
    }
    return { cls: "none", pill: "Assessment incomplete", title: "The assessment of this pole is incomplete.",
      lines: [`${r.quality?.reason ? `${limitations(r).find((l) => l.key === "quality")?.text || ""} ` : ""}${why ? why.replace(/^INCONCLUSIVE - RETAKE PHOTO:\s*/, "").replace(/^./, (c) => c.toUpperCase()) + ". " : ""}This is not a low-risk result; a new photo or a field check is needed.`.trim()], coverage };
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
export function recommendations(r, a, lims) {
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
  // S5: a retake is advised only when it can help (wires not measured or only partly measured), never for a missing reference
  if (a.cls === "none" && r.diagnosis && !r.diagnosis.retake_helps) out.unshift("A new photo will not complete this assessment: no validated sag reference exists. Have a qualified person inspect the pole for any concern.");
  else if (a.cls === "none") out.unshift(`Retake the photo. ${unassessed.map((l) => l.help).filter(Boolean)[0] || "Frame the pole and the full wires on both sides, in good light."}`);
  else if (unassessed.length) out.push(`Some sections could not be assessed. A clearer photo may help: ${unassessed.map((l) => l.help).filter(Boolean)[0]?.replace(/^./, (c) => c.toLowerCase()) || "frame the full wires in good light."}`);
  if (high.length || med.length || vegPower.length) out.push("Download the PDF report to share with the inspector.");
  out.push("Do not touch, climb toward or try to move any overhead wire. Treat every wire as live.");
  return out;
}

