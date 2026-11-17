"""v2.17: P(wire) >= tau strand masks and the strand type head (one type per physical strand)."""

import numpy as np

from app.core.config import CLASSES
from app.pipeline import strand_head as sh
from app.pipeline.wire_instances import Strand, strand_spans

PT, TC = CLASSES.index("power-distribution-lines") + 1, CLASSES.index("telecom-cable") + 1
WIRE_IDS = [CLASSES.index(c) + 1 for c in CLASSES if c != "Vegetation"]
PARAMS = {"min_px": 10, "min_len": 10}


def _probs(shape=(128, 256), split=False):
    p = np.zeros((5, *shape), np.float32)
    p[0] = 1.0
    if split:  # a faint cable whose wire probability is split between power and telecom
        p[0, 60:63, 20:230], p[PT, 60:63, 20:230], p[TC, 60:63, 20:230] = 0.4, 0.3, 0.3
    else:
        p[0, 60:63, 20:230], p[TC, 60:63, 20:230] = 0.1, 0.9
    return p


def test_tau_mask_recovers_a_wire_whose_probability_is_split_between_classes():
    p = _probs(split=True)
    assert strand_spans(p, WIRE_IDS, PARAMS) == []  # argmax: background wins every pixel
    s = strand_spans(p, WIRE_IDS, PARAMS, tau=0.4)
    assert len(s) == 1 and s[0].mask.sum() > 300


def test_features_are_scale_normalised():
    p = _probs()
    s = strand_spans(p, WIRE_IDS, PARAMS)
    rgb = np.full((*p.shape[1:], 3), 200, np.uint8)
    h1, _ = sh.strand_features(s, p, rgb, None)
    h2, _ = sh.strand_features(s, p, rgb, None, scale=0.5)
    assert h1.shape == (1, 24) and h2[0, 8] < h1[0, 8] and np.isclose(h2[0, 12], h1[0, 12] * 0.5)


class _FakeHead:
    def __init__(self, p):
        self.p = p

    def p_telecom(self, hand, dino):
        return np.full(len(hand), self.p, np.float32)


def _strand(share):
    m = np.zeros((128, 256), bool)
    m[60:63, 20:230] = True
    return Strand(max(share, key=share.get), m, 0.9, dict(share), "confident")


def _head(p, min_conf=0.6):
    h = sh.StrandHead("unused.pt", min_conf=min_conf)
    h._head, h._dino = _FakeHead(p), type("D", (), {"grid": staticmethod(lambda rgb: np.zeros((72, 72, 384), np.float32))})()
    return h


def test_head_sets_one_type_per_strand_and_abstains_when_unsure():
    rgb = np.full((128, 256, 3), 200, np.uint8)
    probs = _probs()
    s = [_strand({"power-distribution-lines": 0.55, "telecom-cable": 0.45, "abandoned-wire": 0.0})]
    assert _head(0.9).classify(s, probs, rgb)
    assert s[0].wire_class == "telecom-cable" and s[0].class_status == "confident"
    assert s[0].class_share["head_p_telecom"] == 0.9
    s = [_strand({"power-distribution-lines": 0.9, "telecom-cable": 0.1, "abandoned-wire": 0.0})]
    _head(0.45).classify(s, probs, rgb)
    assert s[0].wire_class == "power-distribution-lines" and s[0].class_status == "uncertain"


def test_head_leaves_abandoned_wire_strands_to_the_pixel_rule():
    s = [_strand({"power-distribution-lines": 0.1, "telecom-cable": 0.1, "abandoned-wire": 0.8})]
    _head(0.99).classify(s, _probs(), np.full((128, 256, 3), 200, np.uint8))
    assert s[0].wire_class == "abandoned-wire"


def test_missing_head_weights_fail_soft(tmp_path):
    h = sh.StrandHead(tmp_path / "missing.pt")
    assert not h.classify([_strand({"telecom-cable": 1.0})], _probs(), np.zeros((128, 256, 3), np.uint8))
    assert "unavailable" in h.error
