"""Assemble docs/thesis/MASTER_CHAPTER4_MANUSCRIPT.md from every versioned report (zero omission by inclusion).

    python -m scripts.build_master_manuscript

Part A  synthesis written for the master file (docs/thesis/master/synthesis.md)
Part B  every versioned Chapter 4 report, verbatim, headings demoted two levels (v2.7 -> v2.13)
Part C  appendices: the complete disclosure log (v2.1 -> v2.13, incl. the v2.6 baseline), the shipped
        configuration verbatim, an index of every results file, the figure index with captions, the
        defense summary, the dataset source register and the git history (live log plus the archived pre-import history).
Nothing in Part B or C is edited; the synthesis cross-references it. Re-run after any report changes.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
T = ROOT / "docs" / "thesis"
OUT = T / "MASTER_CHAPTER4_MANUSCRIPT.md"
REPORTS = [("v2.7", "chapter4_v2.7_results.md"), ("v2.8", "chapter4_v2.8_autonomous_upgrades.md"),
           ("v2.9", "chapter4_v2.9_refinements.md"), ("v2.10", "chapter4_v2.10_hardening.md"),
           ("v2.11", "chapter4_v2.11_field_ui.md"), ("v2.12", "chapter4_v2.12_ensemble.md"),
           ("v2.13", "chapter4_v2.13_cv_ui.md"), ("v2.14", "chapter4_v2.14_cv_ui.md"), ("v2.15", "chapter4_v2.15_site.md")]
FIGURES = {
    "stress_conditions.jpg": "The eight capture conditions of the stress benchmark (v2.8, ml/stress.py).",
    "high_crs_spans_val.jpg": "Visual audit of spans in the high CRS band, validation (v2.8 §4.8.2).",
    "loose_end_candidates_val.jpg": "Topological loose-end candidates on validation; mostly mask gaps and hardware terminations (v2.8 §4.8.7).",
    "failure_hard__jpg.rf.g5CExk8W07QgQYnEr7Kx.jpg": "Failure audit, hardest validation photo: green = predicted and labelled, red = predicted only, blue = labelled only (v2.8 §4.8.9).",
    "failure_hard__jpg.rf.nrAwawkRkLwjZmwXqWJr.jpg": "Failure audit, second-hardest validation photo (v2.8 §4.8.9).",
    "failure_hard__jpg.rf.RJlm0PwKjNFr0qUU4yf5.jpg": "Failure audit, third-hardest validation photo (v2.8 §4.8.9).",
    "failure_easy__jpg.rf.IgYH9U9dB84BFmhdQ7TT.jpg": "Failure audit, an easy validation photo for contrast (v2.8 §4.8.9).",
    "failure_easy__jpg.rf.oqqpYyP8Uap8GcvU12bd.jpg": "Failure audit, a second easy validation photo (v2.8 §4.8.9).",
    "v29_motion_router_val.png": "Motion coherence against Laplacian variance per stress condition; the 0.16 threshold (v2.9 §4.11.1).",
    "v29_inclination_val.png": "Share of high / medium CRS spans per chord-inclination band (v2.9 §4.11.2a).",
    "v29_drop_audit_val.jpg": "Visual audit of 24 'service drops' by image inclination: pole-to-pole spans seen in perspective (v2.9 §4.11.2a).",
    "v29_verdicts.png": "Per-photo verdicts, v2.8 against v2.9, validation and test (v2.9 §4.11.2d, §4.11.6).",
    "v29_diagnostic_example.jpg": "The v2 diagnostic figure: reliability badge, segmentation legend, spans, verdict reasons (v2.9 §4.11.5).",
    "v212_night_veg_guard.jpg": "Night pole P-D67F60: YOLO vegetation instance over the black sky (left) and Depth Anything V2 relative depth (right) (v2.12).",
    "v213_ui_hazard_dark_desktop.jpg": "Redesigned field screener, HAZARD DETECTED, dark theme, desktop (mocked result; v2.13 §4.15.9).",
    "v213_ui_not_at_risk_light_desktop.jpg": "NOT AT RISK with two suppressed ground traces, light theme, desktop (v2.13 §4.15.9).",
    "v213_ui_loader_dark_desktop.jpg": "Shimmer skeleton of the result layout while the analysis runs (v2.13 §4.15.9).",
}


def demote(md: str) -> str:
    out, fence = [], False
    for line in md.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
        if not fence and re.match(r"^#{1,5} ", line):
            line = "#" + line
        out.append(line)
    return "\n".join(out)


def results_index() -> str:
    rows = []
    for p in sorted((T / "results").rglob("*"), key=lambda q: q.relative_to(T).parts):  # case-sensitive, as on macOS
        if not p.is_file():
            continue
        rel = p.relative_to(T).as_posix()  # same index on Windows and macOS
        raw = p.read_bytes()
        # size as committed: a Windows checkout (core.autocrlf) has CRLF in text files, the repository LF
        size = len(raw.replace(b"\r\n", b"\n")) if p.suffix in (".json", ".jsonl", ".csv", ".md", ".txt") else len(raw)
        desc = ""
        if p.suffix == ".json":
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                desc = "keys: " + ", ".join(list(d)[:6]) if isinstance(d, dict) else f"list of {len(d)} records"
            except Exception:
                desc = "JSON"
        elif p.suffix == ".jsonl":
            n = sum(1 for _ in p.open(encoding="utf-8"))
            desc = f"{n} JSON lines"
        elif p.suffix == ".csv":
            n = sum(1 for _ in p.open(encoding="utf-8"))
            desc = f"{n - 1} CSV rows"
        version = re.match(r"(v\d+|server_check_v\d+|live_upload_check_v\d+|ui_check_v\d+)", p.name)
        rows.append(f"| `{rel}` | {version.group(1) if version else ('superseded' if 'superseded' in str(rel) else '-')} | {size:,} B | {desc} |")
    return "| File | Version | Size | Contents |\n|---|---|---|---|\n" + "\n".join(rows)


def figure_index() -> str:
    rows = []
    for p in sorted((T / "figures").glob("*"), key=lambda q: q.name):  # case-sensitive, as on macOS
        rows.append(f"| `figures/{p.name}` | {FIGURES.get(p.name, 'Figure (see the report that produced it).')} |")
    return "| Figure | Caption |\n|---|---|\n" + "\n".join(rows)


def main() -> None:
    parts = []
    synth = (T / "master" / "synthesis.md").read_text(encoding="utf-8")
    parts.append(synth.rstrip() + "\n")
    parts.append("\n---\n\n# Part B — Versioned reports (verbatim)\n\n"
                 "Each report is reproduced exactly as committed, with its headings demoted two levels. Section numbers "
                 "inside a report (4.x, 4.11.x, 4.12.x, 4.13.x, 4.14.x, 4.15.x) are that report's own and are cited as "
                 "\"v2.N §…\" in Part A.\n")
    for i, (ver, name) in enumerate(REPORTS, 1):
        p = T / name
        if not p.exists():
            parts.append(f"\n## B.{i} {ver} — report missing at build time\n")
            continue
        parts.append(f"\n---\n\n## B.{i} Report {ver} (`docs/thesis/{name}`)\n\n" + demote(demote(p.read_text(encoding="utf-8"))) + "\n")
    parts.append("\n---\n\n# Part C — Appendices\n")
    parts.append("\n## C.1 Complete disclosure log (v2.1 to v2.13, including the v2.6 baseline)\n\n"
                 + demote(demote((ROOT / "DISCLOSURE.md").read_text(encoding="utf-8"))) + "\n")
    parts.append("\n## C.2 Shipped configuration (`config/wireframe.yaml`, verbatim)\n\n```yaml\n"
                 + (ROOT / "config" / "wireframe.yaml").read_text(encoding="utf-8") + "```\n")
    parts.append("\n## C.3 Index of results files (`docs/thesis/results/`)\n\n" + results_index() + "\n")
    parts.append("\n## C.4 Figure index\n\n" + figure_index() + "\n")
    parts.append("\n## C.5 Defense summary (verbatim)\n\n" + demote(demote((T / "defense_summary_v2.9.md").read_text(encoding="utf-8"))) + "\n")
    parts.append("\n## C.6 Dataset source register (`ml/sources.yaml`, verbatim)\n\n```yaml\n"
                 + (ROOT / "ml" / "sources.yaml").read_text(encoding="utf-8") + "```\n")
    log = subprocess.run(["git", "log", "--date=short", "--pretty=format:%h | %ad | %s"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    parts.append("\n## C.7 Git history\n\n```\n" + log + "\n```\n")
    pre = T / "master" / "git_history_pre_import.txt"
    if pre.exists():  # v2.13: the GitHub repository starts at the re-import 4dbc6ca; earlier history is archived
        parts.append("\nDevelopment history before the repository was re-imported as `4dbc6ca` "
                     "(archived in `docs/thesis/master/git_history_pre_import.txt`):\n\n```\n"
                     + pre.read_text(encoding="utf-8").rstrip() + "\n```\n")
    text = "\n".join(parts)
    OUT.write_text(text, encoding="utf-8", newline="\n")  # explicit: Windows would default to cp1252 and CRLF
    print(f"wrote {OUT.relative_to(ROOT)}: {OUT.stat().st_size:,} bytes, {text.count(chr(10)):,} lines")


if __name__ == "__main__":
    main()
