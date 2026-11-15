"""Upload check against a RUNNING server over real HTTP (v2.10).

    python -m scripts.live_upload_check --base http://127.0.0.1:8000 --out docs/thesis/results/live_upload_check_v210.json

Posts multipart/form-data uploads to /api/analysis/upload (anonymous field upload, like the web UI),
polls the status, fetches the JSON result and the PDF report, and fails if any step does not return
the expected HTTP status. Cases: the demo samples in runs/demo_samples/ (if present) and generated
edge cases (64x64 thumbnail, 4000x3000 RGBA PNG, greyscale PNG, blank image). Each upload is tagged
with a pole code QA-LIVE-<n> so it is easy to find (and delete) in the admin portal.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path

import httpx
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def _png(img: Image.Image, **kw) -> bytes:
    b = io.BytesIO()
    img.save(b, "PNG", **kw)
    return b.getvalue()


def edge_cases() -> dict[str, tuple[str, bytes]]:
    rng = np.random.default_rng(0)
    scene = np.full((3000, 4000, 3), 200, np.uint8)
    scene[:1500] = 230
    for y in range(300, 1500, 120):
        scene[y: y + 6] = 35
    scene = np.clip(scene.astype(int) + rng.integers(-5, 6, scene.shape), 0, 255).astype(np.uint8)
    rgba = Image.fromarray(scene).convert("RGBA")
    small = Image.fromarray(scene[::47, ::63][:64, :64])
    return {"tiny_64x64.jpg": ("image/jpeg", _jpeg(small)),
            "huge_4000x3000_rgba.png": ("image/png", _png(rgba, compress_level=1)),
            "grayscale.png": ("image/png", _png(Image.fromarray(scene[::4, ::4]).convert("L"))),
            "blank.png": ("image/png", _png(Image.new("RGB", (1024, 768), (128, 128, 128))))}


def _jpeg(img: Image.Image) -> bytes:
    b = io.BytesIO()
    img.save(b, "JPEG", quality=92)
    return b.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    cases: dict[str, tuple[str, bytes]] = {}
    demo = ROOT / "runs" / "demo_samples"
    for p in sorted(demo.glob("*")) if demo.exists() else []:
        cases[p.name] = ("image/png" if p.suffix == ".png" else "image/jpeg", p.read_bytes())
    cases.update(edge_cases())
    rows, ok = [], True
    with httpx.Client(base_url=a.base, timeout=120, headers={"origin": a.base}) as c:
        for i, (name, (ctype, data)) in enumerate(cases.items(), 1):
            t0 = time.perf_counter()
            r = c.post("/api/analysis/upload", files={"file": (name, data, ctype)},
                       data={"pole_code": f"QA-LIVE-{i}"})
            row = {"case": name, "bytes": len(data), "upload_http": r.status_code}
            if r.status_code != 202:
                row["error"] = r.text[:300]
                ok = False
                rows.append(row)
                print(row, flush=True)
                continue
            aid = r.json()["id"]
            while (s := c.get(f"/api/analysis/{aid}").json())["status"] == "processing":
                time.sleep(0.1)
            res = c.get(f"/api/analysis/{aid}/result")
            pdf = c.get(f"/api/reports/{aid}/pdf")
            body = res.json() if res.status_code == 200 else {}
            row.update({"analysis_id": aid, "status": s["status"], "seconds": round(time.perf_counter() - t0, 2),
                        "result_http": res.status_code, "pdf_http": pdf.status_code,
                        "pdf_valid": pdf.content[:4] == b"%PDF", "verdict": (body.get("hazard") or {}).get("verdict"),
                        "grade": (body.get("quality") or {}).get("grade"),
                        "reason": (body.get("quality") or {}).get("reason"),
                        "analysed_size": [body.get("acquisition", {}).get("metadata", {}).get(k) for k in ("width", "height")],
                        "flags": [f for f in body.get("flags", []) if f in ("downscaled", "resolution_too_low", "blank_image")]})
            good = (row["result_http"] == 200 and row["pdf_http"] == 200 and row["pdf_valid"]
                    and s["status"] != "error" and row["verdict"])
            ok &= bool(good)
            rows.append(row)
            print({k: v for k, v in row.items() if k != "analysis_id"}, flush=True)
    if a.out:
        a.out.write_text(json.dumps({"base": a.base, "all_ok": ok, "runs": rows}, indent=1))
    print("ALL OK" if ok else "FAILURES", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
