"""v2.13 viewer overlay opacity (app/web/assets/viewer.js, OverlayViewer.setOpacity / _paint).

Node evaluates the ES module with a recording 2D context: opacity is clamped to [0, 1], a value below 1 repaints the
photo over the overlays with alpha 1 - opacity, and nothing extra is drawn at full opacity, with overlays switched off
(the "Original" view) or before the photo has loaded. Skipped when Node is not installed.
"""

import json
import shutil
import subprocess

import pytest

from app.core.config import ROOT_DIR

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")
MODULE = (ROOT_DIR / "app" / "web" / "assets" / "viewer.js").as_uri()

HARNESS = """
import { OverlayViewer } from %(module)s;
const P = OverlayViewer.prototype;
function fake(opacity, { overlays = true, image = true } = {}) {
  const log = [];
  const ctx = { globalAlpha: 1, setTransform() {}, save() {}, restore() { this.globalAlpha = 1; },
    drawImage(img) { log.push({ img: img.name, alpha: this.globalAlpha }); } };
  const v = Object.create(P);
  Object.assign(v, { ctx, view: { s: 2, x: 0, y: 0 }, dpr: 1, layers: { overlays },
    image: image ? { name: "photo" } : null, opacity, _paintLayers() { log.push("layers"); } });
  return { v, log };
}
const out = {};
// clamping, and draw() requested on every change
for (const a of [-0.5, 0, 0.35, 1, 2.5]) {
  let drawn = 0;
  const v = Object.create(P); v.draw = () => { drawn++; };
  v.setOpacity(a);
  out[`clamp_${a}`] = { opacity: v.opacity, drawn };
}
const paint = (o, opts) => { const { v, log } = fake(o, opts); v._paint(); return log; };
out.full = paint(1);
out.unset = paint(undefined);
out.half = paint(0.25);
out.zero = paint(0);
out.photo_view = paint(0.25, { overlays: false });
out.no_image = paint(0.25, { image: false });
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def js(tmp_path_factory):
    script = tmp_path_factory.mktemp("viewer") / "run.mjs"
    script.write_text(HARNESS % {"module": json.dumps(MODULE)})
    res = subprocess.run([NODE, str(script)], capture_output=True, text=True, timeout=60, check=True)
    return json.loads(res.stdout)


@pytest.mark.parametrize("given,expected", [(-0.5, 0), (0, 0), (0.35, 0.35), (1, 1), (2.5, 1)])
def test_set_opacity_clamps_and_redraws(js, given, expected):
    assert js[f"clamp_{given}"] == {"opacity": expected, "drawn": 1}


def test_full_or_default_opacity_draws_layers_only(js):
    assert js["full"] == ["layers"]
    assert js["unset"] == ["layers"]  # a fresh viewer (opacity never set) behaves as opacity 1


def test_partial_opacity_fades_overlays_toward_the_photo(js):
    assert js["half"] == ["layers", {"img": "photo", "alpha": 0.75}]
    assert js["zero"] == ["layers", {"img": "photo", "alpha": 1}]  # 0 % = the photo alone


def test_no_fade_in_original_view_or_before_load(js):
    assert js["photo_view"] == ["layers"]
    assert js["no_image"] == ["layers"]
