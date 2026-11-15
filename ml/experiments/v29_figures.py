"""Figures for Chapter 4 v2.9 (validation evidence only, except the final test verdict panel).

    python -m ml.experiments.v29_figures

Writes into docs/thesis/figures/:
  v29_motion_router_val.png      motion coherence vs Laplacian variance, per stress condition
  v29_inclination_val.png        share of high / medium CRS spans per chord-inclination band
  v29_verdicts.png               per-photo verdicts, v2.8 vs v2.9 (validation; test if available)
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

R = Path("docs/thesis/results")
F = Path("docs/thesis/figures")
COL = {"clean": "#2b6cb0", "lowlight": "#4a5568", "glare": "#d69e2e", "motion": "#c53030", "defocus": "#2f855a",
       "jpeg": "#805ad5", "lowres": "#dd6b20", "rainhaze": "#319795"}
VERDICTS = ["AT RISK - HIGH", "AT RISK - MODERATE", "NOT AT RISK", "INCONCLUSIVE - RETAKE PHOTO"]
VCOL = ["#c53030", "#dd6b20", "#2f855a", "#718096"]


def motion_router() -> None:
    d = json.loads((R / "v29_motion_calib_val.json").read_text())["rows"]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), dpi=150)
    for c, col in COL.items():
        r = [x for x in d if x["condition"] == c]
        ax.scatter([x["sharpness"] for x in r], [x["motion_coherence"] for x in r], s=14, c=col, label=c, alpha=0.85,
                   edgecolors="none")
    ax.axvline(60, color="#999", lw=0.8, ls="--")
    ax.axhline(0.16, color="#999", lw=0.8, ls="--")
    ax.text(64, 0.44, "blur gate (60)", fontsize=7, color="#555")
    ax.text(0.6, 0.165, "motion threshold 0.16", fontsize=7, color="#555")
    ax.set_xscale("log")
    ax.set_xlabel("variance of the Laplacian (log)")
    ax.set_ylabel("direction-coherent blur anisotropy")
    ax.set_title("Motion-blur router, validation stress set (22 photos x 8 conditions)\nglare points above the line are excluded by the overexposure guard", fontsize=8.5)
    ax.legend(fontsize=7, ncol=4, loc="upper right", frameon=False)
    fig.tight_layout()
    fig.savefig(F / "v29_motion_router_val.png")
    plt.close(fig)


def inclination() -> None:
    d = json.loads((R / "v29_span_geometry_summary_val.json").read_text())["by_inclination"]
    labels = [f"{b['band_deg'][0]:.0f}-{b['band_deg'][1]:.0f} deg\n(n={b['spans']})" for b in d]
    hi = [b["high_share"] * 100 for b in d]
    md = [b["medium_share"] * 100 for b in d]
    fig, ax = plt.subplots(figsize=(6.4, 3.6), dpi=150)
    x = range(len(d))
    ax.bar(x, hi, color="#c53030", label="high CRS band")
    ax.bar(x, md, bottom=hi, color="#dd6b20", label="medium CRS band")
    ax.axvline(2.5, color="#555", lw=0.8, ls="--")
    ax.text(2.55, max(h + m for h, m in zip(hi, md, strict=True)) * 0.95, "35 deg: service drops ->", fontsize=7)
    ax.set_xticks(list(x), labels, fontsize=7)
    ax.set_ylabel("% of scored spans")
    ax.set_title("CRS level by chord inclination (validation, single pass)", fontsize=9)
    ax.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(F / "v29_inclination_val.png")
    plt.close(fig)


def verdicts() -> None:
    sets = []
    for split in ("val", "test"):
        p = R / f"v29_verdicts_pipeline_{split}.json"
        if p.exists():
            d = json.loads(p.read_text())
            for name in ("v2.8", "v2.9"):
                k = f"{name} | {split}"
                if k in d:
                    sets.append((f"{name}\n{split} ({len(d[k]['photos'])})", d[k]["verdict"]))
    if not sets:
        return
    fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=150)
    for i, (_lab, v) in enumerate(sets):
        left = 0
        for name, col in zip(VERDICTS, VCOL, strict=True):
            n = v.get(name, 0)
            ax.barh(i, n, left=left, color=col, label=name if i == 0 else None)
            if n:
                ax.text(left + n / 2, i, str(n), ha="center", va="center", fontsize=7, color="white")
            left += n
    ax.set_yticks(range(len(sets)), [s[0] for s in sets], fontsize=7)
    ax.invert_yaxis()
    ax.set_xlabel("photos")
    ax.set_title("Per-photo verdict, v2.8 vs v2.9", fontsize=9)
    ax.legend(fontsize=6.5, ncol=2, frameon=False, loc="lower center", bbox_to_anchor=(0.5, -0.55))
    fig.tight_layout()
    fig.savefig(F / "v29_verdicts.png")
    plt.close(fig)


def main() -> None:
    F.mkdir(parents=True, exist_ok=True)
    motion_router()
    inclination()
    verdicts()


if __name__ == "__main__":
    main()
