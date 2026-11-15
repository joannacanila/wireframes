"""v2.12: backbone sidecars, ensemble specs and probability fusion (the fusion is opt-in, not shipped)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline import wirenet as wn

torch = pytest.importorskip("torch")


def test_arch_defaults_to_resnet34_without_sidecar(tmp_path):
    assert wn.model_arch(tmp_path / "plain.pt") == {"encoder": "resnet34", "arch": "Unet"}


def test_arch_read_from_sidecar(tmp_path):
    (tmp_path / "m.arch.json").write_text(json.dumps({"encoder": "mit_b1", "arch": "Unet", "classes": 5}))
    assert wn.model_arch(tmp_path / "m.pt") == {"encoder": "mit_b1", "arch": "Unet"}


def _spec(tmp_path: Path, members) -> Path:
    (tmp_path / "models").mkdir(exist_ok=True)
    p = tmp_path / "models" / "ens.json"
    p.write_text(json.dumps({"members": members}))
    return p


def test_members_normalised_and_zero_weights_dropped(tmp_path):
    spec = _spec(tmp_path, [{"weights": "models/a.pt", "weight": 1}, {"weights": "models/b.pt", "weight": 3},
                            {"weights": "models/c.pt", "weight": 0}])
    m = wn.ensemble_members(spec)
    assert [p.name for p, _ in m] == ["a.pt", "b.pt"]
    assert [w for _, w in m] == pytest.approx([0.25, 0.75])
    assert m[0][0] == tmp_path / "models" / "a.pt"  # relative paths resolve from the repo root


@pytest.mark.parametrize("members", [[], [{"weights": "models/a.pt", "weight": -1}],
                                     [{"weights": "models/a.pt", "weight": 0}]])
def test_invalid_specs_rejected(tmp_path, members):
    with pytest.raises(ValueError):
        wn.ensemble_members(_spec(tmp_path, members))


class _Const(torch.nn.Module):
    def __init__(self, logits):
        super().__init__()
        self.logits = torch.tensor(logits, dtype=torch.float32)

    def forward(self, x):
        b, _, h, w = x.shape
        return self.logits.view(1, -1, 1, 1).expand(b, -1, h, w)


def test_fused_softmax_is_weighted_mean_of_member_probabilities():
    a, b = _Const([2.0, 0.0, -1.0, 0.5, 0.0]), _Const([-1.0, 3.0, 0.0, 0.0, 1.0])
    fused = wn._fused_module([(a, 0.25), (b, 0.75)]).eval()
    x = torch.zeros(1, 3, 4, 4)
    got = fused(x).softmax(1)
    want = 0.25 * a(x).softmax(1) + 0.75 * b(x).softmax(1)
    assert torch.allclose(got, want, atol=1e-6)


def test_fused_module_works_with_predict_probs():
    a, b = _Const([0.0, 1.0, 0.0, 0.0, 0.0]), _Const([0.0, 0.0, 1.0, 0.0, 0.0])
    fused = wn._fused_module([(a, 0.5), (b, 0.5)]).eval()
    p = wn.predict_probs(fused, np.zeros((40, 50, 3), np.uint8), "cpu")
    assert p.shape == (5, 40, 50)
    assert np.allclose(p.sum(0), 1.0, atol=1e-5)
    assert np.allclose(p[1], p[2], atol=1e-6)


def test_shipped_config_still_single_r34():
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    assert cfg.segmentation.wirenet_weights == "models/wirenet-r34.pt"
    assert cfg.segmentation.wirenet_tta == "flip"


def test_repo_ensemble_spec_is_valid():
    spec = ROOT_DIR / "models" / "v212_ensemble.json"
    if not spec.exists():
        pytest.skip("ensemble spec not present")
    m = wn.ensemble_members(spec)
    assert [p.name for p, _ in m] == ["wirenet-r34.pt", "v212_mitb1.pt", "v212_convnext_t.pt"]
    assert [w for _, w in m] == pytest.approx([0.25, 0.5, 0.25])
