"""v2.19: the approved decision-support notice, in one place for every server-side output (PDF reports, CSV
exports, the assistant). The web pages use the identical text from app/web/assets/notice.js; a test keeps the two
copies equal. Do not reword: the wording is approved by the project owner."""

TITLE = "Important Notice"
TEXT = (
    "WIREFRAME is designed to support the screening and assessment of overhead utility wires. Its results are "
    "generated using automated computer-vision models and may contain errors, omissions, or inaccurate "
    "classifications. Findings should not be considered a definitive safety assessment or a substitute for an "
    "actual inspection. Always have results verified by a qualified professional before making engineering, "
    "maintenance, or safety-related decisions."
)
# one line for page footers and CSV headers, so no printed page or exported file is separated from the notice
SHORT = "Automated screening result, not a safety assessment. Verify with a qualified professional."
