"""S16: is the +/-1 px sag-ratio resolution an honest uncertainty? Calibrate it against labelled wires.

For every predicted wire on one labelled wire (ml/experiments/s15_measure.py pairs), the deployed fitter is run on the
predicted mask and on the label over the same extent. The absolute difference is the measurement error. It is binned
by chord length and compared with the resolution the app reports (1 px / chord). Offline; no model.

    python -m ml.experiments.s16_uncertainty --dump runs/s15/clamp-square-val.json --split val --form square
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from app.core.config import ROOT_DIR, load_analysis_config
from app.pipeline.span_fit import fit_span
from ml.experiments import s15_aspect as sa
from ml.experiments import s15_measure as sm

BINS = [0, 60, 100, 160, 250, 400, 10_000]


def rows(dump: str, split: str, form: str) -> list[dict]:
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    kw = dict(kink_ratio=cfg.catenary.kink_ratio, kink_check_from=cfg.catenary.kink_check_from)
    out = []
    for p in sm.pairs(json.loads(Path(dump).read_text()), sa.gts(split, form), cfg):
        q = sm.measure(p["pred_mask"], cfg, fit_span, **kw)
        ref = sm.measure(p["ref_mask"], cfg, fit_span, **kw) if "ref_mask" in p else p["ref"]
        if not (q and q["ok"] and ref and ref["ok"]):
            continue
        out.append({"chord": q["chord"], "rel_res": q["rel_res"], "err": abs(q["ratio"] - ref["ratio"]),
                    "res": 1.0 / q["chord"], "cls": p["cls"], "ratio": q["ratio"], "ref": ref["ratio"]})
    return out


def table(rs: list[dict]) -> list[dict]:
    t = []
    for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
        b = [r for r in rs if lo <= r["chord"] < hi]
        if not b:
            continue
        e, res = np.array([r["err"] for r in b]), np.array([r["res"] for r in b])
        t.append({"chord_px": f"{lo}-{hi}", "n": len(b), "median_err_pp": round(float(np.median(e)) * 100, 2),
                  "p90_err_pp": round(float(np.percentile(e, 90)) * 100, 2),
                  "median_res_pp": round(float(np.median(res)) * 100, 2),
                  "covered_by_res": round(float((e <= res).mean()), 3),
                  "p90_err_in_px": round(float(np.percentile(e * np.array([r["chord"] for r in b]), 90)), 2)})
    return t


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", required=True)
    ap.add_argument("--split", default="val")
    ap.add_argument("--form", default="square")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    rs = rows(a.dump, a.split, a.form)
    e_px = np.array([r["err"] * r["chord"] for r in rs])
    rep = {"n": len(rs), "by_chord": table(rs), "covered_by_1px": round(float((e_px <= 1).mean()), 3),
           "err_px_quantiles": {q: round(float(np.percentile(e_px, q)), 2) for q in (50, 68, 80, 90, 95)}}
    print(json.dumps(rep, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1))


if __name__ == "__main__":
    main()


def fit_band(rs: list[dict], q: float = 0.9) -> tuple[float, float]:
    """(a_px, b): the q-quantile of |error| modelled as a / chord + b, by pinball-loss grid search."""
    e, c = np.array([r["err"] for r in rs]), np.array([r["chord"] for r in rs])
    best = (np.inf, 0.0, 0.0)
    for a_px in np.arange(0.5, 8.01, 0.05):
        for b in np.arange(0.0, 0.0151, 0.00025):
            u = e - (a_px / c + b)
            loss = float(np.mean(np.maximum(q * u, (q - 1) * u)))
            if loss < best[0]:
                best = (loss, float(a_px), float(b))
    return round(best[1], 2), round(best[2], 5)


def coverage(rs: list[dict], a_px: float, b: float) -> dict:
    e, c = np.array([r["err"] for r in rs]), np.array([r["chord"] for r in rs])
    out = {"all": round(float((e <= a_px / c + b).mean()), 3)}
    for lo, hi in zip(BINS[:-1], BINS[1:], strict=True):
        m = (c >= lo) & (c < hi)
        if m.any():
            out[f"{lo}-{hi}"] = round(float((e[m] <= a_px / c[m] + b).mean()), 3)
    return out


def calibrate() -> None:
    val = rows("runs/s15/clamp-square-val.json", "val", "square")
    test = rows("runs/s15/clamp-field-test.json", "test", "field")
    a_px, b = fit_band(val)
    rep = {"fitted_on": "val (square, 1024)", "checked_on": "test (field-shaped, held out)", "quantile": 0.9,
           "a_px": a_px, "b_ratio": b, "n_val": len(val), "n_test": len(test),
           "coverage_val": coverage(val, a_px, b), "coverage_test": coverage(test, a_px, b),
           "coverage_test_1px": coverage(test, 1.0, 0.0), "by_chord_test": table(test)}
    print(json.dumps(rep, indent=1))
    Path("docs/thesis/results/s16_uncertainty_calibration.json").write_text(json.dumps(rep, indent=1))


# ----------------------------------------------------------------------------- residual-aware band (S16, D-34)
def feats(rs: list[dict]) -> np.ndarray:
    """Columns of the band: 1 model px over the chord, a constant, the fit's relative residual."""
    return np.array([[1.0 / r["chord"], 1.0, r["rel_res"] or 0.0] for r in rs])


def fit_qr(rs: list[dict], q: float = 0.9) -> list[float]:
    """Linear quantile regression of |error| on feats() (non-negative coefficients), as a linear programme."""
    from scipy.optimize import linprog
    X, y = feats(rs), np.array([r["err"] for r in rs])
    n, k = X.shape
    # minimise q*sum(u+) + (1-q)*sum(u-), with y - X b = u+ - u-, b >= 0
    c = np.concatenate([np.zeros(k), q * np.ones(n), (1 - q) * np.ones(n)])
    A = np.hstack([X, np.eye(n), -np.eye(n)])
    res = linprog(c, A_eq=A, b_eq=y, bounds=[(0, None)] * (k + 2 * n), method="highs")
    return [round(float(v), 5) for v in res.x[:k]]


def band(r: dict, coef: list[float]) -> float:
    return float(feats([r])[0] @ np.asarray(coef))


def flag_precision(rs: list[dict], coef: list[float] | None, cls: str, th: float) -> dict:
    sel = [r for r in rs if r["cls"] == cls]
    lab = int(sum(r["ref"] > th for r in sel))
    def above(r):  # the comparison rule: above only when the whole band lies above the boundary
        return r["ratio"] - (band(r, coef) if coef else 1.0 / r["chord"]) > th
    flagged = [r for r in sel if above(r)]
    tp = int(sum(r["ref"] > th for r in flagged))
    return {"flagged": len(flagged), "true": tp, "precision": round(tp / len(flagged), 2) if flagged else None,
            "label_above": lab, "recall": round(tp / lab, 2) if lab else None}


def calibrate_residual() -> None:
    def rr(dump, split, form):
        out = []
        for r in rows(dump, split, form):
            out.append(r)
        return out
    val = rr("runs/s15/clamp-square-val.json", "val", "square")
    test = rr("runs/s15/clamp-field-test.json", "test", "field")
    coef = fit_qr(val)
    e, u = np.array([r["err"] for r in test]), np.array([band(r, coef) for r in test])
    rep = {"model": "|error| q0.90 = a / chord_model_px + b + c * rel_residual", "a_px": coef[0], "b": coef[1],
           "c": coef[2], "fitted_on": "val", "coverage_val": round(float(np.mean(
               [r["err"] <= band(r, coef) for r in val])), 3), "coverage_test": round(float((e <= u).mean()), 3),
           "median_band_test_pp": round(float(np.median(u)) * 100, 2)}
    for name, cls, th in (("telecom_above_5", "telecom-cable", 0.05), ("power_above_3", "power-distribution-lines", 0.03)):
        for split, rs in (("val", val), ("test", test)):
            rep[f"{name}_{split}_1px"] = flag_precision(rs, None, cls, th)
            rep[f"{name}_{split}_band"] = flag_precision(rs, coef, cls, th)
    print(json.dumps(rep, indent=1))
    Path("docs/thesis/results/s16_uncertainty_residual.json").write_text(json.dumps(rep, indent=1))
