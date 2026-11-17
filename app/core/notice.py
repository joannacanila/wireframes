"""v2.19: the approved decision-support notice, in one place for every server-side output (PDF reports, CSV
exports, the assistant). The web pages use the identical text from app/web/assets/notice.js; a test keeps the two
copies equal. Do not reword: the wording is the project owner's (S4 brief, 18 Nov 2026, section 7)."""

TITLE = "Important Notice"
TEXT = (
    "WIREFRAME is a decision-support tool intended for preliminary visual screening of overhead conductor conditions. "
    "Its automated outputs and image-derived measurements may be inaccurate or incomplete due to image quality, "
    "visibility, perspective, classification errors, environmental conditions, and other limitations. Results do "
    "not constitute a certified engineering inspection, confirmation of structural or electrical safety, or proof "
    "of regulatory compliance. All findings should be reviewed by a qualified professional. Results marked as "
    "inconclusive or not assessed require further information, verification, or an appropriate field inspection."
)
# one line for page footers and CSV headers, so no printed page or exported file is separated from the notice
SHORT = ("Preliminary visual screening, not a certified inspection or proof of compliance. Review with a qualified "
         "professional.")
