"""Static audit: NumPy 2.0 removals, importability, configuration validity."""

import ast
import importlib
import pkgutil
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import app
from app.core.config import ROOT_DIR, AnalysisConfig, load_analysis_config

SOURCES = [p for d in ("app", "ml", "scripts", "tests") for p in (ROOT_DIR / d).rglob("*.py")]

# Removed or deprecated in NumPy 2.0 (NEP 52 / migration guide).
NUMPY2_REMOVED = re.compile(
    r"(?<!\bnp)\.ptp\(|np\.(float_|complex_|unicode_|string_|NaN|Inf|PINF|NINF|infty|Infinity|cfloat|singlecomplex|"
    r"longfloat|longcomplex|clongfloat|alltrue|sometrue|product|cumproduct|round_|in1d|row_stack|trapz|"
    r"asfarray|issubclass_|msort|find_common_type|deprecate|safe_eval|recfromcsv|recfromtxt|"
    r"set_string_function|source|who|lookfor|byte_bounds|compare_chararrays|format_parser|mat\b)\b"
)


def test_no_numpy2_removed_api():
    hits = []
    for p in SOURCES:
        if p.name == "test_codebase_audit.py":
            continue
        for n, line in enumerate(p.read_text().splitlines(), 1):
            if NUMPY2_REMOVED.search(line.split("#")[0]):
                hits.append(f"{p.relative_to(ROOT_DIR)}:{n}: {line.strip()}")
    assert not hits, "\n".join(hits)


def test_numpy_major_version():
    import numpy
    assert int(numpy.__version__.split(".")[0]) >= 2


@pytest.mark.parametrize("p", SOURCES, ids=lambda p: str(p.relative_to(ROOT_DIR)))
def test_sources_parse(p):
    ast.parse(p.read_text())


def test_every_app_module_imports():
    for m in pkgutil.walk_packages(app.__path__, "app."):
        importlib.import_module(m.name)


def test_shipped_config_is_valid_and_flags_provisional():
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    assert cfg.segmentation.iou_nms == 0.45 and cfg.preprocessing.size == 640
    assert cfg.vni.radius_factor == 0.15 and cfg.centerline.l_min_px == 30
    assert "crs" in cfg.provisional_blocks()


@pytest.mark.parametrize("path,value", [
    (("mspi", "alpha"), 0.9),                 # weights no longer sum to 1
    (("vni", "q99"), 0.0),                    # q99 <= q01
    (("crs", "delta_lim0", "abandoned-wire"), 0),  # non-positive limit
    (("preprocessing", "gaussian_kernel"), 4),
])
def test_invalid_configs_are_rejected(path, value):
    import yaml
    raw = yaml.safe_load((ROOT_DIR / "config" / "wireframe.yaml").read_text())
    node = raw
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(ValidationError):
        AnalysisConfig.model_validate(raw)


def test_requirements_cover_imports():
    req = (ROOT_DIR / "requirements.txt").read_text().lower()
    for pkg in ("fastapi", "sqlalchemy", "numpy", "scipy", "opencv-python-headless", "pillow",
                "reportlab", "pydantic-settings", "pyyaml", "python-multipart", "uvicorn"):
        assert pkg in req, pkg
    assert Path(ROOT_DIR / "requirements-ml.txt").exists()


def test_config_updates_keep_comments_and_validate(tmp_path):
    import shutil

    from app.core.config import update_config_values
    p = tmp_path / "w.yaml"
    shutil.copy(ROOT_DIR / "config" / "wireframe.yaml", p)
    before = [ln for ln in p.read_text().splitlines() if ln.lstrip().startswith("#")]
    update_config_values(p, {"segmentation": {"conf_min": 0.31, "trained_enhancement": "auto"}})
    after = p.read_text()
    assert [ln for ln in after.splitlines() if ln.lstrip().startswith("#")] == before
    assert load_analysis_config(p).segmentation.conf_min == 0.31
    with pytest.raises(ValidationError):  # an invalid value is refused, the file stays valid
        update_config_values(p, {"mspi": {"alpha": 0.9}})
    assert load_analysis_config(p).mspi.alpha == pytest.approx(1 / 3)
    with pytest.raises(KeyError):
        update_config_values(p, {"vni": {"no_such_key": 1}})
