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
    name: str
    size: int
    sha256: str


REQUIRED = (
    WeightFile("wirenet-r34.pt", 97_918_915, "ab2353c796f547ce1bae8e8aa02e26444e93d7d8ca77d991f0587ffe95e67525"),
    WeightFile("wireframe-yolo11m-seg.pt", 20_660_928,
               "1532d7ae9c74acab4bd66d13bd0b55786c950078a8548e1124442df4737d6652"),
)

RETRY_DELAYS_S = (5, 20, 60)  # a fresh install often starts before the network or proxy is ready


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


def fetch(w: WeightFile, models_dir: Path, base_url: str | None = None, timeout: float = 60.0) -> Path:
    """Download one weight file, verify size and SHA-256, and move it into place atomically."""
    base = (base_url or os.environ.get("WIREFRAME_WEIGHTS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    dest = models_dir / w.name
    part = models_dir / f"{w.name}.part"
    models_dir.mkdir(parents=True, exist_ok=True)
    digest, size = hashlib.sha256(), 0
    req = urllib.request.Request(f"{base}/{w.name}", headers={"User-Agent": "wireframe-weights/1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp, open(part, "wb") as out:
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
