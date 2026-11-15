"""Topological loose-end detector (v2.8)."""

import cv2
import numpy as np

from app.pipeline.loose_ends import loose_ends


def _line(shape, p, q, t=2):
    m = np.zeros(shape, np.uint8)
    cv2.line(m, p, q, 1, t)
    return m.astype(bool)


def test_a_dangling_end_is_found_and_scored_as_hanging():
    shape = (400, 600)
    span = _line(shape, (5, 100), (595, 120))                 # border to border: no loose end
    drop = _line(shape, (300, 112), (310, 300))               # hangs from the span, free end mid-air
    ends = loose_ends([span, drop])
    assert len(ends) == 1 and ends[0].span == 1
    assert ends[0].y > 250 and ends[0].downward > 0.9 and ends[0].score > 0.9


def test_attachments_and_frame_exits_are_not_loose_ends():
    shape = (400, 600)
    # three wires converging on one attachment point (a pole) in mid-image
    spans = [_line(shape, (5, 50 + 40 * k), (300, 200)) for k in range(3)]
    assert loose_ends(spans) == []
    # a wire that leaves the frame on both sides
    assert loose_ends([_line(shape, (0, 10), (599, 390))]) == []


def test_content_mask_excludes_letterbox_padding():
    shape = (400, 600)
    content = np.zeros(shape, bool)
    content[100:300] = True                                  # photo band; padding above and below
    span = _line(shape, (50, 100), (550, 299))               # both ends on the content edge
    assert loose_ends([span], content=content) == []
