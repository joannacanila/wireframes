"""Image acquisition layer (Section 3.3.1) and GPS removal (Section 3.14.4).

Responsibilities:

1. Decode the upload and apply the EXIF orientation, so that every later coordinate
   (masks, centerlines, overlays) refers to the image as a person sees it.
2. Read the EXIF fields relevant to the acquisition envelope and compare them with it.
   Each check yields ``pass``, ``fail`` or ``unverified``. Failing or unverifiable
   checks raise flags; the image is never rejected for them (Algorithm 1, line 2).
3. Separate GPS coordinates from the image. The stored copy has no GPS IFD and no
   XMP packet; the coordinates are returned so the caller can store them in the
   access-limited ``pole_locations`` table (RA 10173, Data Privacy Act of 2012).

Only an undecodable file is rejected. v2.10: every readable image is accepted. It is normalised to
3-channel uint8 RGB (``to_rgb``: EXIF orientation, 16-bit, alpha, palette, CMYK, greyscale) and
downscaled to ``max_side_px`` on the long edge. Images that fail the objective quality check of
Figure 3.6 (too small, constant) are accepted and flagged; the reliability grade then turns the
verdict into INCONCLUSIVE - RETAKE PHOTO with the reason, instead of the upload failing.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError
from PIL.ExifTags import IFD

from app.core.config import AcquisitionConfig

# EXIF tag ids (CIPA DC-008)
_TAG_MAKE = 0x010F
_TAG_MODEL = 0x0110
_TAG_ORIENTATION = 0x0112
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_FOCAL_LENGTH = 0x920A
_TAG_FOCAL_35MM = 0xA405
_TAG_SUBJECT_DISTANCE = 0x9206
_TAG_EXPOSURE_TIME = 0x829A
_TAG_ISO = 0x8827
_TAG_GPS_IFD = 0x8825

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "TIFF", "MPO", "BMP", "GIF", "HEIF"}


# S16 (Part 8): HEIC / HEIF from phones. pi-heif is the decode-only build of pillow-heif (libheif + libde265, no
# encoder). Registering its opener makes Pillow recognise HEIF by content (the ISO-BMFF ``ftyp`` box), never by file
# name or the client's MIME type. libheif applies the container's rotation / mirror on decode and pi-heif resets the
# EXIF orientation to 1, so ``to_rgb``'s exif_transpose cannot rotate twice. Pillow's decompression-bomb limit applies.
def _register_heif() -> bool:
    try:
        import pi_heif
    except ImportError:  # optional at import time; uploads then report HEIC as unsupported
        return False
    pi_heif.register_heif_opener()
    return True


HEIF_AVAILABLE = _register_heif()
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1", b"heif"}


def heif_brand(data: bytes) -> str | None:
    """The major brand of an ISO-BMFF file when it is a HEIF-family image (``ftyp`` box at offset 4), else None."""
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        return brand.decode("ascii", "replace") if brand in HEIF_BRANDS or brand == b"avif" else None
    return None
DEFAULT_MAX_SIDE = 2048


def to_rgb(img: Image.Image, background: tuple[int, int, int] = (255, 255, 255)) -> Image.Image:
    """Any Pillow image -> 8-bit RGB, upright, alpha composited over ``background``.

    * EXIF orientation is applied (``ImageOps.exif_transpose``).
    * 16- and 32-bit integer / float images are scaled, not clipped: values above 255 are taken as
      16-bit (/257); float images in 0-1 are scaled by 255.
    * Alpha (RGBA, LA, PA, palette transparency) is composited over white: a transparent area
      becomes neutral sky-like background, not the arbitrary colour hidden under the alpha.
    * CMYK, YCbCr, LAB, palette, 1-bit and greyscale are converted by Pillow.
    Multi-frame files (GIF, MPO, TIFF stacks) use their first frame."""
    try:
        img.seek(0)
    except (EOFError, AttributeError, ValueError):
        pass
    img = ImageOps.exif_transpose(img) or img
    if img.mode in ("I", "I;16", "I;16B", "I;16L", "I;16N", "F"):
        a = np.asarray(img, dtype=np.float64)
        if img.mode == "F" and np.nanmax(a) <= 1.0:
            a = a * 255.0
        elif np.nanmax(a) > 255:
            a = a / 257.0
        a = np.nan_to_num(np.clip(a, 0, 255)).astype(np.uint8)
        return Image.fromarray(a, "L").convert("RGB")
    if img.mode == "P" and "transparency" in img.info:
        img = img.convert("RGBA")
    if img.mode in ("RGBA", "LA", "PA", "RGBa", "La"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, background + (255,))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return img.convert("RGB") if img.mode != "RGB" else img


def load_rgb(data: bytes, max_side: int = 0) -> np.ndarray:
    """Decode bytes to an H x W x 3 uint8 RGB array with ``to_rgb``; optional long-edge cap.
    Used wherever a stored upload is read back (worker, reports, admin) so all see one image."""
    with Image.open(io.BytesIO(data)) as im:
        im.load()
        rgb = np.asarray(to_rgb(im), dtype=np.uint8)
    return _cap(rgb, max_side)


def _cap(rgb: np.ndarray, max_side: int) -> np.ndarray:
    import cv2
    h, w = rgb.shape[:2]
    if max_side and max(h, w) > max_side:
        s = max_side / max(h, w)
        rgb = cv2.resize(rgb, (max(1, round(w * s)), max(1, round(h * s))), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(rgb)


class ImageRejected(ValueError):
    """Raised when an image fails the objective quality check (Figure 3.6)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class EnvelopeCheck:
    name: str
    status: str  # pass | fail | unverified
    value: float | None
    expected: tuple[float, float]
    source: str  # exif | user | none

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "value": self.value,
            "expected": list(self.expected),
            "source": self.source,
        }


@dataclass
class AcquisitionResult:
    image_rgb: np.ndarray  # H x W x 3, uint8, orientation applied
    sanitized_bytes: bytes  # image file to store: GPS and XMP removed
    sanitized_ext: str
    metadata: dict[str, Any]  # non-identifying EXIF summary
    gps: tuple[float, float] | None  # (lat, lon) - never stored with the image
    envelope: list[EnvelopeCheck]
    flags: list[str] = field(default_factory=list)


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        if isinstance(v, tuple) and len(v) == 2:  # legacy rational
            return float(Fraction(int(v[0]), int(v[1]))) if v[1] else None
        f = float(v)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return f if np.isfinite(f) else None


def _dms_to_deg(dms: Any, ref: Any) -> float | None:
    try:
        d, m, s = (_to_float(x) for x in dms)
    except (TypeError, ValueError):
        return None
    if d is None or m is None or s is None:
        return None
    deg = d + m / 60.0 + s / 3600.0
    if str(ref).strip().upper() in {"S", "W"}:
        deg = -deg
    return deg


def read_gps(exif: Image.Exif) -> tuple[float, float] | None:
    try:
        gps = exif.get_ifd(IFD.GPSInfo)
    except Exception:  # malformed IFD offsets
        return None
    if not gps or 2 not in gps or 4 not in gps:
        return None
    lat = _dms_to_deg(gps.get(2), gps.get(1, "N"))
    lon = _dms_to_deg(gps.get(4), gps.get(3, "E"))
    if lat is None or lon is None or not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    if lat == 0 and lon == 0:  # common "no fix" placeholder
        return None
    return round(lat, 7), round(lon, 7)


def _exif_metadata(exif: Image.Exif) -> dict[str, Any]:
    try:
        sub = exif.get_ifd(IFD.Exif)
    except Exception:
        sub = {}
    def g(tag: int) -> Any:
        return sub.get(tag, exif.get(tag))
    dt_raw = g(_TAG_DATETIME_ORIGINAL)
    captured_at = None
    if isinstance(dt_raw, str):
        try:
            captured_at = datetime.strptime(dt_raw.strip(), "%Y:%m:%d %H:%M:%S").isoformat()
        except ValueError:
            captured_at = None
    meta = {
        "make": (str(exif.get(_TAG_MAKE)).strip("\x00 ") if exif.get(_TAG_MAKE) else None),
        "model": (str(exif.get(_TAG_MODEL)).strip("\x00 ") if exif.get(_TAG_MODEL) else None),
        "captured_at": captured_at,
        "focal_length_mm": _to_float(g(_TAG_FOCAL_LENGTH)),
        "focal_length_35mm": _to_float(g(_TAG_FOCAL_35MM)),
        "subject_distance_m": _to_float(g(_TAG_SUBJECT_DISTANCE)),
        "exposure_time_s": _to_float(g(_TAG_EXPOSURE_TIME)),
        "iso": _to_float(g(_TAG_ISO)),
    }
    return meta


def _check(name: str, value: float | None, rng: tuple[float, float], source: str) -> EnvelopeCheck:
    if value is None:
        return EnvelopeCheck(name, "unverified", None, rng, "none")
    status = "pass" if rng[0] <= value <= rng[1] else "fail"
    return EnvelopeCheck(name, status, round(float(value), 3), rng, source)


def check_envelope(
    meta: dict[str, Any],
    cfg: AcquisitionConfig,
    *,
    distance_m: float | None = None,
    elevation_deg: float | None = None,
) -> list[EnvelopeCheck]:
    """Compare metadata with the acquisition envelope of Section 3.3.1.

    Field-entered values (distance, elevation) take precedence over EXIF because EXIF
    SubjectDistance is a focus estimate and elevation is not recorded by cameras.
    """
    checks = [_check("focal_length_35mm", meta.get("focal_length_35mm"), cfg.focal_length_35mm, "exif")]
    if distance_m is not None:
        checks.append(_check("distance_m", distance_m, cfg.distance_m, "user"))
    else:
        checks.append(_check("distance_m", meta.get("subject_distance_m"), cfg.distance_m, "exif"))
    checks.append(_check("elevation_deg", elevation_deg, cfg.elevation_deg, "user"))
    hour = None
    if meta.get("captured_at"):
        t = datetime.fromisoformat(meta["captured_at"])
        hour = t.hour + t.minute / 60.0
    checks.append(_check("daylight_hour", hour, cfg.daylight_hours, "exif"))
    return checks


def envelope_flags(checks: list[EnvelopeCheck], has_exif: bool) -> list[str]:
    flags = []
    if not has_exif:
        flags.append("acquisition_unverified")
    for c in checks:
        if c.status == "fail":
            flags.append(f"outside_envelope:{c.name}")
        elif c.status == "unverified" and has_exif:
            flags.append(f"unverified:{c.name}")
    return flags


_STRIP_APPN = {0xE1, 0xED}  # APP1 (EXIF incl. GPS, XMP), APP13 (Photoshop IRB / IPTC location)


def strip_jpeg_metadata(data: bytes, exif: bytes | None = None) -> bytes | None:
    """Remove EXIF, XMP and IPTC from a JPEG at the byte level, without re-encoding (v2.10), and
    optionally insert a replacement EXIF block (``exif``: the "Exif\\0\\0..." payload, e.g. the original
    EXIF with the GPS IFD deleted).

    The entropy-coded image data, quantisation and Huffman tables, JFIF and ICC segments are copied
    verbatim, so the stored file decodes to exactly the uploaded pixels. Returns None for anything
    that is not a well-formed JPEG marker stream (the caller then re-encodes)."""
    if data[:2] != b"\xff\xd8":
        return None
    out, i, n = bytearray(b"\xff\xd8"), 2, len(data)
    if exif:
        if len(exif) + 2 > 0xFFFF:
            return None
        out += b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker == 0xDA:  # start of scan: the rest is image data, copied as is
            out += data[i:]
            return bytes(out)
        if 0xD0 <= marker <= 0xD9 or marker == 0x01:
            out += data[i: i + 2]
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2: i + 4], "big")
        if seg_len < 2 or i + 2 + seg_len > n:
            return None
        if marker not in _STRIP_APPN:
            out += data[i: i + 2 + seg_len]
        i += 2 + seg_len
    return None


def _sanitize(img: Image.Image, fmt: str, oriented: bool, data: bytes | None = None) -> tuple[bytes, str]:
    """Re-encode the image with the GPS IFD and XMP removed.

    v2.10: an untouched JPEG (8-bit RGB, upright, not downscaled) keeps its compressed data byte for
    byte; only the metadata segments are removed (``strip_jpeg_metadata``), so the server analyses
    exactly the uploaded pixels. Otherwise JPEG falls back to ``quality="keep"`` (original tables) or
    a q95 re-encode.
    """
    exif = img.getexif()
    if _TAG_GPS_IFD in exif:
        del exif[_TAG_GPS_IFD]
    exif[_TAG_ORIENTATION] = 1
    buf = io.BytesIO()
    if fmt == "JPEG" and not oriented and data is not None:
        stripped = strip_jpeg_metadata(data, exif.tobytes() if len(exif) else None)
        if stripped is not None:
            try:  # trust it only if it decodes to the very same pixels
                with Image.open(io.BytesIO(stripped)) as a, Image.open(io.BytesIO(data)) as b:
                    if np.array_equal(np.asarray(a.convert("RGB")), np.asarray(b.convert("RGB"))):
                        return stripped, ".jpg"
            except (OSError, ValueError):
                pass
    if fmt in {"JPEG", "MPO"}:
        kw: dict[str, Any] = {"exif": exif.tobytes(), "optimize": True}
        if not oriented and fmt == "JPEG" and img.mode == "RGB":
            # "keep" requires the original JpegImageFile object, not a converted copy.
            img.save(buf, "JPEG", quality="keep", **kw)
        else:
            img.convert("RGB").save(buf, "JPEG", quality=95, subsampling=0, **kw)
        return buf.getvalue(), ".jpg"
    # Lossless formats are normalised to RGB PNG (alpha already composited); PNG gets no EXIF at all.
    img.convert("RGB").save(buf, "PNG", optimize=True)
    return buf.getvalue(), ".png"


def acquire(
    data: bytes,
    cfg: AcquisitionConfig,
    *,
    distance_m: float | None = None,
    elevation_deg: float | None = None,
) -> AcquisitionResult:
    """Decode, quality-check, read EXIF, check the envelope and strip GPS."""
    try:
        img = Image.open(io.BytesIO(data))
        fmt = (img.format or "").upper()
        img.load()
    except Image.DecompressionBombError as exc:
        raise ImageRejected("too_many_pixels", "The image has too many pixels to process.") from exc
    except (UnidentifiedImageError, OSError, ValueError, ImportError, RuntimeError) as exc:
        # ImportError: plugins registered by other libraries (e.g. Ultralytics registers a
        # lazy HEIF opener) can raise ModuleNotFoundError on bytes that are not an image.
        brand = heif_brand(data)
        if brand == "avif" or (brand and not HEIF_AVAILABLE):
            raise ImageRejected("unsupported_codec", "This photo uses an image encoding the server cannot read "
                                "(HEIC / HEIF support or this codec is not available). Export it as JPEG and "
                                "try again.") from exc
        if brand:
            raise ImageRejected("undecodable", "This HEIC / HEIF photo could not be read; the file may be damaged "
                                "or incomplete. Try the original photo again, or export it as JPEG.") from exc
        raise ImageRejected("undecodable", "The file is not a readable image.") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageRejected("unsupported_format", f"Unsupported image format: {fmt or 'unknown'}.")

    exif = img.getexif()
    has_exif = len(exif) > 0
    gps = read_gps(exif) if has_exif else None
    meta = _exif_metadata(exif) if has_exif else {}

    orientation = exif.get(_TAG_ORIENTATION, 1) if has_exif else 1
    oriented = orientation not in (None, 1)
    mode_in, (w0, h0) = img.mode, img.size
    try:
        rgb_full = np.asarray(to_rgb(img), dtype=np.uint8)
    except (OSError, ValueError, MemoryError) as exc:
        raise ImageRejected("undecodable", "The file is not a readable image.") from exc
    max_side = getattr(cfg, "max_side_px", DEFAULT_MAX_SIDE)
    rgb = _cap(rgb_full, max_side)
    resized = rgb.shape[:2] != rgb_full.shape[:2]
    del rgb_full

    h, w = rgb.shape[:2]
    flags_extra = []
    if min(h, w) < cfg.min_side_px:
        if getattr(cfg, "reject_low_quality", False):
            raise ImageRejected("resolution_too_low",
                                f"Image is {w}x{h}px; the shorter side must be at least {cfg.min_side_px}px.")
        flags_extra.append("resolution_too_low")
    if int(rgb.max()) == int(rgb.min()):
        if getattr(cfg, "reject_low_quality", False):
            raise ImageRejected("constant_image", "Image has no content (all pixels identical).")
        flags_extra.append("blank_image")

    # the stored copy is exactly the analysed image unless it is an untouched 8-bit RGB JPEG
    untouched_jpeg = fmt == "JPEG" and mode_in == "RGB" and not oriented and not resized
    sanitized, ext = _sanitize(img if untouched_jpeg else Image.fromarray(rgb, "RGB"), fmt,
                               oriented or not untouched_jpeg, data if untouched_jpeg else None)
    checks = check_envelope(meta, cfg, distance_m=distance_m, elevation_deg=elevation_deg)
    flags = envelope_flags(checks, has_exif) + flags_extra + (["downscaled"] if resized else [])
    meta = {k: v for k, v in meta.items() if v is not None}
    meta.update({"width": w, "height": h, "format": fmt, "had_gps": gps is not None})
    if resized or mode_in != "RGB":
        meta.update({"original_width": w0, "original_height": h0, "original_mode": mode_in})
    return AcquisitionResult(rgb, sanitized, ext, meta, gps, checks, flags)


def file_has_gps(path: Path | str) -> bool:
    """True if a stored file still carries any GPS information (used by tests/audits)."""
    raw = Path(path).read_bytes()
    with Image.open(io.BytesIO(raw)) as im:
        if _TAG_GPS_IFD in im.getexif():
            return True
    return b"GPSLatitude" in raw or b"exif:GPS" in raw
