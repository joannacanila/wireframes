"""Pole-detector dataset (v2.16) from Zenodo record 20091961, "Annotated Google Street View dataset for pole and tree
detection" (Curitiba, Brazil; CC BY 4.0 as published; images are Google Street View captures, so use is limited to
research and neither the images nor weights trained on them are redistributed by this repository).

* classes in the source: 0 = arvore (tree), 1 = poste (post: utility poles, also lamp and sign posts)
* kept: poste only, as class 0 "pole"
* split by street sequence (the filename prefix "Image_<seq> (<n>)"), 70:15:15 with a fixed seed, so frames of one
  street never fall in two splits (adjacent Street View frames are near-duplicates)

    python -m ml.poles.prepare --src datasets/external/zenodo_20091961_gsv_poles/extracted --out datasets/external/poles_yolo
"""

from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("datasets/external/zenodo_20091961_gsv_poles/extracted"))
    ap.add_argument("--out", type=Path, default=Path("datasets/external/poles_yolo"))
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()
    seqs: dict[str, list[Path]] = defaultdict(list)
    for img in sorted((a.src / "Images").glob("*.jpg")):
        m = re.match(r"Image_([^ ]+) \(\d+\)$", img.stem)
        seqs[m.group(1) if m else img.stem].append(img)
    keys = sorted(seqs)
    random.Random(a.seed).shuffle(keys)
    n = len(keys)
    cut = {"train": keys[: round(0.70 * n)], "val": keys[round(0.70 * n): round(0.85 * n)], "test": keys[round(0.85 * n):]}
    stats = {}
    for split, ks in cut.items():
        (a.out / "images" / split).mkdir(parents=True, exist_ok=True)
        (a.out / "labels" / split).mkdir(parents=True, exist_ok=True)
        n_img = n_box = 0
        for k in ks:
            for img in seqs[k]:
                lbl = a.src / "Labels" / f"{img.stem}.txt"
                rows = []
                if lbl.exists():
                    for line in lbl.read_text().splitlines():
                        p = line.split()
                        if len(p) == 5 and p[0] == "1":
                            rows.append("0 " + " ".join(p[1:]))
                name = img.name.replace(" ", "_").replace("(", "").replace(")", "")
                dst = a.out / "images" / split / name
                if not dst.exists():
                    dst.symlink_to(img.resolve())
                (a.out / "labels" / split / Path(name).with_suffix(".txt").name).write_text("\n".join(rows) + ("\n" if rows else ""))
                n_img += 1
                n_box += len(rows)
        stats[split] = {"sequences": len(ks), "images": n_img, "poles": n_box}
    (a.out / "data.yaml").write_text(f"path: {a.out.resolve()}\ntrain: images/train\nval: images/val\ntest: images/test\n"
                                     "names:\n  0: pole\n")
    (a.out / "split.json").write_text(json.dumps({"seed": a.seed, "stats": stats, "sequences": cut}, indent=1))
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
