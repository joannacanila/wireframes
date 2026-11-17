"""Runtime settings and the validated analysis-parameter configuration.

Two layers:

* ``Settings``: deployment concerns (paths, host, database URL) from environment
  variables prefixed ``WIREFRAME_``.
* ``AnalysisConfig``: the thesis parameters (weights, limits, quantiles) loaded from
  ``config/wireframe.yaml`` and validated, so that an inconsistent file (weights that
  do not sum to one, q99 <= q01, ...) is rejected at startup instead of producing
  silently wrong scores.
"""

from __future__ import annotations

import hashlib
import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]

# Revised taxonomy (2026-11-14), in the dataset's class-index order. The names are the
# team dataset's own, so a trained checkpoint's `names` match them without a mapping.
#   telecom-cable   standard telecom lines and fiber optic drops (formerly two classes)
#   abandoned-wire  replaces dead_wire
#   Vegetation      not a wire: never fitted or scored for sag (Section 3.6.5); used only for
#                   the vegetation-contact flag on wires it touches
VEGETATION = "Vegetation"
CLASSES: tuple[str, ...] = (VEGETATION, "abandoned-wire", "power-distribution-lines", "telecom-cable")
WIRE_CLASSES: tuple[str, ...] = tuple(c for c in CLASSES if c != VEGETATION)  # sag-scored classes
# Earlier names, for migrating stored analyses and mapping older source labels.
LEGACY_CLASS_NAMES = {"power_distribution_line": "power-distribution-lines", "telecom_cable": "telecom-cable",
                      "fiber_optic_drop": "telecom-cable", "dead_wire": "abandoned-wire"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIREFRAME_", env_file=".env", extra="ignore")

    data_dir: Path = ROOT_DIR / "var"
    config_path: Path = ROOT_DIR / "config" / "wireframe.yaml"
    database_url: str | None = None
    max_upload_mb: int = 64  # v2.10: 25-50 MB phone photos (HEIF-to-JPEG exports, 50 MP sensors)
    public_uploads_per_window: int = 20  # anonymous uploads per client per 15 minutes
    cookie_secure: bool = False  # set True behind HTTPS (WIREFRAME_COOKIE_SECURE=1)
    log_level: str = "INFO"
    start_worker: bool = True

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def render_dir(self) -> Path:
        return self.data_dir / "renders"

    @property
    def report_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'wireframe.db'}"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.upload_dir, self.render_dir, self.report_dir):
            d.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- parameters


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _range(v: list[float]) -> tuple[float, float]:
    if len(v) != 2 or v[0] > v[1]:
        raise ValueError("expected [low, high] with low <= high")
    return float(v[0]), float(v[1])


class AcquisitionConfig(_Strict):
    provisional: bool = True
    focal_length_35mm: tuple[float, float]
    distance_m: tuple[float, float]
    elevation_deg: tuple[float, float]
    daylight_hours: tuple[float, float]
    min_side_px: int = Field(gt=0)
    # v2.10 ingestion: long-edge cap for analysis and storage (0 = none); with reject_low_quality False a
    # too-small or blank photo is accepted and flagged, and its verdict becomes INCONCLUSIVE (quality grade)
    max_side_px: int = Field(2048, ge=0)
    reject_low_quality: bool = False

    _v = field_validator("focal_length_35mm", "distance_m", "elevation_deg", "daylight_hours",
                         mode="before")(_range)


class PreprocessingConfig(_Strict):
    size: int = Field(640, gt=31)
    letterbox_fill: int = Field(114, ge=0, le=255)
    gaussian_kernel: int = 5
    gaussian_smoothing: bool = True

    @field_validator("gaussian_kernel")
    @classmethod
    def _odd(cls, v: int) -> int:
        if v < 1 or v % 2 == 0:
            raise ValueError("Gaussian kernel size must be a positive odd integer")
        return v


class Band(_Strict):
    label: str
    max: float


class VNIConfig(_Strict):
    provisional: bool = True
    radius_factor: float = Field(0.15, gt=0, lt=1)
    remove_mean: bool = False
    q01: float = Field(ge=0, le=1)
    q99: float = Field(ge=0, le=1)
    calibration_set: str | None = None
    bands: tuple[Band, ...]

    @model_validator(mode="after")
    def _check(self) -> VNIConfig:
        if self.q99 <= self.q01:
            raise ValueError("vni.q99 must be greater than vni.q01")
        maxes = [b.max for b in self.bands]
        if maxes != sorted(maxes) or not math.isclose(maxes[-1], 1.0):
            raise ValueError("vni.bands must be ascending and end at 1.0")
        return self


class EnhancementConfig(_Strict):
    """Adaptive enhancement of the segmentation input (app/pipeline/enhance.py). Thresholds
    are provisional: they decide *whether* a step runs and are to be checked on field photos."""
    provisional: bool = True
    mode: Literal["off", "auto", "always"] = "auto"
    working_long_side: int = Field(640, ge=320, le=4096)
    clahe_clip_limit: float = Field(2.0, gt=0)
    clahe_tiles: int = Field(8, ge=2, le=32)
    denoise_sigma_min: float = Field(6.0, gt=0)  # estimated noise sigma (grey levels)
    denoise_strength: float = Field(0.9, gt=0)
    dark_l_max: float = Field(35.0, ge=0, le=100)  # mean CIELAB L
    low_contrast_max: float = Field(12.0, ge=0, le=100)  # std of L
    overexposed_frac_min: float = Field(0.25, ge=0, le=1)  # share of L > 97
    blur_laplacian_max: float = Field(60.0, ge=0)  # variance of Laplacian
    unsharp_sigma: float = Field(1.0, gt=0)
    unsharp_amount: float = Field(0.6, ge=0, le=3)       # at the blur threshold
    unsharp_amount_max: float = Field(1.5, ge=0, le=3)   # reached as sharpness falls towards 0
    unsharp_threshold: float = Field(4.0, ge=0)
    # Blur fallback (runner): a second, stronger pass when a degraded photo yields no or broken spans
    fallback: bool = True
    fallback_unscored_share: float = Field(0.5, gt=0, le=1)  # share of unscored wires that counts as broken
    fallback_clahe_clip_limit: float = Field(3.0, gt=0)
    fallback_unsharp_amount: float = Field(1.2, ge=0, le=3)
    fallback_tta_hflip: bool = True                      # also segment the mirrored photo and merge
    # v2.8 candidates (off unless they won the validation stress benchmark; see Chapter 4 v2.8)
    retinex: bool = False                                # multi-scale Retinex on L* for dark / glare photos
    retinex_dark_l_max: float = Field(40.0, ge=0, le=100)
    retinex_glare_frac_min: float = Field(0.15, ge=0, le=1)
    edge_denoise: bool = False                           # bilateral filter for moderate noise / JPEG blocking
    edge_denoise_sigma_min: float = Field(3.0, gt=0)
    jpeg_blockiness_min: float = Field(1.3, gt=0)        # 8 px boundary / interior gradient ratio
    # v2.9 routing (off unless they won the validation stress benchmark; see Chapter 4 v2.9)
    motion_route: bool = False                           # directional blur -> smoothing, no unsharp
    motion_coherence_min: float = Field(0.16, ge=0, le=1)
    motion_action: Literal["smooth", "skip"] = "smooth"  # bilateral smoothing, or just no unsharp
    motion_fallback_unsharp: bool = False                # fallback pass of a motion photo still sharpens
    deblock: bool = False                                # 8 px boundary filter for heavy JPEG
    deblock_blockiness_min: float = Field(1.3, gt=0)
    deblock_alpha: float = Field(12.0, gt=0)
    deblock_beta: float = Field(4.0, gt=0)


class SegmentationConfig(_Strict):
    weights: str
    imgsz: int = 640
    iou_nms: float = Field(0.45, gt=0, lt=1)
    conf_min: float = Field(0.25, ge=0, lt=1)
    conf_provisional: bool = True
    trained_enhancement: Literal["off", "auto", "always"] | None = None  # set by ml.train
    mask_threshold: float = Field(0.5, gt=0, lt=1)
    device: str = "auto"
    # Full-resolution wire refinement (app/pipeline/wirenet.py); None = YOLO masks only
    wirenet_weights: str | None = None
    wirenet_dilate_frac: float = Field(0.006, gt=0, le=0.05)  # coarse-mask dilation x longer image side
    # where wire spans come from: "skeleton" (WireNet class map, app/pipeline/wire_instances.py)
    # or "yolo" (YOLO proposals refined by WireNet pixels). Vegetation always comes from YOLO.
    wirenet_instances: Literal["skeleton", "yolo"] = "skeleton"
    wirenet_tta: Literal["none", "flip", "flip_ms"] = "none"  # WireNet test-time augmentation (v2.8)
    # v2.11 dual polarity: for a dark photo (mean L* of the photo content <= dual_polarity_max_mean_l) WireNet
    # also runs on the photo with L* inverted (street-lit conductors are lighter than a night sky; the
    # training photos show dark wires on a bright sky). The inverted pass is used when it finds at least
    # dual_polarity_ratio times the wire pixels. Chosen on validation, Chapter 4 v2.11.
    dual_polarity: bool = False
    dual_polarity_max_mean_l: float = Field(40.0, ge=0, le=100)
    dual_polarity_ratio: float = Field(1.2, gt=0)
    # v2.13 ground gate (app/pipeline/scene.py): a wire span with >= ground_gate_share of its pixels on ADE20K
    # ground classes (road, sidewalk, floor, ...) is a kerb / manhole / pavement edge, not an overhead conductor.
    # It is not scored; it is listed with the reason. Chapter 4 v2.13.
    ground_gate: bool = False
    ground_gate_share: float = Field(0.5, gt=0, le=1)
    pole_weights: str | None = None                    # v2.16: YOLO pole detector (context only); null = off
    pole_conf: float = Field(0.35, gt=0, lt=1)
    pole_occluder_gap: float = Field(0.0, ge=0)        # v2.16: bridge gap allowed across a detected pole (0 = off)
    structure_gate: bool = False                       # v2.16: drop spans running along a structure outline
    structure_gate_contrast: float = Field(0.6, gt=0, le=1)
    vegetation_gate: bool = False                      # v2.16: drop YOLO vegetation not on ADE20K vegetation
    vegetation_gate_min_share: float = Field(0.4, ge=0, le=1)
    # span extraction (app/pipeline/wire_instances.py); defaults = v2.7 behaviour
    span_min_px: int = Field(30, ge=1)
    span_min_len: int = Field(30, ge=1)
    span_max_turn_deg: float = Field(25.0, gt=0, le=90)
    span_cluster_radius: int = Field(10, ge=0)
    span_max_gap: float = Field(0.0, ge=0)             # collinear gap bridging; 0 = off
    span_gap_angle_deg: float = Field(15.0, gt=0, le=90)
    span_draw_bridge: bool = True
    span_min_stitched_len: int = Field(0, ge=0)        # minimum stitched span length (skeleton px)
    span_own_component: bool = False                   # v2.14: pixels join only spans in their own mask component
    span_max_lateral_px: float = Field(0.0, ge=0)     # v2.14 option: reject a bridge offset sideways by more (0 = off)
    span_strand_classes: bool = False                  # v2.16: geometry on the union wire mask, one class per strand
    span_class_min_share: float = Field(0.0, ge=0, le=1)  # v2.16: below this class-evidence share: type uncertain
    span_wire_tau: float | None = Field(None, gt=0, lt=1)  # v2.17: wire mask = P(any wire) >= tau (None: argmax)
    strand_head: str | None = None                     # v2.17: strand type head (models/strand_head_v217.pt)
    strand_head_min_conf: float = Field(0.55, ge=0.5, le=1)  # v2.17: head top-class probability below: uncertain


class CenterlineConfig(_Strict):
    provisional: bool = True
    l_min_px: int = Field(30, ge=0)
    n_min_points: int = Field(20, ge=4)


class CatenaryConfig(_Strict):
    provisional: bool = True
    max_rel_residual: float = Field(gt=0)
    border_margin_frac: float = Field(ge=0, lt=0.5)
    max_function_evals: int = Field(4000, gt=0)


class Levels(_Strict):
    low_below: float = 3.0
    high_above: float = 6.0

    @model_validator(mode="after")
    def _order(self) -> Levels:
        if not 0 <= self.low_below <= self.high_above <= 10:
            raise ValueError("CRS level cut-offs must satisfy 0 <= low_below <= high_above <= 10")
        return self


def _all_classes(v: dict[str, float]) -> dict[str, float]:
    missing = set(WIRE_CLASSES) - set(v)
    extra = set(v) - set(WIRE_CLASSES)
    if missing or extra:
        raise ValueError(f"expected exactly the wire classes {list(WIRE_CLASSES)} (missing={missing}, extra={extra})")
    return v


class CRSConfig(_Strict):
    provisional: bool = True
    class_weights: dict[str, float]
    delta_lim0: dict[str, float]
    levels: Levels
    # v2.8 measurement-precision gate: a span gets a CRS only if its chord L satisfies
    # sigma / L <= f * delta_lim (sigma = skeleton noise, px). 0 disables the gate.
    min_chord_px: float = Field(0.0, ge=0)
    # v2.9: chords steeper than this (deg from the image horizontal) are service drops / guy
    # wires, not pole-to-pole spans: no sag score (see Chapter 4 v2.9). 0 disables the split.
    max_span_inclination_deg: float = Field(0.0, ge=0, le=90)

    _v = field_validator("class_weights", "delta_lim0")(_all_classes)

    @field_validator("delta_lim0")
    @classmethod
    def _positive(cls, v: dict[str, float]) -> dict[str, float]:
        if any(x <= 0 for x in v.values()):
            raise ValueError("delta_lim0 values must be positive")
        return v


class Conductor(_Strict):
    weight_n_per_m: float = Field(gt=0)
    diameter_m: float = Field(gt=0)
    area_m2: float = Field(gt=0)
    elastic_modulus_pa: float = Field(gt=0)
    thermal_expansion_per_c: float = Field(ge=0)
    reference_tension_n: float = Field(gt=0)


class EngineeringConfig(_Strict):
    provisional: bool = True
    reference_temperature_c: float = 25.0
    air_density: float = Field(1.225, gt=0)
    drag_coefficient: float = Field(1.0, gt=0)
    conductors: dict[str, Conductor]

    _v = field_validator("conductors")(_all_classes)


class HazardConfig(_Strict):
    """Explainable verdict layer (app/pipeline/hazard.py). Rules, not learned thresholds."""
    enabled: bool = False
    veg_moderate_px: int = Field(15, ge=0)
    loose_ends: bool = False
    loose_end_tau: float = Field(0.5, ge=0, le=1)
    border_px: int = Field(12, ge=0)
    isolation_px: int = Field(8, ge=0)
    cluster_px: int = Field(25, ge=0)
    cluster_n: int = Field(2, ge=1)
    # v2.9 pole-level sag aggregation. "any": one span in a band sets the verdict (v2.8).
    # "corroborated": the count of spans in the band must exceed what the per-span test-retest
    # false-alarm rate p0 explains (one-sided binomial test at level alpha), see hazard.py.
    sag_rule: Literal["any", "corroborated"] = "any"
    p0_high: float = Field(0.05, gt=0, lt=1)
    p0_medium: float = Field(0.10, gt=0, lt=1)
    alpha: float = Field(0.05, gt=0, lt=1)
    # service drops reaching the lowest share of the frame: a framing proxy for a low-hanging
    # wire over a footpath (no height is measured). 0 disables.
    low_frame_frac: float = Field(0.0, ge=0, lt=1)


class QualityConfig(_Strict):
    """Reliability grade thresholds on R = 1 - (U + D)/2 (app/pipeline/quality.py); calibrated on
    the validation stress set, see Chapter 4 v2.8."""
    enabled: bool = False
    grade_a: float = Field(0.80, ge=0, le=1)
    grade_b: float = Field(0.70, ge=0, le=1)
    grade_c: float = Field(0.60, ge=0, le=1)
    min_wire_pixels: int = Field(500, ge=0)
    min_mean_l: float = Field(8.0, ge=0, le=100)
    max_clipped: float = Field(0.85, ge=0, le=1)
    # v2.10 glare gate: a photo that is mostly near-white (L* > 90 share >= glare_bright_min) AND whose
    # thin dark lines stand out by less than glare_line_contrast_max L* units is unreadable for wires,
    # however confident the model is ("confident absence"). Chosen on validation, Chapter 4 v2.10.
    glare_gate: bool = False
    glare_bright_min: float = Field(0.6, ge=0, le=1)
    glare_line_contrast_max: float = Field(15.0, ge=0)
    # v2.10 ingestion: photos below the Figure 3.6 minimum side, or blank (std of L* below min_contrast), are
    # accepted and graded Retake (INCONCLUSIVE) instead of being rejected at upload
    min_side_px: int = Field(320, ge=0)
    min_contrast: float = Field(1.0, ge=0)


class GuardrailConfig(_Strict):
    """v2.17 retake guardrail (app/pipeline/guardrail.py). Reads the finished analysis; never changes a wire, class,
    CRS or verdict. Thresholds chosen on validation + its eight capture conditions (ml/experiments/v217_guardrail.py)."""
    enabled: bool = False
    min_wires: int = Field(4, ge=1)                       # share-based checks need at least this many wires
    conflict_min_share: float = Field(0.3, ge=0, le=0.5)  # second wire type's evidence share that makes a conflict
    conflict_retake_n: int = Field(2, ge=1)
    uncertain_attention: float = Field(0.25, ge=0, le=1)
    uncertain_retake: float = Field(0.5, ge=0, le=1)
    fragment_attention: float = Field(0.5, ge=0, le=1)
    fragment_retake: float = Field(0.75, ge=0, le=1)
    structure_attention: float = Field(0.25, ge=0, le=1)
    structure_retake: float = Field(0.5, ge=0, le=1)


class VegetationConfig(_Strict):
    """Vegetation contact (revised taxonomy). Not part of Chapter III's equations: Vegetation
    masks never enter Algorithms 3-5, CRS or MSPI. A wire whose mask lies within the margin of a
    Vegetation mask is flagged `vegetation_contact` for the inspector to judge."""
    provisional: bool = True
    contact_margin_frac: float = Field(0.01, ge=0, le=0.2)    # x the image's longer side
    min_contact_share: float = Field(0.02, gt=0, le=1)        # share of the wire's mask pixels


class MSPIConfig(_Strict):
    provisional: bool = True
    alpha: float = Field(ge=0, le=1)
    beta: float = Field(ge=0, le=1)
    gamma: float = Field(ge=0, le=1)
    sensitivity_step: float = Field(0.1, gt=0, le=0.5)

    @model_validator(mode="after")
    def _sum(self) -> MSPIConfig:
        if not math.isclose(self.alpha + self.beta + self.gamma, 1.0, abs_tol=1e-9):
            raise ValueError("mspi alpha + beta + gamma must equal 1 (Equation 11)")
        return self


class AnalysisConfig(_Strict):
    version: int
    acquisition: AcquisitionConfig
    preprocessing: PreprocessingConfig
    enhancement: EnhancementConfig = EnhancementConfig()
    vni: VNIConfig
    segmentation: SegmentationConfig
    centerline: CenterlineConfig
    catenary: CatenaryConfig
    crs: CRSConfig
    engineering: EngineeringConfig
    mspi: MSPIConfig
    vegetation: VegetationConfig = VegetationConfig()
    hazard: HazardConfig = HazardConfig()
    quality: QualityConfig = QualityConfig()
    guardrail: GuardrailConfig = GuardrailConfig()

    @property
    def fingerprint(self) -> str:
        """Short hash stored with every analysis so results trace to their parameters."""
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()[:12]

    def provisional_blocks(self) -> list[str]:
        names = []
        for name in ("acquisition", "enhancement", "vni", "centerline", "catenary", "crs", "engineering", "mspi", "vegetation"):
            if getattr(getattr(self, name), "provisional", False):
                names.append(name)
        if self.segmentation.conf_provisional:
            names.append("segmentation.conf_min")
        return names


def load_analysis_config(path: Path | str) -> AnalysisConfig:
    with open(path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return AnalysisConfig.model_validate(raw)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_analysis_config() -> AnalysisConfig:
    return load_analysis_config(get_settings().config_path)


def update_config_values(path: Path | str, updates: dict[str, dict[str, object]]) -> None:
    """Set ``section.key`` values in config/wireframe.yaml *in place*, keeping every comment.

    A YAML round trip (safe_load + safe_dump) would drop the provenance comments that mark
    values as provisional or calibrated, so values are replaced on their own lines. The result
    is re-validated before it is written.
    """
    import re

    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    pending = {(s, k): v for s, kv in updates.items() for k, v in kv.items()}
    section = None
    for i, line in enumerate(lines):
        top = re.match(r"^([A-Za-z_][\w-]*):", line)
        if top:
            section = top.group(1)
            continue
        m = re.match(r"^(\s+)([A-Za-z_][\w-]*):(\s*)([^#\n]*?)(\s*#.*)?(\r?\n)?$", line)
        if m and section and (section, m.group(2)) in pending:
            v = pending.pop((section, m.group(2)))
            text = "null" if v is None else json.dumps(v) if isinstance(v, (str, bool)) else repr(v)
            if isinstance(v, bool):
                text = "true" if v else "false"
            elif isinstance(v, str):
                text = v if re.fullmatch(r"[\w./-]+", v) else json.dumps(v)
            lines[i] = f"{m.group(1)}{m.group(2)}:{m.group(3) or ' '}{text}{m.group(5) or ''}{m.group(6) or ''}"
    if pending:
        raise KeyError(f"config keys not found: {sorted(pending)}")
    new = "".join(lines)
    AnalysisConfig.model_validate(yaml.safe_load(new))  # never write an invalid file
    p.write_text(new, encoding="utf-8")
