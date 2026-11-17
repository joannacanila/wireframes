"""v2.17 centerline metrics on cached WireNet probabilities (validation; no models loaded, no scene gates).

Separates what the *model* gets right from what span assembly and gates do:
    pixel     every argmax wire pixel, class = argmax class (no spans): the model's own output
    strands   the v2.16 strand extractor (shipped settings), class per strand, "uncertain" below the 0.9 share

    python -m ml.experiments.v217_cached --cache runs/cache/val-v216
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.core.config import CLASSES, ROOT_DIR, load_analysis_config
from app.pipeline import centerline_eval as ce
from app.pipeline.wire_instances import strand_spans
from app.pipeline.wirenet import span_params
from ml.experiments import harness
from ml.experiments.v216_strands import WIRE_IDS


def run(cache: Path, mode: str, params: dict, min_share: float, n_boot: int = 1000):
    per, tally = [], ce.StrandTally()
    for _, z in harness.load(cache):
        probs = z["probs"].astype("float32")
        gts = [(c, m) for c, m in zip(z["gt_classes"].tolist(), z["gt_masks"], strict=True) if c in ce.WIRE_CLASSES]
        shape = probs.shape[1:]
        if mode == "pixel":
            am = probs.argmax(0)
            preds = [ce.Pred(CLASSES[i - 1], am == i) for i in WIRE_IDS]
        else:
            preds = [ce.Pred(s.wire_class, s.mask, s.class_status != "uncertain")
                     for s in strand_spans(probs, WIRE_IDS, params, min_class_share=min_share)]
            tally.add(preds, gts, shape)
        per.append(ce.image_counts(preds, gts, shape))
    return {"centerline": ce.summarize(per, n_boot), "strand_class": tally.report() if mode != "pixel" else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path("runs/cache/val-v216"))
    ap.add_argument("--out", type=Path, default=Path("docs/thesis/results/v217_cached_val.json"))
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").segmentation
    out = {}
    for mode in ("pixel", "strands"):
        out[mode] = r = run(a.cache, mode, span_params(cfg), cfg.span_class_min_share)
        f = lambda x: "-" if x is None else f"{x:.3f}"  # noqa: E731
        for t, m in r["centerline"].items():
            if isinstance(m, dict):
                print(f"{mode:8s} {t:26s} P {f(m['precision'])} R {f(m['recall'])} F1 {f(m['f1'])} "
                      f"F1 CI {[round(v, 3) for v in m['f1_ci95'] or []]}")
        if r["strand_class"]:
            s = r["strand_class"]
            print("  strand class acc", f(s["accuracy"]), "uncertain", f(s["uncertain_rate"]), s["confusion"])
    a.out.write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
