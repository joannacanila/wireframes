"""The landing page's browser math (app/web/assets/spectral.js) must equal the pipeline.

Python generates inputs, Node evaluates the ES module on them, and the results are compared
with app/pipeline/vni.py and catenary.py. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess

import numpy as np
import pytest

from app.core.config import ROOT_DIR
from app.pipeline import catenary as cat
from app.pipeline import vni
from app.pipeline.crs import catenary_risk_score, risk_level

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
MODULE = (ROOT_DIR / "app" / "web" / "assets" / "spectral.js").as_uri()


def run_js(tmp_path, body: str, payload: dict) -> dict:
    (tmp_path / "in.json").write_text(json.dumps(payload))
    script = tmp_path / "run.mjs"
    script.write_text(
        f"import * as S from {json.dumps(MODULE)};\n"
        f"import {{ readFileSync }} from 'node:fs';\n"
        f"const I = JSON.parse(readFileSync({json.dumps(str(tmp_path / 'in.json'))}, 'utf8'));\n"
        f"const out = (() => {{ {body} }})();\n"
        "console.log(JSON.stringify(out));\n"
    )
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(res.stdout)


@pytest.mark.parametrize("remove_mean", [False, True])
def test_js_vni_equals_pipeline(tmp_path, remove_mean):
    rng = np.random.default_rng(4)
    g = (rng.random((64, 64)) * 40 + np.sin(np.arange(64) / 2.0)[None, :] * 60 + 100).astype(np.float64)
    out = run_js(tmp_path, "const p = S.powerSpectrum(Float64Array.from(I.g), 64, 64, I.rm);"
                           "return {raw: S.vniRawFromSpectrum(p, 64, 64, 0.15), p0: p[2080], p1: p[100]};",
                 {"g": g.ravel().tolist(), "rm": remove_mean})
    p = vni.power_spectrum(g, remove_mean)
    assert out["raw"] == pytest.approx(vni.vni_raw(g, 0.15, remove_mean), rel=1e-9)
    assert out["p0"] == pytest.approx(p.ravel()[2080], rel=1e-9)
    assert out["p1"] == pytest.approx(p.ravel()[100], rel=1e-9)


def test_js_hann_matches_scipy(tmp_path):
    from scipy.signal.windows import hann
    out = run_js(tmp_path, "return Array.from(S.hann(16));", {})
    np.testing.assert_allclose(out, hann(16, sym=False), atol=1e-15)


@pytest.mark.parametrize("a,b,xa,xb", [(900, 820, 100, 1500), (400, 500, 120, 900), (3000, 200, 0, 1400)])
def test_js_chord_sag_equals_pipeline(tmp_path, a, b, xa, xb):
    out = run_js(tmp_path, "return S.chordSag(I.a, I.b, 0, I.xa, I.xb);", {"a": a, "b": b, "xa": xa, "xb": xb})
    length, delta, xs, t = cat._chord_sag(a, b, 0.0, xa, xb)
    assert out["L"] == pytest.approx(length, rel=1e-12)
    assert out["delta"] == pytest.approx(delta, rel=1e-9, abs=1e-9)
    assert out["xs"] == pytest.approx(xs, rel=1e-12)


def test_js_sag_inversion_and_crs(tmp_path):
    out = run_js(tmp_path, "const a = S.aForSag(1000, 40); return {a, d: a*(Math.cosh(500/a)-1),"
                           "crs: S.crs(0.02, 0.04), lv: [S.level(2.99), S.level(3), S.level(6), S.level(6.01)]};", {})
    assert out["d"] == pytest.approx(40, rel=1e-9)
    assert out["crs"] == pytest.approx(catenary_risk_score(0.02, 0.04))
    assert out["lv"] == [risk_level(2.99), risk_level(3.0), risk_level(6.0), risk_level(6.01)]
