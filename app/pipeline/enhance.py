"""Adaptive enhancement of field photographs for the segmentation path.

Scope (deliberately narrow):
* Applied to the RGB path that feeds YOLO11-seg (and, through ``ml.curate``, to the training
  images, so the model is trained on what it will see).
* NEVER applied to the grayscale path of the VNI. Section 3.6.2 fixes that path at a 5 x 5
  Gaussian "so that VNI values stay comparable"; CLAHE and sharpening add high-frequency
  energy and would inflate the VNI. ``preprocess`` builds the gray path from the original
  image for this reason, and a test pins it.
* Skeletonization works on the predicted binary masks, so enhancement reaches it only through
  better masks (thin fiber drops that survive segmentation), never directly.

Steps, each decided per image in ``auto`` mode from measured quality:
1. Denoise   cv2.fastNlMeansDenoisingColored, strength scaled to the estimated noise sigma
             (Immerkaer 1996, fast noise variance estimation), for high-ISO night/overcast shots.
2. CLAHE     on the L channel of CIELAB (tiles 8 x 8, clip limit 2.0) for dark, flat or
             overexposed photos; colour channels are untouched.
3. Unsharp   I + amount (I - G_sigma * I), only where |detail| > threshold, for soft photos.
             Blur-adaptive: the amount grows linearly from ``unsharp_amount`` at the blur
             threshold (variance of the Laplacian) to ``unsharp_amount_max`` at zero sharpness,
             so a badly blurred photo gets more edge restoration and a crisp one gets none. The
             radius widens (sigma x 1-3) and the detail threshold drops (x 1-0.25) with blur too.
Order matters: denoise before contrast and sharpening, which would otherwise amplify noise.

v2.9 routing (``motion_route``, ``deblock``):
* Motion blur vs defocus. Both lower the variance of the Laplacian; only motion blur removes
  detail along one direction. ``motion_coherence`` measures that per 128 px tile with a
  content-normalised structure tensor: J_f (gradients of the image) whitened by J_c (gradients
  of a sigma-2 Gaussian copy), M = J_c^-1/2 J_f J_c^-1/2. Content orientation (parallel wires,
  facades) appears in both tensors and cancels; a blur kernel attenuates fine detail more than
  coarse detail, and a *linear* kernel does so only along its direction, so M becomes
  anisotropic. The tile anisotropies a = 1 - lambda_min/lambda_max are combined as the length of
  the mean doubled-angle vector, so they count only when the lost direction agrees across the
  photo (a camera shake has one direction; content anisotropy does not). A soft photo with
  coherence >= ``motion_coherence_min`` gets edge-preserving smoothing instead of the unsharp
  mask, which would otherwise sharpen the streak.
* JPEG blocking: ``deblock`` smooths small steps across the 8 px block grid only (an H.264-style
  boundary filter, gated so real edges such as wires are untouched). It runs at the original
  resolution, before the working-scale resize, because the grid is only aligned there.

``enhance_strong`` is the fallback profile (runner): denoise if noisy, CLAHE at a higher clip
limit and a stronger unsharp mask, applied regardless of the measured thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.config import EnhancementConfig

# 3 x 3 Laplacian difference kernel of Immerkaer's estimator
_IMMERKAER = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)


@dataclass(frozen=True)
class Quality:
    mean_l: float  # mean CIELAB lightness, 0-100
    contrast: float  # std of lightness, 0-100
    clipped_high: float  # share of near-white pixels
    noise_sigma: float  # estimated Gaussian noise sigma, 8-bit grey levels
    sharpness: float  # variance of the Laplacian on a 1024 px long-side copy
    motion_coherence: float = 0.0  # direction-coherent blur anisotropy, 0-1 (v2.9)
    motion_angle_deg: float = 0.0  # direction of the lost detail (image x axis = 0)
    bright_share: float = 0.0  # share of content pixels with L* > 90 (v2.10 glare gate)
    line_contrast: float = 0.0  # thin dark-line visibility: mean top-1% black-top-hat on L*, L* units (v2.10)

    def as_dict(self) -> dict:
        return {k: round(float(v), 4) for k, v in self.__dict__.items()}


@dataclass
class Enhanced:
    rgb: np.ndarray
    applied: list[str] = field(default_factory=list)
    quality: Quality | None = None

    @property
    def flags(self) -> list[str]:
        return [f"enhanced:{s}" for s in self.applied]


def _work_copy(gray: np.ndarray, long_side: int = 1024) -> np.ndarray:
    h, w = gray.shape
    s = long_side / max(h, w)
    return gray if s >= 1 else cv2.resize(gray, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)


def noise_sigma(gray: np.ndarray) -> float:
    """Immerkaer (1996): sigma = sqrt(pi/2) / (6 (W-2)(H-2)) * sum |I * N|."""
    g = gray.astype(np.float64)
    h, w = g.shape
    if h < 3 or w < 3:
        return 0.0
    conv = cv2.filter2D(g, -1, _IMMERKAER, borderType=cv2.BORDER_REFLECT)[1:-1, 1:-1]
    return float(np.sqrt(np.pi / 2) * np.abs(conv).sum() / (6.0 * (w - 2) * (h - 2)))


def _tensor_parts(g: np.ndarray):
    gx = cv2.Sobel(g, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_64F, 0, 1, ksize=3)
    return gx * gx, gx * gy, gy * gy


def motion_coherence(gray: np.ndarray, content: np.ndarray | None = None, tile: int = 128,
                     scale: float = 2.0, min_energy: float = 4.0) -> tuple[float, float]:
    """(coherence 0-1, angle of the lost detail in degrees). See the module docstring.

    ``content`` masks out letterbox padding; tiles with < 90% content or with almost no coarse
    gradient energy (sky, flat walls) are skipped, since their tensor is noise."""
    g = gray.astype(np.float64)
    fine, coarse = _tensor_parts(g), _tensor_parts(cv2.GaussianBlur(g, (0, 0), scale))
    if content is None:
        content = np.ones(g.shape, bool)
    h, w = g.shape
    z = []
    for y in range(0, h - tile + 1, tile):
        for x in range(0, w - tile + 1, tile):
            sl = (slice(y, y + tile), slice(x, x + tile))
            if content[sl].mean() < 0.9:
                continue
            jc = np.array([[coarse[0][sl].mean(), coarse[1][sl].mean()], [coarse[1][sl].mean(), coarse[2][sl].mean()]])
            if np.trace(jc) < min_energy:
                continue
            jf = np.array([[fine[0][sl].mean(), fine[1][sl].mean()], [fine[1][sl].mean(), fine[2][sl].mean()]])
            wc, vc = np.linalg.eigh(jc + 1e-6 * np.eye(2))
            ci = vc @ np.diag(wc ** -0.5) @ vc.T
            e, ev = np.linalg.eigh(ci @ jf @ ci)
            if e[1] <= 0:
                continue
            a = 1.0 - max(e[0], 0.0) / e[1]
            z.append(a * np.exp(2j * np.arctan2(ev[1, 0], ev[0, 0])))  # doubled angle: axial direction
    if not z:
        return 0.0, 0.0
    m = np.mean(z)
    return float(abs(m)), float(np.degrees(np.angle(m) / 2) % 180)


def _content(rgb: np.ndarray, fill: int = 114, margin: int = 9) -> np.ndarray:
    """Photo content without letterbox padding: padding is whole rows / columns of the fill
    colour, so a grey wall inside the photo is not mistaken for it. ``margin`` px next to the
    padding are excluded too (the resize edge)."""
    flat = (np.abs(rgb.astype(np.int16) - fill) <= 3).all(axis=2)
    rows, cols = flat.all(axis=1), flat.all(axis=0)
    m = ~(rows[:, None] | cols[None, :])
    if m.all():
        return m
    return cv2.erode(m.astype(np.uint8), np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)).astype(bool)


def line_visibility(l_star: np.ndarray, content: np.ndarray) -> tuple[float, float]:
    """(near-white share, thin dark-line contrast) of a photo, on CIELAB L* (0-100).

    Glare does two things at once: it lifts most of the frame to near-white AND it compresses how
    much darker a thin dark structure (a conductor) is than its surroundings. The second is
    measured with a black top-hat (5 px ellipse: the thickness scale of a wire at the 1024 px
    working scale) and summarised as the mean of the strongest 1% of responses, so it reads the
    wires and edges rather than flat sky. A bright overcast sky keeps this contrast; glare does
    not (Chapter 4 v2.10)."""
    if not content.any():
        return 0.0, 0.0
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    th = cv2.morphologyEx(l_star, cv2.MORPH_BLACKHAT, k)[content]
    top = np.partition(th, th.size - max(1, th.size // 100))[th.size - max(1, th.size // 100):]
    return float((l_star[content] > 90).mean()), float(top.mean())


def measure(rgb: np.ndarray, coherence_below: float = 60.0) -> Quality:
    """``coherence_below``: motion coherence is computed only for photos at or below this Laplacian
    variance (the blur gate), the only photos it is used for; others get 0."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    l_chan = lab[..., 0].astype(np.float64) * (100.0 / 255.0)  # OpenCV stores L in 0-255
    small = rgb
    if max(rgb.shape[:2]) > 1024:
        s = 1024 / max(rgb.shape[:2])
        small = cv2.resize(rgb, (round(rgb.shape[1] * s), round(rgb.shape[0] * s)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    sharp = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    content = _content(small)
    coh, ang = motion_coherence(gray, content) if sharp <= coherence_below else (0.0, 0.0)
    l_small = cv2.cvtColor(small, cv2.COLOR_RGB2LAB)[..., 0].astype(np.float32) * (100.0 / 255.0)
    bright, line_c = line_visibility(l_small, content)
    return Quality(
        mean_l=float(l_chan.mean()), contrast=float(l_chan.std()),
        clipped_high=float((l_chan > 97).mean()),
        noise_sigma=noise_sigma(gray),
        sharpness=sharp,
        motion_coherence=coh, motion_angle_deg=ang, bright_share=bright, line_contrast=line_c,
    )


def is_motion_blur(q: Quality, cfg: EnhancementConfig) -> bool:
    """Soft, direction-coherent and not washed out: in an overexposed photo the low Laplacian
    variance comes from lost contrast, not from blur, and the anisotropy is the content's."""
    return bool(getattr(cfg, "motion_route", False) and q.sharpness <= cfg.blur_laplacian_max
                and q.motion_coherence >= cfg.motion_coherence_min
                and q.clipped_high < cfg.overexposed_frac_min)


def deblock(rgb: np.ndarray, alpha: float = 12.0, beta: float = 4.0) -> np.ndarray:
    """Smooth the 8 px JPEG block grid. At each boundary between pixels p0 | q0 (with p1, q1
    beside them) the step is filtered only if |p0 - q0| < alpha and both sides are flat
    (|p1 - p0|, |q1 - q0| < beta): a blocking step, not an edge. With d = q0 - p0, p0 and q0
    move d/3 towards each other and p1, q1 d/6, so the step becomes a ramp over four pixels."""
    img = rgb.astype(np.float32)
    for axis in (1, 0):
        a = np.moveaxis(img, axis, 0)  # boundaries along the first axis
        n = a.shape[0]
        b = np.arange(8, n - 1, 8)
        b = b[b >= 2]
        if not len(b):
            continue
        p1, p0, q0, q1 = a[b - 2], a[b - 1], a[b], a[b + 1]
        gate = ((np.abs(p0 - q0) < alpha) & (np.abs(p1 - p0) < beta) & (np.abs(q1 - q0) < beta)).astype(np.float32)
        d = (q0 - p0) * gate
        a[b - 2] = p1 + d / 6
        a[b - 1] = p0 + d / 3
        a[b] = q0 - d / 3
        a[b + 1] = q1 - d / 6
        img = np.moveaxis(a, 0, axis)
    return np.clip(img + 0.5, 0, 255).astype(np.uint8)


def clahe_lab(rgb: np.ndarray, clip: float = 2.0, tiles: int = 8) -> np.ndarray:
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[..., 0] = cv2.createCLAHE(clipLimit=clip, tileGridSize=(tiles, tiles)).apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def denoise(rgb: np.ndarray, sigma: float, strength: float = 0.9) -> np.ndarray:
    h = float(np.clip(strength * sigma, 3.0, 15.0))  # filter strength tracks the measured noise
    return cv2.fastNlMeansDenoisingColored(rgb, None, h, h, 7, 21)


def unsharp(rgb: np.ndarray, sigma: float = 1.0, amount: float = 0.6, threshold: float = 4.0) -> np.ndarray:
    img = rgb.astype(np.float32)
    blur = cv2.GaussianBlur(img, (0, 0), sigma)
    detail = img - blur
    mask = (np.abs(detail).max(axis=2, keepdims=True) > threshold).astype(np.float32)
    return np.clip(img + amount * detail * mask, 0, 255).astype(np.uint8)


def blur_degree(q: Quality, cfg: EnhancementConfig) -> float:
    """0 at (or above) the blur threshold, 1 at zero sharpness (variance of the Laplacian)."""
    if cfg.blur_laplacian_max <= 0:
        return 0.0
    return float(np.clip(1.0 - q.sharpness / cfg.blur_laplacian_max, 0.0, 1.0))


def unsharp_amount_for(q: Quality, cfg: EnhancementConfig) -> float:
    """Linear in blur: ``unsharp_amount`` at the threshold, ``unsharp_amount_max`` at sharpness 0."""
    return cfg.unsharp_amount + blur_degree(q, cfg) * max(0.0, cfg.unsharp_amount_max - cfg.unsharp_amount)


def unsharp_params_for(q: Quality, cfg: EnhancementConfig, amount: float | None = None) -> tuple[float, float, float]:
    """(sigma, amount, threshold), all blur-adaptive. A wider blur in a soft photo spreads an edge
    over more pixels, so the detail layer is taken at a larger radius and the noise threshold is
    lowered; otherwise a strongly blurred photo has no detail above the threshold and nothing
    is sharpened at all."""
    t = blur_degree(q, cfg)
    return (cfg.unsharp_sigma * (1.0 + 2.0 * t), unsharp_amount_for(q, cfg) if amount is None else amount,
            cfg.unsharp_threshold * (1.0 - 0.75 * t))


def multiscale_retinex(rgb: np.ndarray, sigmas=(0.01, 0.05, 0.15), blend: float = 0.6) -> np.ndarray:
    """MSR on CIELAB L only (Jobson et al. 1997): log L - log(G_sigma * L), averaged over
    scales (sigmas as fractions of the longer side), stretched to 0-255 at the 1st-99th
    percentiles and blended with the original L. Colour channels are untouched."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    L = lab[..., 0].astype(np.float32) + 1.0
    s = max(rgb.shape[:2])
    r = np.mean([np.log(L) - np.log(cv2.GaussianBlur(L, (0, 0), max(1.0, f * s)) + 1e-3) for f in sigmas], axis=0)
    lo, hi = np.percentile(r, (1, 99))
    r = np.clip((r - lo) / max(hi - lo, 1e-6), 0, 1) * 255.0
    lab[..., 0] = np.clip(blend * r + (1 - blend) * (L - 1.0), 0, 255).astype(np.uint8)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)


def blockiness(gray: np.ndarray) -> float:
    """JPEG blocking: mean absolute horizontal+vertical step across 8 px block boundaries divided
    by the mean step inside blocks (about 1.0 for natural images, larger for heavy JPEG)."""
    g = gray.astype(np.float32)
    dx = np.abs(np.diff(g, axis=1))
    dy = np.abs(np.diff(g, axis=0))
    bx = dx[:, 7::8].mean() / max(np.delete(dx, np.s_[7::8], axis=1).mean(), 1e-6)
    by = dy[7::8, :].mean() / max(np.delete(dy, np.s_[7::8], axis=0).mean(), 1e-6)
    return float((bx + by) / 2)


def edge_denoise(rgb: np.ndarray, sigma: float) -> np.ndarray:
    """Edge-preserving smoothing: removes sensor noise and JPEG blocking, keeps thin wire edges."""
    return cv2.bilateralFilter(rgb, 5, float(np.clip(6 * max(sigma, 2.0), 15, 60)), 3)


def decide(q: Quality, cfg: EnhancementConfig) -> list[str]:
    steps = []
    if q.noise_sigma >= cfg.denoise_sigma_min:
        steps.append("denoise")
    elif getattr(cfg, "edge_denoise", False) and q.noise_sigma >= cfg.edge_denoise_sigma_min:
        steps.append("edge_denoise")
    if getattr(cfg, "retinex", False) and (q.mean_l <= cfg.retinex_dark_l_max
                                           or q.clipped_high >= cfg.retinex_glare_frac_min):
        steps.append("retinex")
    if q.mean_l <= cfg.dark_l_max or q.contrast <= cfg.low_contrast_max or q.clipped_high >= cfg.overexposed_frac_min:
        steps.append("clahe")
    if is_motion_blur(q, cfg):
        if getattr(cfg, "motion_action", "smooth") == "smooth":
            steps.append("motion_smooth")  # v2.9: no unsharp under directional blur
        else:
            steps.append("motion_skip")
    elif q.sharpness <= cfg.blur_laplacian_max:
        steps.append("unsharp")
    return steps


def enhance(rgb: np.ndarray, cfg: EnhancementConfig) -> Enhanced:
    """Return the segmentation-path image and what was applied (recorded as flags)."""
    if cfg.mode == "off":
        return Enhanced(rgb)
    q = measure(rgb, cfg.blur_laplacian_max)
    steps = ["denoise", "clahe", "unsharp"] if cfg.mode == "always" else decide(q, cfg)
    if getattr(cfg, "edge_denoise", False) and cfg.mode != "always" and "denoise" not in steps \
            and "edge_denoise" not in steps \
            and blockiness(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)) >= cfg.jpeg_blockiness_min:
        steps.insert(0, "edge_denoise")
    if "motion_smooth" in steps and ("denoise" in steps or "edge_denoise" in steps):
        steps.remove("motion_smooth")  # already smoothed once
    order = {"denoise": 0, "edge_denoise": 0, "motion_smooth": 0, "motion_skip": 0, "retinex": 1, "clahe": 2, "unsharp": 3}
    steps = sorted(steps, key=order.get)
    out = rgb
    for s in steps:  # fixed order: denoise -> retinex -> CLAHE -> unsharp
        if s == "denoise":
            out = denoise(out, q.noise_sigma, cfg.denoise_strength)
        elif s == "motion_skip":
            continue
        elif s in ("edge_denoise", "motion_smooth"):
            out = edge_denoise(out, q.noise_sigma)
        elif s == "retinex":
            out = multiscale_retinex(out)
        elif s == "clahe":
            out = clahe_lab(out, cfg.clahe_clip_limit, cfg.clahe_tiles)
        elif s == "unsharp":
            sigma, amount, thr = unsharp_params_for(q, cfg)
            if cfg.mode == "always" and q.sharpness > cfg.blur_laplacian_max:
                sigma, amount, thr = cfg.unsharp_sigma, cfg.unsharp_amount, cfg.unsharp_threshold
            out = unsharp(out, sigma, amount, thr)
    return Enhanced(out, steps, q)


def enhance_strong(rgb: np.ndarray, cfg: EnhancementConfig) -> Enhanced:
    """Fallback profile for a second segmentation pass on a degraded photo."""
    q = measure(rgb, cfg.blur_laplacian_max)
    out, steps = rgb, []
    if q.noise_sigma >= cfg.denoise_sigma_min:
        out, steps = denoise(out, q.noise_sigma, cfg.denoise_strength), ["denoise"]
    out = clahe_lab(out, cfg.fallback_clahe_clip_limit, cfg.clahe_tiles)
    if is_motion_blur(q, cfg) and not getattr(cfg, "motion_fallback_unsharp", False):
        # v2.9: no sharpening of a motion streak in the fallback either (unless configured)
        return Enhanced(edge_denoise(out, q.noise_sigma), steps + ["clahe_strong", "motion_smooth"], q)
    sigma, amount, thr = unsharp_params_for(q, cfg, max(cfg.fallback_unsharp_amount, unsharp_amount_for(q, cfg)))
    out = unsharp(out, sigma, amount, thr)
    return Enhanced(out, steps + ["clahe_strong", "unsharp_strong"], q)
