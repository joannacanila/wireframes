"""v2.18 utility-pole annotation pack for WIREFRAME's own photographs (CVAT for images 1.1).

The 145-photo team set has no pole boxes, so no pole accuracy can be claimed on WIREFRAME photos until a person
draws them. This pack makes that cheap without letting model output become ground truth:

    * detector boxes are written as ``pole_candidate`` with ``verified=false``; the annotator turns each into a
      ``utility-pole`` (fixing the box) or a ``not-utility-pole`` (a lamp post, sign, column, tower, tree trunk:
      a hard negative), or deletes it, and draws every missed pole
    * per-pole attributes record the hard cases the brief asks for: occluded by wires / vegetation / other, seen
      against the sky, attached equipment (transformer, crossarm hardware), material (concrete / wood / steel)
    * an image counts as labelled only when the annotator adds the image tag ``poles_complete``; a photo with no
      pole then gets the tag and no boxes (a verified pole-free photo), never by default
    * ``import`` writes YOLO box labels for complete images to a NEW folder (datasets/wireframe_poles_v218), with
      the hard-negative boxes and attributes in a JSON sidecar; the team-set splits are kept (train / val / test,
      plus ``field`` for the street photos), and the test split is evaluated once, separately

    python -m ml.poles.annotation_pack export --detector runs/detect/runs/poles/<run>/weights/best.pt
    # CVAT: create one task per split with images/<split>/, Upload annotations -> CVAT 1.1 -> <split>.xml
    python -m ml.poles.annotation_pack import --xml exported_val.xml --split val
"""

from __future__ import annotations

import argparse
import json
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

import cv2

SPLITS = {"train": "datasets/wireframe_seg/images/train", "val": "datasets/wireframe_seg/images/val",
          "test": "datasets/wireframe_seg/images/test", "field": "runs/v216/regression"}
LABELS = ("utility-pole", "not-utility-pole", "pole_candidate")
POLE_ATTRS = {  # name -> (input type, values); the first value is the default
    "verified": ("checkbox", ["false", "true"]),
    "occluded_by": ("select", ["none", "wires", "vegetation", "vehicle-or-building", "other"]),
    "against_sky": ("checkbox", ["false", "true"]),
    "equipment": ("checkbox", ["false", "true"]),
    "material": ("select", ["unknown", "concrete", "wood", "steel"]),
    "truncated": ("checkbox", ["false", "true"]),
    "source": ("text", [""]),
}
TAG = "poles_complete"

README = """# WIREFRAME utility-pole labels (v2.18)

One CVAT task per folder in `images/`; upload `<split>.xml` (CVAT for images 1.1).

A **utility pole** is a pole that carries overhead power or telecom wires (concrete, wood or steel), including the
part hidden behind wires or leaves. Draw the box from the top of the pole (or its top hardware) to where it meets
the ground or leaves the frame (tick `truncated` if it leaves the frame).

1. `pole_candidate` boxes come from a detector and are **not** labels. For each: if it is a utility pole, change the
   label to `utility-pole`, fix the box and tick `verified`; if it is something else vertical (lamp post, sign
   post, column, tower, tree trunk, building edge) change it to `not-utility-pole` and tick `verified`; if it is
   nothing, delete it.
2. Draw a `utility-pole` box for every utility pole the detector missed, however small or occluded.
3. Fill the attributes: `occluded_by`, `against_sky`, `equipment` (transformer, crossarm, cabinets), `material`.
4. When every utility pole in the photo is boxed, add the image tag **poles_complete** (also for a photo that has no
   pole: tag it and draw nothing). Photos without the tag are not imported.

Do not guess: if you cannot tell whether a structure is a utility pole, label it `not-utility-pole` and write
`unsure` in `source`; it is then left out of both positives and negatives.
"""


def export(detector: str | None, out: Path, conf: float, splits: list[str]) -> None:
    det = None
    if detector:
        from app.core.config import ROOT_DIR
        from app.pipeline.poles import make_pole_detector
        det = make_pole_detector(detector, conf, ROOT_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / "README.md").write_text(README)
    summary = {}
    for split in splits:
        imgs = sorted(p for p in Path(SPLITS[split]).glob("*.jpg") if not p.name.count(".v21"))
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        root = ET.Element("annotations")
        ET.SubElement(root, "version").text = "1.1"
        meta = ET.SubElement(ET.SubElement(root, "meta"), "task")
        labels = ET.SubElement(meta, "labels")
        for name in LABELS:
            lab = ET.SubElement(labels, "label")
            ET.SubElement(lab, "name").text = name
            ET.SubElement(lab, "type").text = "rectangle"
            attrs = ET.SubElement(lab, "attributes")
            for an, (kind, vals) in POLE_ATTRS.items():
                at = ET.SubElement(attrs, "attribute")
                ET.SubElement(at, "name").text = an
                ET.SubElement(at, "mutable").text = "false"
                ET.SubElement(at, "input_type").text = kind
                ET.SubElement(at, "default_value").text = vals[0]
                ET.SubElement(at, "values").text = "\n".join(vals)
        tag = ET.SubElement(labels, "label")
        ET.SubElement(tag, "name").text = TAG
        ET.SubElement(tag, "type").text = "tag"
        n_box = 0
        for i, p in enumerate(imgs):
            shutil.copy2(p, out / "images" / split / p.name)
            im = cv2.imread(str(p))
            h, w = im.shape[:2]
            el = ET.SubElement(root, "image", id=str(i), name=p.name, width=str(w), height=str(h))
            found = (det.detect(cv2.cvtColor(im, cv2.COLOR_BGR2RGB)) or []) if det else []
            for b in found:
                x0, y0, x1, y1 = b["bbox"]
                s = ET.SubElement(el, "box", label="pole_candidate", occluded="0", source="auto", xtl=f"{x0:.1f}",
                                  ytl=f"{y0:.1f}", xbr=f"{x1:.1f}", ybr=f"{y1:.1f}", z_order="0")
                for an, (_, vals) in POLE_ATTRS.items():
                    v = f"model:{Path(detector).parent.parent.name if detector.endswith('.pt') else detector} " \
                        f"conf {b['confidence']}" if an == "source" else vals[0]
                    ET.SubElement(s, "attribute", name=an).text = v
                n_box += 1
        ET.ElementTree(root).write(out / f"{split}.xml", encoding="utf-8", xml_declaration=True)
        summary[split] = {"images": len(imgs), "candidate_boxes": n_box}
    (out / "summary.json").write_text(json.dumps({"detector": detector, "conf": conf, "splits": summary}, indent=1))
    print(json.dumps(summary, indent=1))


def parse(xml: Path) -> dict:
    """{image name: {"w", "h", "complete", "poles": [...], "negatives": [...], "unverified": int}}."""
    out = {}
    for el in ET.parse(xml).getroot().iter("image"):
        rec = {"w": int(el.get("width")), "h": int(el.get("height")),
               "complete": any(t.get("label") == TAG for t in el.iter("tag")), "poles": [], "negatives": [],
               "unverified": 0}
        for s in el.iter("box"):
            attrs = {a.get("name"): (a.text or "") for a in s.iter("attribute")}
            box = [float(s.get(k)) for k in ("xtl", "ytl", "xbr", "ybr")]
            lab = s.get("label")
            if lab == "pole_candidate" or attrs.get("verified") != "true":
                rec["unverified"] += 1
            elif lab == "utility-pole":
                rec["poles"].append({"bbox": box, "occluded": s.get("occluded") == "1",
                                     **{k: attrs.get(k) for k in POLE_ATTRS if k not in ("verified", "source")}})
            elif lab == "not-utility-pole" and "unsure" not in attrs.get("source", ""):
                rec["negatives"].append({"bbox": box})
        out[el.get("name")] = rec
    return out


def import_cvat(xml: Path, split: str, out: Path) -> dict:
    recs = parse(xml)
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    side, ok, skipped = {}, 0, []
    for name, r in recs.items():
        if not r["complete"] or r["unverified"]:
            skipped.append(name)
            continue
        src = Path(SPLITS[split]) / name
        dst = out / "images" / split / name
        if not dst.exists():
            dst.symlink_to(src.resolve())
        w, h = r["w"], r["h"]
        rows = [f"0 {(b[0] + b[2]) / 2 / w:.6f} {(b[1] + b[3]) / 2 / h:.6f} {(b[2] - b[0]) / w:.6f} {(b[3] - b[1]) / h:.6f}"
                for b in (p["bbox"] for p in r["poles"])]
        (out / "labels" / split / Path(name).with_suffix(".txt").name).write_text("\n".join(rows) + ("\n" if rows else ""))
        side[name] = {"poles": r["poles"], "negatives": r["negatives"]}
        ok += 1
    (out / f"{split}_attributes.json").write_text(json.dumps(side, indent=1))
    (out / "data.yaml").write_text(f"path: {out.resolve()}\n" + "".join(f"{s}: images/{s}\n" for s in SPLITS)
                                   + "names:\n  0: utility_pole\n")
    res = {"split": split, "imported": ok, "skipped_incomplete_or_unverified": len(skipped)}
    print(json.dumps(res))
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--detector", default=None)
    e.add_argument("--conf", type=float, default=0.25)
    e.add_argument("--out", type=Path, default=Path("runs/v218/pole_annotation"))
    e.add_argument("--splits", nargs="+", default=list(SPLITS))
    i = sub.add_parser("import")
    i.add_argument("--xml", type=Path, required=True)
    i.add_argument("--split", choices=list(SPLITS), required=True)
    i.add_argument("--out", type=Path, default=Path("datasets/wireframe_poles_v218"))
    a = ap.parse_args()
    if a.cmd == "export":
        export(a.detector, a.out, a.conf, a.splits)
    else:
        import_cvat(a.xml, a.split, a.out)


if __name__ == "__main__":
    main()
