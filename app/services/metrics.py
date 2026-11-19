"""S18: the "Other" metrics (VNI, CRS index) and the segmentation-mask legend, built on the server from the
configuration and the result, so no page hard-codes a band, a scale or a class name.

VNI   Section 3.6.2, Equations 1-4, Table 3.7 (app/pipeline/vni.py, config ``vni``). Bands are provisional.
CRS   S17 (D-37): a fixed scaling of the image sag ratio, CRS = min{10, 250 x delta/L} (app/services/comparison.py).
      Power lines only; it never decides a class. Its marks are the power-line reference boundaries x 250.
"""

from __future__ import annotations

from typing import Any

from app.core.config import CLASSES, VEGETATION

# What each class of the segmentation model means (dataset class names; app/core/config.py CLASSES).
CLASS_TEXT = {
    "power-distribution-lines": ("Power distribution line", "Pixels the model labelled as an electric power conductor."),
    "telecom-cable": ("Telecom / fiber cable", "Pixels labelled as a telecommunications, cable TV or fiber line."),
    "abandoned-wire": ("Abandoned wire", "Pixels labelled as a cable that looks disused (slack, cut or coiled)."),
    VEGETATION: ("Vegetation", "Tree or plant pixels; used only to check wire contact, never measured for sag."),
}


def mask_legend(r: dict[str, Any]) -> list[dict[str, Any]]:
    """The classes that actually appear in this photo's mask, in the model's class order. A wire whose class evidence
    is split is drawn grey and listed as a display state, not as a model class."""
    present = {w.get("class") for w in r.get("wires") or []}
    if r.get("vegetation"):
        present.add(VEGETATION)
    out = [{"key": c, "label": CLASS_TEXT[c][0], "text": CLASS_TEXT[c][1], "model_class": True}
           for c in CLASSES if c in present and c in CLASS_TEXT]
    if any(w.get("class_status") == "uncertain" for w in r.get("wires") or []):
        out.append({"key": "uncertain", "label": "Type uncertain", "model_class": False,
                    "text": "Wire pixels whose class evidence is split between types; no type is assumed."})
    return out


def _vni(r: dict[str, Any], cfg) -> dict[str, Any] | None:
    v = r.get("vni")
    if not v or v.get("value") is None:
        return None
    lo = 0.0
    bands = []
    for b in cfg.vni.bands:
        bands.append({"label": b.label, "from": lo, "to": b.max})
        lo = b.max
    return {
        "key": "vni", "name": "Visual Noise Index (VNI)", "value": round(float(v["value"]), 3), "band": v.get("band"),
        "unit": "0-1 scale", "scale_max": 1.0, "bands": bands, "provisional": bool(cfg.vni.provisional),
        "what": "How cluttered the photo is: the share of image detail at high spatial frequencies.",
        "inputs": "The whole photograph in grey scale, smoothed with a fixed Gaussian kernel, mean removed and Hann-windowed.",
        "how": ("2-D Fourier transform; the share of spectral power beyond a radius of "
                f"{cfg.vni.radius_factor:g} x half the shorter image side, rescaled between the calibration set's "
                "1st and 99th percentiles (Chapter III Eq. 1-4)."),
        "meaning": "Higher values mean a busier scene (many wires, leaves, buildings).",
        "reference": ("Bands from Table 3.7 (" + ", ".join(f"{b['label']} up to {b['to']:g}" for b in bands) + ")"
                      + ("; provisional: calibrated on public images, not yet on local photographs" if cfg.vni.provisional
                         else "")),
        "cannot": "It describes the scene, not the wires. It is not a measure of wire condition, sag or safety.",
    }


def _crs(r: dict[str, Any]) -> dict[str, Any] | None:
    from app.services.comparison import CRS_SCALE
    measured = [(w["index"], w["comparison"]["crs_index"]) for w in r.get("wires") or []
                if (w.get("comparison") or {}).get("crs_index") is not None]
    power = next((x for x in (r.get("comparison") or {}).get("by_reference") or [] if x.get("id") == "power"), None)
    ref = None
    for w in r.get("wires") or []:
        c = w.get("comparison") or {}
        if c.get("reference_id") == "power":
            ref = c
            break
    if not measured and ref is None and power is None:
        return None
    lo = (ref or {}).get("lower_boundary")
    hi = (ref or {}).get("upper_boundary")
    marks = [] if lo is None or hi is None else [round(lo * CRS_SCALE, 2), round(hi * CRS_SCALE, 2)]
    top = max(measured, key=lambda x: x[1]) if measured else None
    return {
        "key": "crs", "name": "Catenary Risk Score index (CRS)", "value": None if top is None else top[1],
        "wire": None if top is None else top[0], "n": len(measured), "unit": "0-10 scale", "scale_max": 10.0,
        "marks": marks, "mark_labels": [] if not marks else [f"{lo * 100:g}% sag", f"{hi * 100:g}% reference"],
        "what": "The image sag ratio of a power line, rescaled to 0-10 for continuity with Chapter III.",
        "inputs": "The sag (delta) and chord (L) of each fitted power-line span in the photo.",
        "how": f"CRS = min(10, {CRS_SCALE:g} x delta/L). The highest value among the measured power lines is shown.",
        "meaning": ("The same information as the sag meter on another scale" + (
            f": {marks[0]:g} = {lo * 100:g}% sag, {marks[1]:g} = the {hi * 100:g}% reference." if marks else ".")),
        "reference": "No separate threshold: the marks are the power-line sag reference boundaries x 250.",
        "cannot": ("It is not an independent safety metric and never decides a classification; telecom and "
                   "unidentified wires have no CRS."),
    }


def other_metrics(r: dict[str, Any], cfg) -> list[dict[str, Any]]:
    return [m for m in (_vni(r, cfg), _crs(r)) if m is not None]
