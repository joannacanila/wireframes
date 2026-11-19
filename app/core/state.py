"""Application container shared by routes (attached to ``app.state.wf``)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Request

from app.core.config import ROOT_DIR, AnalysisConfig, Settings
from app.core.database import Database
from app.core.security import SlidingWindow
from app.pipeline.runner import Segmenter
from app.pipeline.wirenet import make_segmenter
from app.services import analysis as svc
from app.services.queue import AnalysisQueue

MODELS_DIR = ROOT_DIR / "models"


@lru_cache(maxsize=32)
def _sha256(path: str, mtime_ns: int, size: int) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def model_identity(seg_cfg) -> dict[str, Any]:
    """S13: the checkpoints the segmenter is configured to use, with their SHA-256, and one short fingerprint over
    them. Stored with every analysis so a result traces to its exact weights, not only to its parameters (the config
    fingerprint does not change when a checkpoint file or the admin's active checkpoint changes)."""
    def resolve(p: str | None) -> Path | None:
        if not p:
            return None
        q = Path(p)
        return q if q.is_absolute() else ROOT_DIR / q

    files: list[Path] = []
    for p in (seg_cfg.weights, seg_cfg.pole_weights, getattr(seg_cfg, "strand_head", None)):
        if (q := resolve(p)) is not None:
            files.append(q)
    if (ens := resolve(seg_cfg.wirenet_weights)) is not None:
        if ens.suffix == ".json" and ens.exists():
            files += [resolve(m["weights"]) for m in json.loads(ens.read_text("utf-8"))["members"]]
        else:
            files.append(ens)
    checkpoints: dict[str, str | None] = {}
    for f in files:
        try:
            st = f.resolve().stat()
            checkpoints[f.name] = _sha256(str(f.resolve()), st.st_mtime_ns, st.st_size)[:16]
        except OSError:
            checkpoints[f.name] = None  # missing: the load error is reported separately
    blob = json.dumps(checkpoints, sort_keys=True).encode()
    return {"fingerprint": hashlib.sha256(blob).hexdigest()[:12], "checkpoints": checkpoints,
            "tta": getattr(seg_cfg, "wirenet_tta", None), "device": getattr(seg_cfg, "device", None)}


@dataclass
class AppState:
    settings: Settings
    config: AnalysisConfig
    db: Database
    segmenter: Segmenter
    queue: AnalysisQueue = field(init=False)
    login_limiter: SlidingWindow = field(default_factory=lambda: SlidingWindow(5, 15 * 60))
    upload_limiter: SlidingWindow = field(init=False)

    def __post_init__(self) -> None:
        self.queue = AnalysisQueue(self.db, self._job)
        self.upload_limiter = SlidingWindow(self.settings.public_uploads_per_window, 15 * 60)

    def _job(self, analysis_id: str) -> str:
        return svc.process(analysis_id, self.db, self.settings, self.config, self.segmenter)

    # ------------------------------------------------------------- runtime settings

    def get_setting(self, key: str, default: Any = None) -> Any:
        from app.models.orm import AppSetting

        with self.db.session() as s:
            row = s.get(AppSetting, key)
            return default if row is None else row.value

    def put_setting(self, key: str, value: Any, by: str) -> None:
        from app.models.orm import AppSetting

        with self.db.session() as s:
            row = s.get(AppSetting, key)
            if row is None:
                s.add(AppSetting(key=key, value=value, updated_by=by))
            else:
                row.value, row.updated_by = value, by

    @property
    def require_reference(self) -> bool:
        return bool(self.get_setting("require_reference_masks", False))

    def model_ready(self) -> bool:
        loader = getattr(self.segmenter, "load", None)
        return bool(loader() if loader else getattr(self.segmenter, "ready", True))

    def activate_weights(self, name: str) -> tuple[bool, str | None]:
        """Swap the segmentation checkpoint. The new one must load before it replaces the old."""
        path = (MODELS_DIR / name).resolve()
        if path.parent != MODELS_DIR.resolve() or path.suffix != ".pt":
            return False, "checkpoint must be a .pt file inside models/"
        seg_cfg = self.config.segmentation.model_copy(update={"weights": str(path)})
        cand = make_segmenter(seg_cfg, ROOT_DIR)
        if not cand.load():
            return False, cand.error
        cand.identity = model_identity(seg_cfg)
        self.segmenter = cand
        return True, None


def build_state(settings: Settings, config: AnalysisConfig, segmenter: Segmenter | None = None) -> AppState:
    settings.ensure_dirs()
    db = Database(settings.resolved_database_url)
    db.create_all()
    from app.services import records
    records.ROW_CFG = config  # S14: the operational status uses the configured comparison rule
    from app.services import contacts  # S14: built-in directory rows, once; administrators own them afterwards
    with db.session() as s:
        contacts.seed(s)
        contacts.upgrade_seed(s)  # S18: researched provider rows (once); unedited S14 rows corrected
        from app.services.review_flow import load_rubric
        load_rubric(s)  # S18: the operational triage rubric in force
    seg = segmenter or make_segmenter(config.segmentation, ROOT_DIR)
    if segmenter is None:
        seg.identity = model_identity(config.segmentation)
    st = AppState(settings, config, db, seg)
    active = st.get_setting("active_weights")
    if segmenter is None and active and (MODELS_DIR / active).exists():
        st.activate_weights(active)
    return st


def get_state(request: Request) -> AppState:
    return request.app.state.wf


def list_checkpoints() -> list[Path]:
    return sorted(MODELS_DIR.glob("*.pt")) if MODELS_DIR.exists() else []
