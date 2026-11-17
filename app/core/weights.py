"""Model weight provisioning: a fresh clone downloads its own weights on first start.

The trained weights are too large for the code history, so they live on the repository's `model-weights` branch
and are served from GitHub's public raw-file URLs. On startup the app checks `models/` and, for every required file
that is missing, downloads it in a background thread:

* to `<name>.part`, then verifies the exact size and SHA-256 before an atomic rename, so the model loader never
  sees a partial or corrupted file;
* without blocking the server: until the files arrive the screener reports validation mode, and the existing
  health check (`AppState.model_ready`) picks the models up as soon as they are in place, with no restart.

Settings (environment):
    WIREFRAME_AUTO_DOWNLOAD=0      never download (offline or air-gapped installs; copy the files by hand)
    WIREFRAME_WEIGHTS_BASE_URL     another mirror with the same file names (default: the repository's branch)

Files already present are never re-downloaded or overwritten, even if they differ (a locally retrained model is
the user's choice); only missing files are fetched.

v2.18: every model the shipped configuration uses comes from this repository, nothing from other hubs: the three
WireNets, the strand type head, the pole detector, the YOLO vegetation model, and copies of the two Hugging Face
backbones (DINOv2-small, Apache-2.0; UperNet-ConvNeXt-T ADE20K, MIT) under ``models/hf/``. Files over GitHub's
100 MB per-file limit are stored as numbered parts (``<name>.000``, ``.001``, ...) and joined on download; the
SHA-256 is checked on the joined file.
"""

from __future__ import annotations

import hashlib
import logging
import os
import ssl
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("wireframe.weights")

DEFAULT_BASE_URL = "https://raw.githubusercontent.com/joannacanila/wireframes/model-weights"


@dataclass(frozen=True)
class WeightFile:
    name: str  # path under models/ (may contain a sub-folder, e.g. hf/dinov2-small/model.safetensors)
    size: int
    sha256: str
    parts: int = 1  # > 1: published as <name>.000, <name>.001, ... (GitHub's 100 MB per-file limit)


PART_BYTES = 90 * 2**20  # size of each published part (the last one is shorter)


REQUIRED = (
    WeightFile("wirenet-r34.pt", 97_918_915, "ab2353c796f547ce1bae8e8aa02e26444e93d7d8ca77d991f0587ffe95e67525"),
    WeightFile("wireframe-yolo11m-seg.pt", 20_660_928,
               "1532d7ae9c74acab4bd66d13bd0b55786c950078a8548e1124442df4737d6652"),
    # v2.17 fusion members (models/v217_ensemble.json) and the strand type head
    WeightFile("v212_mitb1.pt", 65_824_959, "f595ef68fa5a6216fef3e094d1d85310893fc3172705f2f4742005c2d174a445"),
    WeightFile("v212_convnext_t.pt", 127_821_839, "9633cee827508919e2f13e28a4859fdb1c0e40393c632d325821cd0d1e596fc8",
               parts=2),
    WeightFile("strand_head_v217.pt", 9_975, "eac91d8473db12a04125b1b5e6a74a7f52b65d942dfc07aa3f1ed214a3a6be9d"),
    WeightFile("strand_head_v217.json", 279_817, "67f6d584d60b86a74a55c34b1a0189732fb689dbe9e2eb7ae0de635626ded3fe"),
    # v2.18 pole detector (context only)
    WeightFile("poles-yolo11n-v218.pt", 5_487_450, "6fe9183a3e9a9cac647bff42a774ea8d59dce495299f3560750581e8425ea457"),
    # Hugging Face backbones, mirrored so the repository alone runs the system
    WeightFile("hf/dinov2-small/config.json", 547, "1809f83e3bdb1609a501a610ad4a742f4fd8ae44d72ca4aa0df52d1f2ac8628d"),
    WeightFile("hf/dinov2-small/model.safetensors", 88_249_960,
               "ae1e99fcefd534ed978cdeb8326f08030c96e28b7a81ffcbc98a857c84d14be1"),
    WeightFile("hf/upernet-convnext-tiny/config.json", 8_755,
               "c7f6a85a6b69d57f363505d5019e0e34f8d7e272f238d3fc88b2fde202b0d279"),
    WeightFile("hf/upernet-convnext-tiny/preprocessor_config.json", 372,
               "6073ac6a37530ed2a19443d6a748f5579887b3ae48f6730073954b0b38cf3fd6"),
    WeightFile("hf/upernet-convnext-tiny/model.safetensors", 241_053_050,
               "cd55b1d36c6602d34f8cd300d7f788ea0fa5c3ed928e9ac98f353ecb31c7fa1d", parts=3),
)


def hf_source(local: str, repo_id: str) -> str:
    """The repository's own copy of a Hugging Face model (``models/hf/<local>``) when it is complete, otherwise the
    hub id (a developer machine with the model already cached)."""
    from app.core.state import MODELS_DIR
    d = MODELS_DIR / "hf" / local
    need = [w for w in REQUIRED if w.name.startswith(f"hf/{local}/")]
    return str(d) if need and all((MODELS_DIR / w.name).exists() for w in need) else repo_id

RETRY_DELAYS_S = (5, 20, 60, 120, 300)  # a fresh install often starts before the network is ready; slow links stall


def _ssl_context() -> ssl.SSLContext:
    """certifi's CA bundle when installed (python.org builds of Python on macOS ship without root certificates,
    so the system default fails with CERTIFICATE_VERIFY_FAILED); the system store otherwise."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_lock = threading.Lock()
_status: dict[str, str] = {}  # file name -> "present" | "downloading" | "ready" | "failed: …"


def status() -> dict[str, str]:
    with _lock:
        return dict(_status)


def _set(name: str, value: str) -> None:
    with _lock:
        _status[name] = value


def auto_download_enabled() -> bool:
    return os.environ.get("WIREFRAME_AUTO_DOWNLOAD", "1").strip().lower() not in ("0", "false", "no", "off")


def missing(models_dir: Path) -> list[WeightFile]:
    return [w for w in REQUIRED if not (models_dir / w.name).exists()]


def fetch(w: WeightFile, models_dir: Path, base_url: str | None = None, timeout: float = 120.0) -> Path:
    """Download one weight file, verify size and SHA-256, and move it into place atomically."""
    base = (base_url or os.environ.get("WIREFRAME_WEIGHTS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    dest = models_dir / w.name
    part = models_dir / f"{w.name}.part"
    dest.parent.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    urls = [f"{base}/{w.name}"] if w.parts == 1 else [f"{base}/{w.name}.{i:03d}" for i in range(w.parts)]
    try:
        with open(part, "wb") as out:
            for url in urls:
                req = urllib.request.Request(url, headers={"User-Agent": "wireframe-weights/1"})
                with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
                    while chunk := resp.read(1 << 20):
                        out.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
        if size != w.size:
            raise ValueError(f"size {size} bytes, expected {w.size}")
        if digest.hexdigest() != w.sha256:
            raise ValueError("SHA-256 does not match the published checksum")
        os.replace(part, dest)  # atomic: the loader sees the complete file or nothing
        return dest
    finally:
        part.unlink(missing_ok=True)


def ensure(models_dir: Path, base_url: str | None = None, retry_delays: tuple[float, ...] = ()) -> list[str]:
    """Download every missing required file (blocking), retrying after each delay in ``retry_delays``.
    Returns the names that still failed; never raises. A checksum mismatch is not retried."""
    failed = []
    for w in REQUIRED:
        if (models_dir / w.name).exists():
            _set(w.name, "present")
            continue
        for attempt, delay in enumerate((0.0, *retry_delays)):
            if delay:
                time.sleep(delay)
            _set(w.name, "downloading" if not attempt else f"downloading (retry {attempt})")
            log.info("downloading model weights %s (%.0f MB)", w.name, w.size / 2**20)
            try:
                fetch(w, models_dir, base_url)
                _set(w.name, "ready")
                log.info("model weights %s verified and installed", w.name)
                break
            except ValueError as exc:  # size or checksum mismatch: the mirror is wrong, retrying will not help
                _set(w.name, f"failed: {exc}")
                break
            except Exception as exc:  # network down, DNS, TLS, timeout
                _set(w.name, f"failed: {exc}")
        if not (models_dir / w.name).exists():
            log.warning("could not download %s: %s (place it in %s by hand, or set WIREFRAME_WEIGHTS_BASE_URL)",
                        w.name, status().get(w.name), models_dir)
            failed.append(w.name)
    return failed


def ensure_in_background(models_dir: Path) -> threading.Thread | None:
    """Start the download thread when something is missing and auto-download is on; otherwise do nothing."""
    if not auto_download_enabled() or not missing(models_dir):
        for w in REQUIRED:
            _set(w.name, "present" if (models_dir / w.name).exists() else "missing (auto-download off)")
        return None
    t = threading.Thread(target=ensure, args=(models_dir, None, RETRY_DELAYS_S), name="wireframe-weights", daemon=True)
    t.start()
    return t
