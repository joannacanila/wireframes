"""S16 (Part 10.2): turn field uploads into a versioned, leakage-safe set of photos for human labelling.

Reads a WIREFRAME data directory READ-ONLY (the SQLite database and the stored uploads) and writes a new folder:

    images/<sha256>.jpg      the stored upload (already EXIF-oriented, GPS removed at upload), copied unchanged
    manifest.json            version, source, per photo: sha256, dHash, pole, barangay / city, analysis id, group, split
    README.md                what to do next (docs/thesis/FIELD_LABELLING_GUIDE.md)

Rules that keep the evaluation honest:
  * test records (record_kind = test) and failed analyses are left out; nothing is labelled automatically
  * exact duplicates (same bytes) are kept once; near-duplicates (64-bit dHash within ``--near`` bits, a common
    choice for re-saved / resized copies of one photo) are grouped
  * every photo of one pole, and every near-duplicate group, goes to ONE split, so the same scene is never in both
    training and evaluation; splits are assigned by a hash of the group, so re-running gives the same split
  * the folder is never written into datasets/wireframe_seg; labels come back through ml/annotation_pack.py import

    python -m ml.field_dataset --data-dir <WIREFRAME var> --out runs/field/v1 --version field-v1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

SPLITS = (("train", 0.7), ("val", 0.15), ("test", 0.15))


def dhash(path: Path) -> int | None:
    im = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if im is None:
        return None
    s = cv2.resize(im, (9, 8), interpolation=cv2.INTER_AREA).astype(np.int16)
    bits = (s[:, 1:] > s[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def split_of(group: str) -> str:
    x = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    acc = 0.0
    for name, share in SPLITS:
        acc += share
        if x <= acc:
            return name
    return SPLITS[-1][0]


class _UF:
    def __init__(self):
        self.p: dict[str, str] = {}

    def find(self, a: str) -> str:
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def collect(data_dir: Path, out: Path, version: str, near: int = 6) -> dict:
    db = sqlite3.connect(f"file:{data_dir / 'wireframe.db'}?mode=ro", uri=True)
    q = ("select a.id, a.stored_name, p.pole_code, p.barangay, a.city, a.created_at from analyses a "
         "join poles p on p.id = a.pole_id where coalesce(a.record_kind, 'operational') != 'test' and a.status != 'error'")
    rows = db.execute(q).fetchall()
    items, seen = [], {}
    for aid, stored, pole, brgy, city, created in rows:
        src = data_dir / "uploads" / stored
        if not src.exists():
            continue
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        if sha in seen:
            seen[sha]["analyses"].append(aid)
            continue
        it = {"sha256": sha, "file": f"images/{sha}{src.suffix.lower() or '.jpg'}", "analyses": [aid], "pole": pole,
              "barangay": brgy, "city": city, "created_at": created, "dhash": dhash(src), "_src": src}
        seen[sha] = it
        items.append(it)
    uf = _UF()
    for it in items:
        uf.union(it["sha256"], f"pole:{it['pole']}")  # same pole -> same group
    hashed = [it for it in items if it["dhash"] is not None]
    for i, a in enumerate(hashed):
        for b in hashed[i + 1:]:
            if bin(a["dhash"] ^ b["dhash"]).count("1") <= near:
                uf.union(a["sha256"], b["sha256"])
    out.mkdir(parents=True, exist_ok=False)  # never overwrite an existing version
    (out / "images").mkdir()
    for it in items:
        it["group"] = uf.find(it["sha256"])
        it["split"] = split_of(it["group"])
        shutil.copy2(it.pop("_src"), out / it["file"])
        it["dhash"] = None if it["dhash"] is None else f"{it['dhash']:016x}"
    man = {"version": version, "created": datetime.now(UTC).isoformat(timespec="seconds"),
           "source": "WIREFRAME field uploads (operational, processed; test records excluded)",
           "near_duplicate_bits": near, "splits": {n: sum(i["split"] == n for i in items) for n, _ in SPLITS},
           "groups": len({i["group"] for i in items}), "photos": len(items),
           "labels": "none yet: label with docs/thesis/FIELD_LABELLING_GUIDE.md, import with ml/annotation_pack.py",
           "items": items}
    body = json.dumps(man, indent=1)
    (out / "manifest.json").write_text(body)
    (out / "README.md").write_text(f"# {version}\n\n{len(items)} photos in {man['groups']} leakage groups "
                                   f"({man['splits']}). Label them as described in "
                                   "docs/thesis/FIELD_LABELLING_GUIDE.md. Manifest sha256: "
                                   f"{hashlib.sha256(body.encode()).hexdigest()}\n")
    return man


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--near", type=int, default=6)
    a = ap.parse_args()
    m = collect(a.data_dir, a.out, a.version, a.near)
    print(json.dumps({k: v for k, v in m.items() if k != "items"}, indent=1))


if __name__ == "__main__":
    main()
