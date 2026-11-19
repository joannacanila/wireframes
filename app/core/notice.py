"""S17 (Part 5.4): the ONE canonical Important Notice, verbatim, for every output: the results page and every web page
(app/web/assets/notice.js holds the identical string; a test keeps them equal), PDF reports, CSV exports and the
assistant. Do not reword and do not add a second general disclaimer anywhere. It replaces the v2.19 notice and the S14
landing variant, which said the same thing in competing words."""

TITLE = "Important Notice"
TEXT = (
    "WIREFRAME is an image-based decision-support tool designed to help document visible pole and wire conditions. "
    "Its results are preliminary and may contain errors or limitations. Findings should be reviewed by qualified "
    "professionals and must not replace an on-site inspection or applicable safety assessment."
)
# page footers point to the notice rather than restating it in other words
SHORT = "Preliminary image-based screening. See the Important Notice."
