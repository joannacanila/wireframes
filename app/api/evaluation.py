"""/api/evaluation routes: Table 3.10 targets and the latest evaluation runs (Eq. 12-18).

Values come only from ``python -m ml.evaluate ...`` runs (var/evaluation/report.json). A
section that has not been run is reported as ``not_run``; nothing is estimated or filled in.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.state import AppState, get_state
from app.pipeline.evaluation import TARGETS

router = APIRouter(prefix="/api/evaluation", tags=["evaluation"])
State = Annotated[AppState, Depends(get_state)]

SECTIONS = {
    "segmentation_test": "python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split test",
    "segmentation_val": "python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split val",
    "segmentation_test_degraded": "python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split test --degraded",
    "segmentation_test_degraded_raw": "python -m ml.evaluate segmentation --data datasets/wireframe_seg/data.yaml --split test --degraded --no-enhancement",
    "ultralytics_test": "python -m ml.evaluate ultralytics --data datasets/wireframe_seg/data.yaml --split test",
    "ultralytics_test_degraded": "python -m ml.evaluate ultralytics --data datasets/wireframe_seg/data.yaml --split test --degraded",
    "reference": "python -m ml.evaluate reference --clutter clutter.csv --severity severity.csv --priority priority.csv",
    "geometry_synthetic": "python -m ml.evaluate geometry --scenes 40",
}


@router.get("/metrics")
def metrics(st: State) -> dict:
    path = st.settings.data_dir / "evaluation" / "report.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    sections = {}
    for name, cmd in SECTIONS.items():
        if name in data:
            stale = data[name].get("config_fingerprint") != st.config.fingerprint
            sections[name] = {"status": "stale" if stale else "complete", **data[name]}
        else:
            sections[name] = {"status": "not_run", "command": cmd}
    return {
        "targets": TARGETS,
        "sections": sections,
        "equations": {
            "5": "IoU = |A n B| / |A u B|", "12": "Precision = TP / (TP + FP)", "13": "Recall = TP / (TP + FN)",
            "14": "F1 = 2PR / (P + R)", "15": "RMSE = sqrt(mean((x - y)^2))", "16": "MAE = mean(|x - y|)",
            "17": "Pearson r", "18": "Spearman rho = r(rank(x), rank(y))",
        },
        "notes": [
            "Segmentation is evaluated on the held-out test set once, for the final figures (Section 3.4.5).",
            "Reference agreement (H1, H3, H4) requires the consulting engineer's ratings (Section 3.9).",
            "geometry_synthetic measures the geometric layer on rendered wires only; it is not a field result.",
            "Kendall's tau-b is supplementary; Chapter III specifies Spearman's rho for H4.",
        ],
    }
