"""v2.17: a utility-pole detection set from LVIS v1 (`telephone_pole`, human-annotated boxes on COCO 2017 photos).

Why LVIS: the Zenodo Street View set is level, horizontal, Curitiba-only and labels every post ("poste": utility,
lamp and sign posts alike). WIREFRAME photos look *up* at the pole from the kerb. LVIS photos are hand-held,
varied in viewpoint (many upward), and LVIS separates `telephone_pole` ("tall pole supporting telephone wires") from
`lamppost`, `streetlight`, `pole` and `flagpole`, which is exactly the distinction the screener needs.

LVIS is federated, so the labels are used only where they are complete for this category:
    * positives: images with >= 1 telephone_pole box, and telephone_pole NOT in not_exhaustive_category_ids
    * verified negatives: telephone_pole in neg_category_ids (annotators confirmed it is absent) and the image
      contains a lamppost / streetlight / pole / flagpole box (hard negatives: vertical posts that are not utility poles)
Every other image is skipped (its pole status is unknown).

Splits: LVIS *train* images -> our train / val (85 / 15, image-level, seed 0); LVIS *val* images -> our test, which
is never used for selection. Licences: LVIS annotations CC BY 4.0; COCO images are Flickr photos under their own
(mostly non-commercial) Creative Commons terms, so the images and any weights trained on them stay local.

    python -m ml.poles.lvis_prepare --lvis datasets/external/lvis --out datasets/external/poles_lvis
    # then fetch the listed images (parallel curl), see --download
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
from pathlib import Path

import ijson

POLE = 1074  # telephone_pole
HARD = {627: "lamppost", 1027: "streetlight", 827: "pole", 452: "flagpole"}


def scan(path: Path) -> tuple[dict, dict, dict]:
    images, boxes, hard = {}, {}, {}
    with path.open("rb") as f:
        for im in ijson.items(f, "images.item"):
            images[im["id"]] = {"url": im["coco_url"], "w": im["width"], "h": im["height"],
                                "neg": POLE in im.get("neg_category_ids", []),
                                "nonex": POLE in im.get("not_exhaustive_category_ids", [])}
    with path.open("rb") as f:
        for a in ijson.items(f, "annotations.item"):
            c = a["category_id"]
            if c == POLE:
                boxes.setdefault(a["image_id"], []).append([float(v) for v in a["bbox"]])
            elif c in HARD:
                hard.setdefault(a["image_id"], set()).add(HARD[c])
    return images, boxes, hard


def select(images, boxes, hard, max_neg: int, rng: random.Random):
    pos = [i for i in boxes if not images[i]["nonex"]]
    neg = [i for i in hard if images[i]["neg"] and i not in boxes]
    rng.shuffle(neg)
    return sorted(pos), sorted(neg[:max_neg])


def write(out: Path, split: str, ids, images, boxes, hard, manifest: list):
    (out / "labels" / split).mkdir(parents=True, exist_ok=True)
    (out / "images" / split).mkdir(parents=True, exist_ok=True)
    for i in ids:
        im = images[i]
        name = Path(im["url"]).name
        lines = []
        for x, y, w, h in boxes.get(i, []):
            lines.append(f"0 {(x + w / 2) / im['w']:.6f} {(y + h / 2) / im['h']:.6f} {w / im['w']:.6f} {h / im['h']:.6f}")
        (out / "labels" / split / name).with_suffix(".txt").write_text("\n".join(lines) + ("\n" if lines else ""))
        manifest.append({"split": split, "file": name, "url": im["url"], "lvis_image_id": i, "poles": len(lines),
                         "hard_negative_of": sorted(hard.get(i, [])) if not lines else []})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lvis", type=Path, default=Path("datasets/external/lvis"))
    ap.add_argument("--out", type=Path, default=Path("datasets/external/poles_lvis"))
    ap.add_argument("--neg-ratio", type=float, default=0.5, help="verified hard negatives per positive image")
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    rng = random.Random(0)
    manifest: list[dict] = []
    stats = {}
    for src, splits in (("lvis_v1_train.json", ("train", "val")), ("lvis_v1_val.json", ("test",))):
        images, boxes, hard = scan(a.lvis / src)
        pos, neg = select(images, boxes, hard, int(a.neg_ratio * len([i for i in boxes])), rng)
        if splits == ("test",):
            write(a.out, "test", pos + neg, images, boxes, hard, manifest)
            stats["test"] = {"positive_images": len(pos), "negative_images": len(neg),
                             "boxes": sum(len(boxes[i]) for i in pos)}
        else:
            ids = pos + neg
            rng.shuffle(ids)
            k = int(round(0.85 * len(ids)))
            for split, part in (("train", ids[:k]), ("val", ids[k:])):
                write(a.out, split, part, images, boxes, hard, manifest)
                p = [i for i in part if i in boxes]
                stats[split] = {"positive_images": len(p), "negative_images": len(part) - len(p),
                                "boxes": sum(len(boxes[i]) for i in p)}
        del images, boxes, hard
    (a.out / "manifest.json").write_text(json.dumps({"stats": stats, "images": manifest}, indent=1))
    (a.out / "data.yaml").write_text(f"path: {a.out.resolve()}\ntrain: images/train\nval: images/val\n"
                                     "test: images/test\nnames:\n  0: utility_pole\n")
    print(json.dumps(stats, indent=1))
    if a.download:
        download(a.out, manifest)


def download(out: Path, manifest: list[dict]) -> None:
    todo = [m for m in manifest if not (out / "images" / m["split"] / m["file"]).exists()]
    # one curl per file, 8 in parallel; COCO's public image host, no account needed
    pairs = "\n".join(f"{m['url']} {out / 'images' / m['split'] / m['file']}" for m in todo)
    subprocess.run(["xargs", "-P", "8", "-n", "2", "sh", "-c", 'curl -sf -o "$1" "$0"'], input=pairs.encode(), check=False)
    missing = [m["file"] for m in todo if not (out / "images" / m["split"] / m["file"]).exists()]
    print(f"downloaded {len(todo) - len(missing)} of {len(todo)}; missing {len(missing)}")


if __name__ == "__main__":
    main()
