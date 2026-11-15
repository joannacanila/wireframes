"""Dataset audit, cleaning and partitioning (Section 3.4.2 - 3.4.5).

    python -m ml.curate --download                       # fetch from Roboflow, then curate
    python -m ml.curate --raw datasets/raw --out datasets/wireframe_seg

Input: one folder per source under --raw, each a Roboflow YOLO export
(``<split>/images/*.jpg`` and ``<split>/labels/*.txt``; any split names). Output: a
four-class YOLO-seg dataset (Vegetation, abandoned-wire, power-distribution-lines, telecom-cable), ``data.yaml``, ``annotation_queue.csv`` and ``audit.json``
with the count after every step (the thesis requires the counts to be reported).

Steps, in the order of Section 3.4.2:
  1. licence       sources whose licence is not allowed are excluded
  2. viewpoint     images listed in ``<source>/exclude_viewpoint.txt`` are excluded
                   (aerial/non-ground views are judged by a person, not guessed)
  3. duplicates    exact (SHA-256) and near (64-bit DCT pHash, Hamming <= k) duplicates,
                   within and across sources; one image per group is kept and the group
                   id is reused in step 7 so near-duplicates never straddle splits
  4. privacy       Haar-cascade face and plate screening; detections are blurred and the
                   image is listed for manual confirmation
  5. labels        mapped through class_map.yaml; polygon labels are kept; bounding boxes
                   are NOT converted to polygons (a box is not a wire mask) - box-only and
                   `review` images go to the annotation queue
  6. background    images without any wire are capped at background_fraction_max
  7. strata/split  density strata at the tertiles of instance counts; 70:15:15 split,
                   stratified by density, grouped by duplicate group
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import yaml

from app.core.config import CLASSES, VEGETATION

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


# ------------------------------------------------------------------ pure helpers (tested)

def norm_label(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[\s\-/]+", "_", name.strip().lower())).strip("_")


def phash(gray: np.ndarray) -> int:
    """64-bit perceptual hash: sign of the 8x8 low-frequency DCT block against its median."""
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(small)[:8, :8].flatten()
    bits = d > np.median(d[1:])
    return int(sum(1 << i for i, b in enumerate(bits) if b))


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


class UnionFind:
    def __init__(self) -> None:
        self.p: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def duplicate_groups(hashes: dict[str, int], max_dist: int) -> dict[str, str]:
    """Group ids by near-duplicate pHash (BK-free O(n^2/64) scan; fine for ~10^4 images)."""
    uf = UnionFind()
    keys = sorted(hashes)
    arr = np.array([hashes[k] for k in keys], dtype=np.uint64)
    for i, k in enumerate(keys):
        uf.find(k)
        if i + 1 < len(keys):
            x = np.bitwise_xor(arr[i + 1 :], arr[i])
            dist = np.array([int(v).bit_count() for v in x])
            for j in np.flatnonzero(dist <= max_dist):
                uf.union(k, keys[i + 1 + j])
    return {k: uf.find(k) for k in keys}


def parse_label_line(line: str) -> tuple[int, str, list[float]] | None:
    """Returns (class_id, kind, coords) with kind 'box' (5 values) or 'polygon' (>= 7)."""
    parts = line.split()
    if not parts:
        return None
    k, coords = int(parts[0]), [float(v) for v in parts[1:]]
    if len(coords) == 4:
        return k, "box", coords
    if len(coords) >= 6 and len(coords) % 2 == 0:
        return k, "polygon", coords
    raise ValueError(f"malformed label line: {line[:60]}")


def density_strata(counts: dict[str, int]) -> tuple[dict[str, str], tuple[float, float]]:
    """Low / Moderate / High at the tertiles of instance counts (Section 3.4.4)."""
    vals = np.array(list(counts.values()), dtype=float)
    t1, t2 = (np.quantile(vals, [1 / 3, 2 / 3]) if len(vals) else (0.0, 0.0))
    out = {k: ("Low" if v <= t1 else "Moderate" if v <= t2 else "High") for k, v in counts.items()}
    return out, (float(t1), float(t2))


def grouped_stratified_split(items: dict[str, tuple[str, str]], ratios: dict[str, float], seed: int
                             ) -> dict[str, str]:
    """items: image -> (group, stratum). Every duplicate group is assigned whole, so similar
    images never appear in two splits (Section 3.4.5). A group is balanced under its majority
    stratum; within a stratum, groups (largest first, ties shuffled) go to the split whose
    filled share is furthest below its target."""
    rng = np.random.default_rng(seed)
    members: dict[str, list[str]] = defaultdict(list)
    for img, (grp, _) in items.items():
        members[grp].append(img)
    by_stratum: dict[str, list[str]] = defaultdict(list)
    for grp, imgs in members.items():
        stratum = Counter(items[i][1] for i in imgs).most_common(1)[0][0]
        by_stratum[stratum].append(grp)
    names = list(ratios)
    out: dict[str, str] = {}
    for stratum in sorted(by_stratum):
        grps = sorted(by_stratum[stratum])
        grps = [grps[i] for i in rng.permutation(len(grps))]
        grps.sort(key=lambda g: -len(members[g]))
        total = sum(len(members[g]) for g in grps)
        filled = dict.fromkeys(names, 0)
        for g in grps:
            target = max(names, key=lambda n: ratios[n] * total - filled[n])
            filled[target] += len(members[g])
            for im in members[g]:
                out[im] = target
    return out


def letterbox_coords(coords: list[float], w: int, h: int, size: int) -> list[float]:
    """Normalised polygon on a w x h image -> normalised on its size x size letterbox (Fit)."""
    s = min(size / w, size / h)
    px, py = (size - round(w * s)) // 2, (size - round(h * s)) // 2
    xy = np.asarray(coords, dtype=float).reshape(-1, 2) * (w * s, h * s) + (px, py)
    return (np.clip(xy / size, 0.0, 1.0)).ravel().tolist()


def letterbox_image(bgr: np.ndarray, size: int, fill: int) -> np.ndarray:
    h, w = bgr.shape[:2]
    s = min(size / w, size / h)
    nw, nh = round(w * s), round(h * s)
    resized = cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), fill, np.uint8)
    px, py = (size - nw) // 2, (size - nh) // 2
    canvas[py:py + nh, px:px + nw] = resized
    return canvas


def polygon_extent(coords: list[float]) -> float:
    """Longer side of a normalised polygon's bounding box (share of the image)."""
    xy = np.asarray(coords, dtype=float).reshape(-1, 2)
    return float(max(xy[:, 0].max() - xy[:, 0].min(), xy[:, 1].max() - xy[:, 1].min()))


def label_quality(records: list, fragment_max: float) -> dict:
    """Section 3.4.3 asks for one polygon per visible span. Polygons much shorter than a span are
    fragments: the model learns to predict fragments, and a fragment gives the catenary fit
    (Section 3.6.5) too short a centerline to measure sag. Reported per class, per image.
    Vegetation is counted but not judged for fragmentation: it has no span."""
    per_class: dict[str, list[float]] = defaultdict(list)
    per_image = []
    for r in records:
        ext = [(CLASSES[k], polygon_extent(c)) for k, c in r.polygons]
        for cls, e in ext:
            per_class[cls].append(e)
        wires = [e for cls, e in ext if cls != VEGETATION]
        if wires:
            per_image.append(sum(e < fragment_max for e in wires) / len(wires))
    summary = {}
    for cls in CLASSES:
        a = np.asarray(per_class.get(cls, []))
        summary[cls] = {"instances": int(a.size)}
        if a.size and cls != VEGETATION:
            summary[cls] |= {"median_extent": round(float(np.median(a)), 3),
                             "fragment_share": round(float((a < fragment_max).mean()), 3)}
    pi = np.asarray(per_image)
    return {"fragment_extent_max": fragment_max, "per_class": summary,
            "images_mostly_fragments": int((pi > 0.5).sum()) if pi.size else 0,
            "instances_per_image_median": float(np.median([len(r.polygons) for r in records])) if records else 0.0,
            "classes_without_examples": [c for c in CLASSES if not per_class.get(c)]}


def image_geometry(records: list) -> dict:
    """Image sizes and letterbox padding. A Stretch export distorts wire shape (it shows as one
    fixed size for every image whatever the camera's aspect ratio); Fit pads with black bars;
    an unresized export keeps each photo's own size. Both of the latter keep the geometry."""
    sizes: Counter = Counter()
    padded = 0
    for r in records:
        g = cv2.imread(str(r.image), cv2.IMREAD_GRAYSCALE)
        if g is None:
            continue
        sizes[(g.shape[1], g.shape[0])] += 1
        b = max(2, min(g.shape) // 100)
        if (g[:b].max() < 8 and g[-b:].max() < 8) or (g[:, :b].max() < 8 and g[:, -b:].max() < 8):
            padded += 1
    n = sum(sizes.values())
    top = sizes.most_common(1)
    return {"images": n, "distinct_sizes": len(sizes),
            "most_common_size": list(top[0][0]) if top else None,
            "most_common_share": round(top[0][1] / n, 3) if n else None,
            "letterboxed": padded,
            "aspect_ratios": {"min": round(min(w / h for w, h in sizes), 3), "max": round(max(w / h for w, h in sizes), 3)}
            if sizes else None}


# ------------------------------------------------------------------ privacy screen

class PrivacyScreen:
    """Face / licence-plate screening aid (Section 3.4.2 step 4, Section 3.14.3).

    * OpenCV 4.x (thesis Table 3.12): bundled Haar cascades for faces and plates.
    * OpenCV 5.x removed CascadeClassifier; YuNet (``FaceDetectorYN``) is used when
      ``WIREFRAME_YUNET`` points to its ONNX model (faces only).
    * With no detector available it fails closed: every image is listed for manual review.

    Detections are blurred and listed for manual confirmation either way; the absence of a
    detection is never treated as proof that an image is free of personal data.
    """

    def __init__(self, yunet_model: str | None = None) -> None:
        import os

        self.detectors: list = []
        self.mode = "manual_only"
        if hasattr(cv2, "CascadeClassifier"):
            for name, mn in (("haarcascade_frontalface_default.xml", 24), ("haarcascade_russian_plate_number.xml", 20)):
                c = cv2.CascadeClassifier(cv2.data.haarcascades + name)
                if not c.empty():
                    self.detectors.append(("haar", c, mn))
            self.mode = "haar" if self.detectors else self.mode
        model = yunet_model or os.environ.get("WIREFRAME_YUNET")
        if not self.detectors and model and hasattr(cv2, "FaceDetectorYN"):
            self.detectors.append(("yunet", cv2.FaceDetectorYN.create(model, "", (320, 320), 0.7), 0))
            self.mode = "yunet_faces_only"

    def __call__(self, bgr: np.ndarray) -> tuple[np.ndarray, int, bool]:
        """Returns (image, detections, needs_manual_review)."""
        boxes = []
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        for kind, det, mn in self.detectors:
            if kind == "haar":
                boxes += [tuple(map(int, b)) for b in det.detectMultiScale(gray, 1.1, 5, minSize=(mn, mn))]
            else:
                det.setInputSize((bgr.shape[1], bgr.shape[0]))
                _, faces = det.detect(bgr)
                boxes += [tuple(map(int, f[:4])) for f in (faces if faces is not None else [])]
        out = bgr.copy()
        for x, y, w, h in boxes:
            x, y = max(0, x), max(0, y)
            roi = out[y : y + h, x : x + w]
            if roi.size:
                k = max(15, (max(w, h) // 3) | 1)
                out[y : y + h, x : x + w] = cv2.GaussianBlur(roi, (k, k), 0)
        return out, len(boxes), bool(boxes) or self.mode != "haar"


# ------------------------------------------------------------------ pipeline

@dataclass
class Record:
    source: str
    image: Path
    label: Path | None
    key: str = ""
    polygons: list[tuple[int, list[float]]] = field(default_factory=list)
    needs_annotation: str | None = None
    privacy_hits: int = 0


def _labels_for(img: Path) -> Path | None:
    cand = img.parent.parent / "labels" / (img.stem + ".txt")
    return cand if cand.exists() else None


def _source_names(src_dir: Path) -> list[str]:
    for name in ("data.yaml", "data.yml"):
        p = src_dir / name
        if p.exists():
            names = yaml.safe_load(p.read_text())["names"]
            return list(names.values()) if isinstance(names, dict) else list(names)
    raise FileNotFoundError(f"{src_dir}: no data.yaml with class names")


def run(raw: Path, out: Path, sources_cfg: Path, class_map_path: Path, enhancement=None,
        imgsz: int | None = None, fill: int = 114) -> dict:
    """``enhancement`` (EnhancementConfig, default: config/wireframe.yaml) is applied to training
    images exactly as the server applies it to field photos, so train and inference match.

    ``imgsz``: write every image letterboxed ("Fit") to imgsz x imgsz with ``fill`` padding
    (114: the value the server and Ultralytics pad with) and transform the polygons with it.
    Aspect ratios are preserved; small photos are scaled up, large ones down. None keeps the
    enhancement working scale (the v2.5 behaviour)."""
    if enhancement is None:
        from app.core.config import ROOT_DIR, load_analysis_config
        enhancement = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml").enhancement
    reg = yaml.safe_load(sources_cfg.read_text())
    cmap = {norm_label(k): v for k, v in yaml.safe_load(class_map_path.read_text()).items()}
    audit: dict = {"steps": []}
    def step(name: str, n: int, **extra) -> None:
        audit["steps"].append({"step": name, "images": n, **extra})

    # 1 licence
    allowed = set(reg["allowed_licenses"])
    records: list[Record] = []
    excluded_sources = []
    for s in reg["sources"]:
        d = raw / s["name"]
        if not d.exists():
            excluded_sources.append({"source": s["name"], "reason": "not downloaded"})
            continue
        if s.get("license") not in allowed:
            excluded_sources.append({"source": s["name"], "reason": f"licence {s.get('license')!r} not allowed"})
            continue
        for img in sorted(p for p in d.rglob("*") if p.suffix.lower() in IMG_EXT and p.parent.name == "images"):
            records.append(Record(s["name"], img, _labels_for(img)))
    step("1_licence", len(records), excluded_sources=excluded_sources)

    # 2 viewpoint (manual exclusion lists)
    excl = set()
    for s in reg["sources"]:
        f = raw / s["name"] / "exclude_viewpoint.txt"
        if f.exists():
            excl |= {(s["name"], ln.strip()) for ln in f.read_text().splitlines() if ln.strip()}
    records = [r for r in records if (r.source, r.image.name) not in excl]
    step("2_viewpoint", len(records), excluded=len(excl))

    # 3 duplicates
    sha_seen: dict[str, Record] = {}
    hashes: dict[str, int] = {}
    kept = []
    n_before, undecodable = len(records), 0
    for r in records:
        data = r.image.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest in sha_seen:
            continue
        sha_seen[digest] = r
        r.key = f"{norm_label(r.source)}__{r.image.stem}"
        g = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
        if g is None:
            undecodable += 1
            continue
        hashes[r.key] = phash(g)
        kept.append(r)
    groups = duplicate_groups(hashes, reg["near_duplicate_hamming"])
    first_in_group: dict[str, Record] = {}
    for r in kept:
        first_in_group.setdefault(groups[r.key], r)
    records = list(first_in_group.values())
    step("3_duplicates", len(records), exact_removed=n_before - len(sha_seen), undecodable=undecodable,
         near_removed=len(kept) - len(records))

    # 5 labels (before privacy so images routed to annotation are not written twice)
    queue = []
    corrupt, unmapped = Counter(), Counter()
    for r in records:
        names = _source_names(raw / r.source)
        kinds = Counter()
        for line in (r.label.read_text().splitlines() if r.label else []):
            try:
                parsed = parse_label_line(line)
            except ValueError:
                parsed, kinds["corrupt"] = None, kinds["corrupt"] + 1
            if parsed is None:
                continue
            k, kind, coords = parsed
            if not 0 <= k < len(names) or min(coords) < -1e-6 or max(coords) > 1 + 1e-6:
                kinds["corrupt"] += 1  # class index outside data.yaml, or coordinates not normalised
                continue
            if norm_label(names[k]) not in cmap:
                unmapped[names[k]] += 1  # not in class_map.yaml: reported, never guessed
                continue
            target = cmap[norm_label(names[k])]
            if target is None:
                continue
            if kind == "box":
                kinds["box"] += 1  # a box is never converted to a polygon
            elif target == "review":
                kinds["review"] += 1
            else:
                r.polygons.append((CLASSES.index(target), coords))
        if kinds:
            r.needs_annotation = ("corrupt_labels" if kinds["corrupt"] else
                                  "needs_polygons" if kinds["box"] else "needs_class_review")
            corrupt[r.key] = kinds["corrupt"]
            queue.append(r)
    trainable = [r for r in records if r.needs_annotation is None]
    step("5_labels", len(trainable), annotation_queue=len(queue),
         corrupt_label_lines=sum(corrupt.values()), unmapped_labels=dict(unmapped))
    audit["label_quality"] = label_quality(trainable, reg.get("fragment_extent_max", 0.25))
    audit["image_geometry"] = image_geometry(trainable)

    # 6 background cap
    wired = [r for r in trainable if r.polygons]
    bg = [r for r in trainable if not r.polygons]
    cap = int(reg["background_fraction_max"] * len(wired) / max(1e-9, 1 - reg["background_fraction_max"]))
    rng = np.random.default_rng(reg["split"]["seed"])
    bg = [bg[i] for i in sorted(rng.permutation(len(bg))[:cap])]
    final = wired + bg
    step("6_background", len(final), background_kept=len(bg))

    # 7 strata + split. Sources marked train_only (every external source unless declared
    # otherwise) never reach val or test, and neither does anything in their duplicate group.
    policy = {s["name"]: s.get("split", "any" if s.get("primary") else "train_only") for s in reg["sources"]}
    ext_groups = {groups[r.key] for r in final if policy.get(r.source) == "train_only"}
    forced = {r.key for r in final if groups[r.key] in ext_groups}
    strata, cut = density_strata({r.key: len(r.polygons) for r in final})
    ratios = {k: reg["split"][k] for k in ("train", "val", "test")}
    assign = grouped_stratified_split({r.key: (groups[r.key], strata[r.key]) for r in final if r.key not in forced},
                                      ratios, reg["split"]["seed"])
    assign |= dict.fromkeys(forced, "train")
    audit["split_isolation"] = {"train_only_images": len(forced),
                                "train_only_in_val_or_test": sum(assign[k] != "train" for k in forced)}

    # write (4 privacy applied while writing)
    if out.exists():
        shutil.rmtree(out)
    hits = []
    screen = PrivacyScreen()
    from app.pipeline.enhance import enhance as enhance_img
    from app.pipeline.preprocess import to_working_scale
    enhanced_counts: Counter = Counter()
    for r in final:
        split = assign[r.key]
        (out / "images" / split).mkdir(parents=True, exist_ok=True)
        (out / "labels" / split).mkdir(parents=True, exist_ok=True)
        bgr = cv2.imread(str(r.image))
        bgr, r.privacy_hits, review = screen(bgr)
        if review:
            hits.append(r.key)
        if enhancement.mode != "off":  # same scale and steps as inference (app/pipeline/preprocess.py)
            rgb = to_working_scale(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), enhancement.working_long_side)
            e = enhance_img(rgb, enhancement)
            enhanced_counts.update(e.applied or ["none"])
            bgr = cv2.cvtColor(e.rgb, cv2.COLOR_RGB2BGR)
        polys = r.polygons
        if imgsz:
            h0, w0 = bgr.shape[:2]
            polys = [(k, letterbox_coords(c, w0, h0, imgsz)) for k, c in polys]
            bgr = letterbox_image(bgr, imgsz, fill)
        cv2.imwrite(str(out / "images" / split / f"{r.key}.jpg"), bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
        (out / "labels" / split / f"{r.key}.txt").write_text(
            "".join(f"{k} " + " ".join(f"{v:.6f}" for v in c) + "\n" for k, c in polys))
    step("4_privacy", len(final), detector=screen.mode, listed_for_manual_review=len(hits))
    audit["enhancement"] = {"mode": enhancement.mode, "working_long_side": enhancement.working_long_side,
                            "steps_applied": dict(enhanced_counts)}

    with open(out / "annotation_queue.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "image", "reason"])
        for r in queue:
            w.writerow([r.source, str(r.image.relative_to(raw)), r.needs_annotation])
    (out / "privacy_review.txt").write_text("\n".join(hits))
    (out / "data.yaml").write_text(yaml.safe_dump({
        # no `path`: Ultralytics then resolves the splits against this file's folder, so the
        # dataset still works when the folder is copied to the training PC
        "train": "images/train", "val": "images/val", "test": "images/test",
        "names": dict(enumerate(CLASSES)),
        # read by ml.train and written into the config, so inference can flag a mismatch
        "wireframe": {"enhancement_mode": enhancement.mode, "working_long_side": enhancement.working_long_side,
                      "imgsz": imgsz, "letterbox_fill": fill if imgsz else None},
    }, sort_keys=False))
    audit["density_tertiles"] = cut
    audit["split_counts"] = {s: sum(1 for v in assign.values() if v == s) for s in ratios}
    audit["strata_by_split"] = {s: dict(Counter(strata[k] for k, v in assign.items() if v == s)) for s in ratios}
    (out / "audit.json").write_text(json.dumps(audit, indent=2))
    return audit


def download(raw: Path, sources_cfg: Path) -> list[dict]:
    """Fetch every fully specified, licence-cleared source from Roboflow into raw/<name>.

    The API key is read from the environment variable named by ``api_key_env``. Sources with
    missing slugs or a licence outside ``allowed_licenses`` are skipped with the reason, so the
    audit log shows exactly what was and was not fetched.
    """
    import os

    reg = yaml.safe_load(sources_cfg.read_text())
    key = os.environ.get(reg.get("api_key_env", "ROBOFLOW_API_KEY"))
    if not key:
        raise SystemExit(f"set {reg.get('api_key_env', 'ROBOFLOW_API_KEY')} to download from Roboflow")
    from roboflow import Roboflow

    rf = Roboflow(api_key=key)
    allowed = set(reg["allowed_licenses"])
    log = []
    for s in reg["sources"]:
        missing = [k for k in ("workspace", "project", "version") if not s.get(k)]
        if missing:
            log.append({"source": s["name"], "status": "skipped", "reason": f"missing {', '.join(missing)}"})
            continue
        if s.get("license") not in allowed:
            log.append({"source": s["name"], "status": "skipped", "reason": f"licence {s.get('license')!r} not allowed"})
            continue
        dest = raw / s["name"]
        if (dest / "data.yaml").exists():
            log.append({"source": s["name"], "status": "cached", "path": str(dest)})
            continue
        dest.mkdir(parents=True, exist_ok=True)
        rf.workspace(s["workspace"]).project(s["project"]).version(int(s["version"])).download(
            reg.get("export_format", "yolov11"), location=str(dest), overwrite=True)
        log.append({"source": s["name"], "status": "downloaded", "path": str(dest)})
    return log


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", type=Path, default=Path("datasets/raw"))
    ap.add_argument("--out", type=Path, default=Path("datasets/wireframe_seg"))
    ap.add_argument("--sources", type=Path, default=Path(__file__).with_name("sources.yaml"))
    ap.add_argument("--class-map", type=Path, default=Path(__file__).with_name("class_map.yaml"))
    ap.add_argument("--download", action="store_true", help="fetch sources from Roboflow first")
    ap.add_argument("--imgsz", type=int, default=1024, help="letterbox (Fit) size; 0 = keep the working scale")
    ap.add_argument("--fill", type=int, default=114, help="letterbox padding grey level (server pads with 114)")
    a = ap.parse_args()
    fetched = download(a.raw, a.sources) if a.download else None
    audit = run(a.raw, a.out, a.sources, a.class_map, imgsz=a.imgsz or None, fill=a.fill)
    if fetched is not None:
        audit["download"] = fetched
        (a.out / "audit.json").write_text(json.dumps(audit, indent=2))
    print(json.dumps(audit, indent=2))


if __name__ == "__main__":
    main()
