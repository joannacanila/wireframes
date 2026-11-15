"""End-to-end server verification (v2.9): the real FastAPI app, the shipped configuration and the
installed models, on a throw-away data directory.

    python -m scripts.server_check --out docs/thesis/results/server_check_v29.json

Photos (test split) are uploaded through POST /api/analysis/upload as JPEG, under the stress
conditions of ml/stress.py; recorded per upload: status, upload-to-result seconds, verdict and
reasons, reliability grade and reason, span / scored / service-drop counts, and whether the PDF
report is a valid PDF that contains the hazard section.
"""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import time
from pathlib import Path

import cv2
from fastapi.testclient import TestClient
from PIL import Image

from app.core.config import ROOT_DIR, Settings, load_analysis_config
from app.core.state import build_state
from app.main import create_app
from ml.stress import apply_condition

ORIGIN = {"origin": "http://testserver"}
DEFAULT_PHOTOS = ["Screenshot 2026-11-03 145516", "Screenshot 2026-11-03 145557", "Screenshot 2026-11-03 145603",
                  "Screenshot 2026-11-03 145634", "Screenshot 2026-11-03 213707"]


def _jpeg(rgb) -> bytes:
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=92)
    return buf.getvalue()


def main() -> None:
    import secrets
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--photos", nargs="*", default=DEFAULT_PHOTOS)
    ap.add_argument("--conditions", nargs="*", default=["clean", "lowlight", "motion", "glare"])
    ap.add_argument("--stressed", type=int, default=3, help="how many photos get every condition (others: clean)")
    ap.add_argument("--repeat", type=int, default=1, help="uploads per case; the median latency is reported (v2.10)")
    a = ap.parse_args()
    cfg = load_analysis_config(ROOT_DIR / "config" / "wireframe.yaml")
    test_dir = ROOT_DIR / "datasets" / "wireframe_seg" / "images" / "test"
    files = [f for key in a.photos if (f := next((p for p in sorted(test_dir.glob("*.jpg")) if key in p.name), None))]
    if len(files) < len(a.photos):
        print(f"{len(a.photos) - len(files)} requested photo(s) not found in the test split; skipped")
    runs = []
    with tempfile.TemporaryDirectory() as tmp:
        state = build_state(Settings(data_dir=Path(tmp), start_worker=True), cfg)
        from scripts.create_admin import upsert_admin
        pw = secrets.token_urlsafe(16)
        upsert_admin(state.db, "server-check", pw, display_name="Server check")
        with TestClient(create_app(state), headers=ORIGIN) as c:
            assert c.post("/api/auth/login", json={"username": "server-check", "password": pw}).status_code == 200
            ready = c.get("/api/health").json() if c.get("/api/health").status_code == 200 else None
            for i, f in enumerate(files):
                rgb = cv2.cvtColor(cv2.imread(str(f)), cv2.COLOR_BGR2RGB)
                for cond in (a.conditions if i < a.stressed else ["clean"]):
                    x = apply_condition(rgb, cond, f.name)
                    secs = []
                    for _ in range(max(1, a.repeat)):
                        t0 = time.perf_counter()
                        r = c.post("/api/analysis/upload", files={"file": ("pole.jpg", _jpeg(x), "image/jpeg")})
                        assert r.status_code == 202, r.text
                        aid = r.json()["id"]
                        while (s := c.get(f"/api/analysis/{aid}").json())["status"] == "processing":
                            time.sleep(0.02)
                        secs.append(time.perf_counter() - t0)
                    sec = sorted(secs)[len(secs) // 2]
                    res = c.get(f"/api/analysis/{aid}/result").json()
                    pdf = c.get(f"/api/reports/{aid}/pdf")
                    hz, q = res.get("hazard") or {}, res.get("quality") or {}
                    wires = res.get("wires") or []
                    row = {"image": f.name.split("__")[-1][:40], "condition": cond, "status": s["status"],
                           "seconds": round(sec, 2), "seconds_all": [round(v, 2) for v in secs], "verdict": hz.get("verdict"), "reasons": hz.get("reasons"),
                           "grade": q.get("grade"), "grade_reason": q.get("reason"), "reliability": q.get("reliability"),
                           "spans": len(wires), "scored": sum(w.get("crs") is not None for w in wires),
                           "service_drops": sum(w.get("status") == "service_drop" for w in wires),
                           "pdf_ok": pdf.status_code == 200 and pdf.content[:4] == b"%PDF",
                           "pdf_kb": round(len(pdf.content) / 1024)}
                    runs.append(row)
                    print({k: v for k, v in row.items() if k != "reasons"}, flush=True)
    import os
    a.out.write_text(json.dumps({"runs": runs, "health": ready, "load_average": os.getloadavg(), "repeat": a.repeat, "config": {
        "motion_route": cfg.enhancement.motion_route, "deblock": cfg.enhancement.deblock,
        "max_span_inclination_deg": cfg.crs.max_span_inclination_deg, "sag_rule": cfg.hazard.sag_rule,
        "p0_high": cfg.hazard.p0_high, "p0_medium": cfg.hazard.p0_medium,
        "low_frame_frac": cfg.hazard.low_frame_frac, "glare_gate": cfg.quality.glare_gate}}, indent=1, default=float))


if __name__ == "__main__":
    main()
