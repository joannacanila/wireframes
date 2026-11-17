"""v2.18 pole set: the v2.17 LVIS ``telephone_pole`` set (ml/poles/lvis_prepare.py) with more verified negatives.

Positives and their split membership are copied unchanged from ``datasets/external/poles_lvis`` (no image moves
between splits). Negatives are LVIS images whose annotators *confirmed* that no telephone pole is present
(``telephone_pole`` in ``neg_category_ids``), in two groups:

    * confusable: the image has a box of a vertical or street structure that is not a utility pole
      (lamp post, street light, generic pole, flag pole, mast, antenna, traffic light, street sign, signboard,
      billboard, parking meter, fire hydrant, water tower, windmill) - all of them are taken
    * generic: any other verified-absent image - a fixed-seed sample, so backgrounds stay a minority

LVIS train images -> train / val (85 / 15, seed 0); LVIS val images -> test (as in v2.17). Images stay local (COCO
Flickr licences); only the manifest with COCO URLs is written.

    python -m ml.poles.build_v218 --download
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

import ijson

from ml.poles.lvis_prepare import POLE, download

CONFUSABLE = {11: "antenna", 96: "billboard", 445: "fireplug", 452: "flagpole", 627: "lamppost", 676: "mast",
              766: "parking_meter", 827: "pole", 959: "signboard", 1026: "street_sign", 1027: "streetlight",
              1112: "traffic_light", 1170: "water_tower", 1184: "windmill"}


def verified_negatives(path: Path) -> tuple[dict, dict]:
    imgs = {}
    with path.open("rb") as f:
        for im in ijson.items(f, "images.item"):
            if POLE in im.get("neg_category_ids", []):
                imgs[im["id"]] = im["coco_url"]
    conf: dict[int, set] = {}
    with path.open("rb") as f:
        for a in ijson.items(f, "annotations.item"):
            if a["image_id"] in imgs and a["category_id"] in CONFUSABLE:
                conf.setdefault(a["image_id"], set()).add(CONFUSABLE[a["category_id"]])
    return imgs, conf


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lvis", type=Path, default=Path("datasets/external/lvis"))
    ap.add_argument("--base", type=Path, default=Path("datasets/external/poles_lvis"))
    ap.add_argument("--out", type=Path, default=Path("datasets/external/poles_v218"))
    ap.add_argument("--generic-trainval", type=int, default=200)
    ap.add_argument("--generic-test", type=int, default=40)
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    base = json.loads((a.base / "manifest.json").read_text())["images"]
    have = {m["file"] for m in base}
    manifest = []
    for m in base:  # v2.17 images, same split
        for kind in ("images", "labels"):
            (a.out / kind / m["split"]).mkdir(parents=True, exist_ok=True)
        src = a.base / "images" / m["split"] / m["file"]
        dst = a.out / "images" / m["split"] / m["file"]
        if not dst.exists() and src.exists():
            dst.symlink_to(src.resolve())
        lbl = (a.base / "labels" / m["split"] / m["file"]).with_suffix(".txt")
        (a.out / "labels" / m["split"] / lbl.name).write_text(lbl.read_text())
        manifest.append(m | {"source": "v2.17"})
    rng = random.Random(0)
    stats = {}
    for src, n_generic in (("lvis_v1_train.json", a.generic_trainval), ("lvis_v1_val.json", a.generic_test)):
        imgs, conf = verified_negatives(a.lvis / src)
        c = sorted(i for i in conf if Path(imgs[i]).name not in have)
        g = sorted(i for i in imgs if i not in conf and Path(imgs[i]).name not in have)
        rng.shuffle(g)
        picks = [(i, "confusable") for i in c] + [(i, "generic") for i in g[:n_generic]]
        rng.shuffle(picks)
        if src == "lvis_v1_val.json":
            assign = [("test", p) for p in picks]
        else:
            k = int(round(0.85 * len(picks)))
            assign = [("train", p) for p in picks[:k]] + [("val", p) for p in picks[k:]]
        for split, (i, kind) in assign:
            name = Path(imgs[i]).name
            (a.out / "labels" / split / name).with_suffix(".txt").write_text("")
            manifest.append({"split": split, "file": name, "url": imgs[i], "lvis_image_id": i, "poles": 0,
                             "source": f"v2.18 {kind} negative", "hard_negative_of": sorted(conf.get(i, []))})
            stats.setdefault(split, {}).setdefault(kind, 0)
            stats[split][kind] += 1
    for split in ("train", "val", "test"):
        rows = [m for m in manifest if m["split"] == split]
        stats.setdefault(split, {}).update({"images": len(rows), "positive_images": sum(m["poles"] > 0 for m in rows),
                                            "boxes": sum(m["poles"] for m in rows)})
    (a.out / "manifest.json").write_text(json.dumps({"stats": stats, "images": manifest}, indent=1))
    (a.out / "data.yaml").write_text(f"path: {a.out.resolve()}\ntrain: images/train\nval: images/val\n"
                                     "test: images/test\nnames:\n  0: utility_pole\n")
    print(json.dumps(stats, indent=1))
    if a.download:
        download(a.out, [m for m in manifest if m["source"] != "v2.17"])


if __name__ == "__main__":
    main()
