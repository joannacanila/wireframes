"""v2.17 human-verification pack: what the labels are missing, as CVAT 1.1 XML pre-annotations that a person must
check before anything enters training or evaluation.

The 145-photo set omits many real wires (thesis reference Part 12, item 13), has no pole boxes, and has no explicit
negatives, so "not labelled" cannot be treated as "not a wire" and precision measured on it is a lower bound.
This pack makes the missing labels cheap to add without letting model output become ground truth:

    * every shipped prediction is written with attribute ``verified=false`` and the label it came from
      (``wire_candidate``: a reported wire not covered by a label; ``pole_candidate``: a detector box;
      ``structure_negative``: a span the structure-edge gate removed)
    * existing human labels are written unchanged with ``verified=true`` so the annotator sees the full picture
    * ``import`` accepts a CVAT export only for images whose every shape has ``verified=true`` (the annotator ticks
      it per shape after correcting it) and writes YOLO-seg / YOLO-box files to a NEW folder; nothing in
      datasets/wireframe_seg is modified, and the test split is exported for completeness only (its additions
      become a new, separately reported test set, never mixed into training)

    python -m ml.annotation_pack export --split val --dump runs/v217/dump/v216-val.json --out runs/v217/annotation/val
    # annotate in CVAT (Projects -> import "CVAT for images 1.1" with the images folder), export again, then:
    python -m ml.annotation_pack import --xml exported.xml --out datasets/wireframe_seg_v217/val

Instructions for annotators: runs/v217/annotation/<split>/README.md (written by ``export``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2
import numpy as np

from app.core.config import CLASSES

README = """# WIREFRAME label verification pack

Open `annotations.xml` in CVAT (create a task with the images in `images/`, then *Upload annotations* -> CVAT 1.1).

Every shape has an attribute **verified**. Shapes the model proposed start as `verified = false`.

1. **wire_candidate** (polyline/polygon): a wire the screener reported where no label exists. If it is a real
   wire, change its label to the wire type you can see (`power-distribution-lines`, `telecom-cable`,
   `abandoned-wire`, or `wire-unknown-type` if you cannot tell) and trace it continuously along the whole visible
   cable. If it is not a wire, change the label to `not-wire` (it becomes a hard negative). Tick `verified`.
2. **pole_candidate** (box): fix the box to the whole visible utility pole; delete boxes on lamp posts, sign
   posts, trees or columns; draw any missed utility pole. Tick `verified`.
3. **structure_negative** (polygon): an edge the screener ignored as a flyover / sign / roof edge. If it really is
   not a wire, keep it as `not-wire`; if it is a wire, relabel it as above. Tick `verified`.
4. Existing labels (`verified = true`) may be corrected; also trace any wire still missing.

An image is imported only when **every** shape on it is verified. Do not label a whole photo negative: only the
traced `not-wire` shapes are negatives, everything else unlabelled stays "unknown".
"""

LABELS = [*CLASSES, "wire-unknown-type", "not-wire", "utility-pole", "wire_candidate", "pole_candidate",
          "structure_negative"]


def _attr(el, verified: bool, source: str) -> None:
    ET.SubElement(el, "attribute", name="verified").text = "true" if verified else "false"
    ET.SubElement(el, "attribute", name="source").text = source


def _pts(poly) -> str:
    return ";".join(f"{x:.1f},{y:.1f}" for x, y in poly)


def export(split: str, dump: Path, out: Path, coverage_tol: int = 5) -> None:
    from app.pipeline.segment import parse_yolo_seg_labels
    rows = {r["stem"]: r for r in json.loads(dump.read_text())}
    (out / "images").mkdir(parents=True, exist_ok=True)
    root = ET.Element("annotations")
    ET.SubElement(root, "version").text = "1.1"
    meta = ET.SubElement(ET.SubElement(root, "meta"), "task")
    labels = ET.SubElement(meta, "labels")
    for name in LABELS:
        lab = ET.SubElement(labels, "label")
        ET.SubElement(lab, "name").text = name
        attrs = ET.SubElement(lab, "attributes")
        for an, vals in (("verified", "false\ntrue"), ("source", "")):
            a = ET.SubElement(attrs, "attribute")
            ET.SubElement(a, "name").text = an
            ET.SubElement(a, "input_type").text = "checkbox" if an == "verified" else "text"
            ET.SubElement(a, "values").text = vals
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * coverage_tol + 1,) * 2)
    n_cand = 0
    for i, img in enumerate(sorted(Path(f"datasets/wireframe_seg/images/{split}").glob("*.jpg"))):
        shutil.copy2(img, out / "images" / img.name)
        h, w = cv2.imread(str(img)).shape[:2]
        el = ET.SubElement(root, "image", id=str(i), name=img.name, width=str(w), height=str(h))
        lbl = Path(str(img).replace("/images/", "/labels/")).with_suffix(".txt")
        gts = parse_yolo_seg_labels(lbl.read_text() if lbl.exists() else "", w, h)
        covered = np.zeros((h, w), np.uint8)
        for g in gts:
            m = g.mask.astype(np.uint8)
            covered |= cv2.dilate(m, k)
            for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
                if len(c) >= 3:
                    s = ET.SubElement(el, "polygon", label=g.wire_class, occluded="0", points=_pts(c[:, 0, :]))
                    _attr(s, True, "human-label")
        row = rows.get(img.stem)
        for wd in (row or {}).get("wires", []):
            for poly in wd["polygons"]:
                p = np.asarray(poly, np.int32)
                if len(p) < 3:
                    continue
                m = np.zeros((h, w), np.uint8)
                cv2.fillPoly(m, [p], 1)
                if (m & covered).sum() >= 0.5 * max(m.sum(), 1):
                    continue  # mostly on an existing label: nothing new to verify
                s = ET.SubElement(el, "polygon", label="wire_candidate", occluded="0", points=_pts(p))
                _attr(s, False, f"model:{wd['cls']}{'' if wd['confident'] else ' (type uncertain)'}")
                n_cand += 1
        for b in (row or {}).get("pole_boxes", []):
            s = ET.SubElement(el, "box", label="pole_candidate", occluded="0", xtl=str(b[0]), ytl=str(b[1]),
                              xbr=str(b[2]), ybr=str(b[3]))
            _attr(s, False, "model:pole")
        for sp in (row or {}).get("suppressed_polygons", []):
            s = ET.SubElement(el, "polygon", label="structure_negative", occluded="0", points=_pts(sp))
            _attr(s, False, "model:structure-edge gate")
    (out / "annotations.xml").write_bytes(ET.tostring(root))
    (out / "README.md").write_text(README)
    print(f"{out}: {i + 1} images, {n_cand} wire candidates to verify")


def import_cvat(xml: Path, out: Path) -> None:
    root = ET.parse(xml).getroot()
    (out / "labels").mkdir(parents=True, exist_ok=True)
    seg_ids = {c: j for j, c in enumerate(CLASSES)}
    ok = skipped = 0
    for el in root.iter("image"):
        shapes = [s for s in el if s.tag in ("polygon", "polyline", "box")]
        if not shapes or any(s.find("attribute[@name='verified']") is None or
                             s.find("attribute[@name='verified']").text != "true" for s in shapes):
            skipped += 1
            continue
        w, h = float(el.get("width")), float(el.get("height"))
        seg, poles, neg = [], [], []
        for s in shapes:
            lab = s.get("label")
            if s.tag == "box":
                if lab == "utility-pole":
                    x0, y0, x1, y1 = (float(s.get(k)) for k in ("xtl", "ytl", "xbr", "ybr"))
                    poles.append(f"0 {(x0 + x1) / 2 / w:.6f} {(y0 + y1) / 2 / h:.6f} {(x1 - x0) / w:.6f} {(y1 - y0) / h:.6f}")
                continue
            pts = [tuple(map(float, p.split(","))) for p in s.get("points").split(";")]
            norm = " ".join(f"{x / w:.6f} {y / h:.6f}" for x, y in pts)
            if lab in seg_ids:
                seg.append(f"{seg_ids[lab]} {norm}")
            elif lab in ("not-wire", "wire-unknown-type"):
                neg.append({"label": lab, "points": pts})
            # unverifiable candidate labels left as-is are rejected by the verified check above
        stem = Path(el.get("name")).stem
        (out / "labels" / f"{stem}.txt").write_text("\n".join(seg) + "\n")
        (out / "labels" / f"{stem}.poles.txt").write_text("\n".join(poles) + ("\n" if poles else ""))
        (out / "labels" / f"{stem}.regions.json").write_text(json.dumps(neg))
        ok += 1
    print(f"imported {ok} fully verified images; skipped {skipped} with unverified or no shapes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["export", "import"])
    ap.add_argument("--split", default="val")
    ap.add_argument("--dump", type=Path)
    ap.add_argument("--xml", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    if a.stage == "export":
        export(a.split, a.dump, a.out)
    else:
        import_cvat(a.xml, a.out)


if __name__ == "__main__":
    main()
