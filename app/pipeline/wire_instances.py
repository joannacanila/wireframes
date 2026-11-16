"""Span instances from a full-resolution wire class map (v2.7).

YOLO11-seg at 1024 px proposes almost no wire spans above the operating threshold (test split:
0 power-distribution-line and 4 telecom-cable proposals against 253 and 361 labelled spans), so
refining its proposals cannot recover the wires. WireNet labels wire pixels well (validation
pixel IoU power 0.27 / tolerant 0.52). Spans are therefore extracted from WireNet's class map
geometrically:

1. per wire class: the class mask, components smaller than ``min_px`` removed
2. Zhang-Suen skeleton (the same thinning as Algorithm 4)
3. junction pixels (>= 3 skeleton neighbours) removed: the skeleton falls apart into pieces
4. pieces meeting at a junction are re-joined when their end directions are collinear within
   ``max_turn_deg`` (a wire continues straight through a crossing); union-find over pieces
5. every mask pixel is assigned to its nearest skeleton piece (Euclidean distance transform);
   a span's mask is the union of its pieces' pixels; spans whose skeleton is shorter than
   ``min_len`` are dropped
6. confidence = mean class probability over the span's pixels

v2.9 option (``xjunction_tol_deg`` > 0): at a crossing with four or more piece ends, the pieces
are paired by the *best perfect matching* of tangent continuity instead of greedily, with
tangents measured 4-20 px away from the end (the skeleton bends inside a junction, so the
nearest pixels give a biased direction). The matching is accepted only if every pair is
collinear within ``xjunction_tol_deg`` and the paired wires cross at more than
``xjunction_min_cross_deg``; otherwise the greedy rule above applies.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

_NEIGH = np.ones((3, 3), np.uint8)


def _skeleton(mask: np.ndarray) -> np.ndarray:
    import cv2
    return cv2.ximgproc.thinning(mask.astype(np.uint8) * 255, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN) > 0 \
        if hasattr(cv2, "ximgproc") else _skimage_skeleton(mask)


def _skimage_skeleton(mask: np.ndarray) -> np.ndarray:
    from skimage.morphology import skeletonize
    return skeletonize(mask)


def _label_coords(lab: np.ndarray, n: int) -> list[np.ndarray]:
    """[(row, col) pixels of label i + 1] for i < n, each in row-major order, from one pass over the
    image (v2.10; replaces n full-frame ``argwhere(lab == i + 1)`` scans, O(n x pixels))."""
    flat = np.flatnonzero(lab)
    vals = lab.ravel()[flat]
    order = np.argsort(vals, kind="stable")
    flat, vals = flat[order], vals[order]
    bounds = np.searchsorted(vals, np.arange(1, n + 2))
    rc = np.stack(np.unravel_index(flat, lab.shape), axis=1)
    return [rc[bounds[i]: bounds[i + 1]] for i in range(n)]


def _end_direction(coords: np.ndarray, end: np.ndarray, k: int = 8) -> np.ndarray:
    """Unit vector pointing *out of* a piece at ``end``, from its k nearest pixels."""
    d = np.hypot(*(coords - end).T)
    near = coords[np.argsort(d)[: min(k, len(coords))]]
    v = end - near.mean(axis=0)
    n = np.hypot(*v)
    return v / n if n > 0 else v


def _far_direction(coords: np.ndarray, end: np.ndarray, near: float = 4.0, far: float = 20.0) -> np.ndarray:
    """Outward unit tangent at ``end`` from the piece pixels ``near``-``far`` px away (principal
    axis, oriented away from the piece); falls back to the k-nearest estimate on short pieces."""
    d = np.hypot(*(coords - end).T)
    sel = coords[(d >= near) & (d <= far)]
    if len(sel) < 4:
        return _end_direction(coords, end)
    c = sel.mean(axis=0)
    _, _, vt = np.linalg.svd(sel - c, full_matrices=False)
    v = vt[0]
    if np.dot(end - c, v) < 0:
        v = -v
    return v / (np.hypot(*v) or 1.0)


def _perfect_matchings(items: list[int]):
    if len(items) < 2:
        yield []
        return
    a, rest = items[0], items[1:]
    for k, b in enumerate(rest):
        for m in _perfect_matchings(rest[:k] + rest[k + 1:]):
            yield [(a, b)] + m


def _xjunction_pairs(members, tol_deg: float, min_cross_deg: float):
    """Best perfect matching of piece ends at one crossing (4 or 6 distinct pieces), or None."""
    ids = {}
    for i, d in members:
        ids.setdefault(i, d)  # one end per piece at this junction
    if len(ids) not in (4, 6):
        return None
    keys = list(ids)
    cos_tol = np.cos(np.deg2rad(180 - tol_deg))
    best, best_cost = None, None
    for m in _perfect_matchings(list(range(len(keys)))):
        dots = [float(np.dot(ids[keys[a]], ids[keys[b]])) for a, b in m]
        if max(dots) > cos_tol:
            continue  # some pair is not anti-parallel within the tolerance
        cost = sum(1.0 + x for x in dots)
        if best_cost is None or cost < best_cost:
            best, best_cost = m, cost
    if best is None:
        return None
    lines = [ids[keys[a]] - ids[keys[b]] for a, b in best]  # direction of each paired wire
    for x in range(len(lines)):
        for y in range(x + 1, len(lines)):
            u, v = lines[x], lines[y]
            c = abs(float(np.dot(u, v))) / ((np.hypot(*u) * np.hypot(*v)) or 1.0)
            if np.degrees(np.arccos(np.clip(c, 0, 1))) <= min_cross_deg:
                return None  # near-parallel wires: a bundle, not an X crossing
    return [(keys[a], keys[b]) for a, b in best]


def spans_from_class_mask(mask: np.ndarray, prob: np.ndarray, min_px: int = 30, min_len: int = 30,
                          max_turn_deg: float = 25.0, junction_radius: int = 3, cluster_radius: int = 10,
                          xjunction_tol_deg: float = 0.0, xjunction_min_cross_deg: float = 30.0,
                          xjunction_far_tangent: bool = True, own_component: bool = False) -> list[tuple[np.ndarray, float]]:
    """Returns [(span mask, confidence)] for one wire class. v2.14 ``own_component``: a mask pixel may only join a
    skeleton piece in its own 8-connected mask component (otherwise a separate neighbouring wire whose own pieces
    were dropped is painted into the nearest span)."""
    lab, n = ndi.label(mask, structure=_NEIGH)
    if n == 0:
        return []
    sizes = ndi.sum(mask, lab, index=np.arange(1, n + 1))
    mask = np.isin(lab, np.flatnonzero(sizes >= min_px) + 1)
    if not mask.any():
        return []
    skel = _skeleton(mask)
    nb = ndi.convolve(skel.astype(np.uint8), _NEIGH, mode="constant") - 1
    junction = skel & (nb >= 3)
    jzone = ndi.binary_dilation(junction, iterations=junction_radius)
    pieces = skel & ~jzone
    plab, npieces = ndi.label(pieces, structure=_NEIGH)
    if npieces == 0:
        return []
    coords = _label_coords(plab, npieces)
    # piece ends: pixels with exactly one neighbour in their own piece (or the only pixel)
    pnb = ndi.convolve((plab > 0).astype(np.uint8), _NEIGH, mode="constant") - 1
    parent = list(range(npieces))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    # a shallow crossing thins into two junction points joined by a short bridge: junction points
    # within ``cluster_radius`` form one crossing, so both halves of each wire meet there
    cluster = ndi.binary_dilation(junction, iterations=cluster_radius)
    jlab, nj = ndi.label(cluster, structure=_NEIGH)
    touch: dict[int, list[tuple[int, np.ndarray]]] = {}
    grown = ndi.binary_dilation(cluster, iterations=1)
    bridges = set()
    for i, c in enumerate(coords):
        if cluster[tuple(c.T)].all():
            bridges.add(i)  # a bridge inside a crossing, not a wire end
            continue
        ends = [p for p in c if pnb[tuple(p)] <= 1] or [c[0]]
        for e in ends:
            if grown[tuple(e)]:
                y, x = e
                win = jlab[max(0, y - 1): y + 2, max(0, x - 1): x + 2]
                ids = np.unique(win[win > 0])
                for j in ids:
                    touch.setdefault(int(j), []).append(
                        (i, _far_direction(c, e) if xjunction_tol_deg > 0 and xjunction_far_tangent
                         else _end_direction(c, e)))
    cos_max = np.cos(np.deg2rad(180 - max_turn_deg))
    for members in touch.values():
        if xjunction_tol_deg > 0:
            xp = _xjunction_pairs(members, xjunction_tol_deg, xjunction_min_cross_deg)
            if xp is not None:
                for ia, ib in xp:
                    if find(ia) != find(ib):
                        parent[find(ia)] = find(ib)
                continue
        # join collinear pairs meeting at this junction, straightest first, each piece end once
        pairs = sorted(((float(np.dot(da, db)), ia, ib) for x, (ia, da) in enumerate(members)
                        for (ib, db) in members[x + 1:] if ia != ib), key=lambda t: t[0])
        used: set[int] = set()
        for c, ia, ib in pairs:
            if c <= cos_max and ia not in used and ib not in used:
                parent[find(ia)] = find(ib)
                used.update((ia, ib))
    groups: dict[int, list[int]] = {}
    for i in range(npieces):
        if i not in bridges:
            groups.setdefault(find(i), []).append(i)
    if not groups:
        return []
    # nearest-piece assignment for every mask pixel (bridges are not seeds: their pixels go to
    # the wires that cross there)
    seeds = plab.copy()
    if bridges:
        seeds[np.isin(seeds, np.fromiter((i + 1 for i in bridges), np.int32))] = 0
    _, (iy, ix) = ndi.distance_transform_edt(seeds == 0, return_indices=True)
    owner = seeds[iy, ix]
    owner[~mask] = 0
    if own_component:
        clab, _ = ndi.label(mask, structure=_NEIGH)
        owner[clab != clab[iy, ix]] = 0
    remap = np.zeros(npieces + 1, np.int32)
    for g, members in enumerate(groups.values(), start=1):
        remap[np.array(members) + 1] = g
    span_lab = remap[owner]
    boxes = ndi.find_objects(span_lab)  # v2.10: work inside each span's bounding box
    out = []
    for g, members in enumerate(groups.values(), start=1):
        length = sum(len(coords[i]) for i in members)
        if length < min_len or g > len(boxes) or boxes[g - 1] is None:
            continue
        sl = boxes[g - 1]
        sub = span_lab[sl] == g
        if sub.sum() < min_px:
            continue
        m = np.zeros(mask.shape, bool)
        m[sl] = sub
        out.append((m, float(prob[sl][sub].mean())))
    return out


# ----------------------------------------------------------------------------- v2.8: gap bridging

def _crop_skeleton(mask: np.ndarray) -> np.ndarray:
    """Skeleton pixel coordinates (row, col) in image space, computed on the bounding-box crop."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        return np.zeros((0, 2))
    y0, x0 = max(rows[0] - 1, 0), max(cols[0] - 1, 0)
    crop = mask[y0: rows[-1] + 2, x0: cols[-1] + 2]
    return np.argwhere(_skeleton(crop)) + (y0, x0)


def skeleton_length(mask: np.ndarray) -> int:
    return len(_crop_skeleton(mask))


def span_ends(mask: np.ndarray, k: int = 12) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    """Endpoints and outward unit tangents of a span, from its skeleton ordered along its
    principal axis. Returns (end_a, tan_a, end_b, tan_b, skeleton_length)."""
    sk = _crop_skeleton(mask).astype(np.float64)
    if len(sk) < 2:
        sk = np.argwhere(mask).astype(np.float64)
    c = sk.mean(axis=0)
    _, _, vt = np.linalg.svd(sk - c, full_matrices=False)
    t = (sk - c) @ vt[0]
    order = np.argsort(t)
    sk = sk[order]
    a, b = sk[0], sk[-1]
    ta = a - sk[: min(k, len(sk))].mean(axis=0)
    tb = b - sk[-min(k, len(sk)):].mean(axis=0)
    ta = ta / (np.hypot(*ta) or 1.0)
    tb = tb / (np.hypot(*tb) or 1.0)
    return a, ta, b, tb, float(len(sk))


def _stitch_candidates(ends, max_gap: float, cos_lim: float) -> list:
    """All (gap, i, j, end_i, end_j) pairs that may be bridged, in the order of v2.8's nested loop
    (i < j, then end a/b of i, then end a/b of j), vectorised over every pair of span ends (v2.10).
    Each test is the v2.8 test, element by element: distance <= max_gap; tangents anti-parallel
    within the angle; the gap runs along both tangents (only when it is longer than 1 px)."""
    n = len(ends)
    if n < 2:
        return []
    pts = np.array([[e[0], e[2]] for e in ends], np.float64).reshape(2 * n, 2)   # end k = (span k // 2, side k % 2)
    tan = np.array([[e[1], e[3]] for e in ends], np.float64).reshape(2 * n, 2)
    owner = np.repeat(np.arange(n), 2)
    a, b = np.nonzero(owner[:, None] < owner[None, :])          # ends of span i against ends of span j > i
    gap = pts[b] - pts[a]
    d = np.hypot(gap[:, 0], gap[:, 1])
    ok = d <= max_gap
    tt = tan[a, 0] * tan[b, 0] + tan[a, 1] * tan[b, 1]
    ok &= ~(-tt < cos_lim)
    long_ = ok & (d > 1.0)
    u = np.zeros_like(gap)
    u[long_] = gap[long_] / d[long_, None]
    da = tan[a, 0] * u[:, 0] + tan[a, 1] * u[:, 1]
    db = tan[b, 0] * -u[:, 0] + tan[b, 1] * -u[:, 1]
    ok &= ~(long_ & ((da < cos_lim) | (db < cos_lim)))
    sel = np.flatnonzero(ok)
    # v2.8's loop order is (i, j, side of i, side of j); ties in the later gap sort keep this order
    sel = sel[np.lexsort((b[sel] % 2, a[sel] % 2, owner[b[sel]], owner[a[sel]]))]
    return [(float(d[k]), int(owner[a[k]]), int(owner[b[k]]), pts[a[k]], pts[b[k]]) for k in sel]


def bridge_support(prob: np.ndarray, ea: np.ndarray, eb: np.ndarray) -> float:
    """Mean class probability along the straight gap between two span ends ((row, col) points), end
    pixels excluded: how much evidence WireNet has for a wire in the gap it did not label."""
    n = int(np.ceil(np.hypot(*(eb - ea))))
    if n < 2:
        return 1.0
    t = np.linspace(0.0, 1.0, n + 1)[1:-1]
    pts = np.rint(ea[None] + t[:, None] * (eb - ea)[None]).astype(int)
    pts[:, 0] = pts[:, 0].clip(0, prob.shape[0] - 1)
    pts[:, 1] = pts[:, 1].clip(0, prob.shape[1] - 1)
    return float(prob[pts[:, 0], pts[:, 1]].mean())


def lateral_offset(end_i, end_j, ea: np.ndarray, eb: np.ndarray) -> float:
    """Largest sideways distance of each bridge end from the other end's tangent line (px)."""
    def tangent(e, p):
        return e[1] if np.allclose(e[0], p) else e[3]
    d = eb - ea
    ta, tb = tangent(end_i, ea), tangent(end_j, eb)
    return float(max(abs(ta[0] * d[1] - ta[1] * d[0]), abs(tb[0] * d[1] - tb[1] * d[0])))


def _gap_crosses(ea: np.ndarray, eb: np.ndarray, boxes) -> bool:
    """Whether the straight gap between two (row, col) points passes through any (x0, y0, x1, y1) box."""
    t = np.linspace(0.0, 1.0, 33)
    pts = ea[None] + t[:, None] * (eb - ea)[None]
    ys, xs = pts[:, 0], pts[:, 1]
    return any(bool(((xs >= x0) & (xs <= x1) & (ys >= y0) & (ys <= y1)).any()) for x0, y0, x1, y1 in boxes)


def stitch_spans(spans: list[tuple[np.ndarray, float]], max_gap: float = 30.0, max_angle_deg: float = 15.0,
                 draw_bridge: bool = True, bridge_width: int = 2, prob: np.ndarray | None = None,
                 bridge_min_prob: float = 0.0, bridges_out: list | None = None,
                 max_lateral_px: float = 0.0, occluders: list | None = None,
                 occluder_max_gap: float = 0.0) -> list[tuple[np.ndarray, float]]:
    """Join spans whose facing endpoints are within ``max_gap`` px, whose outward tangents are
    anti-parallel within ``max_angle_deg`` and whose gap runs along both tangents (occlusions and
    mask dropouts). Greedy nearest-first with union-find; confidence is the area-weighted mean.

    v2.14: ``bridge_min_prob`` > 0 also requires WireNet evidence in the gap (mean class probability
    along it, ``bridge_support``). ``bridges_out``, if given, receives one ((x0, y0), (x1, y1)) segment
    per accepted bridge, so drawn bridge pixels can be shown as inferred, never as detections.
    ``max_lateral_px`` > 0 rejects a bridge whose far end lies more than that many px to the side of either end's
    tangent line: a dropout on one wire continues along it, a parallel neighbouring wire is offset sideways
    (the angle test alone allows 70 px x tan 25 deg = 33 px of offset)."""
    if len(spans) < 2:
        return spans
    ends = [span_ends(m) for m, _ in spans]
    cos_lim = np.cos(np.deg2rad(max_angle_deg))
    # v2.16: a gap longer than max_gap may be bridged only if the straight gap crosses an occluder box (a detected pole
    # standing in front of the wire), up to occluder_max_gap; every other test is unchanged
    long_gap = occluder_max_gap if occluders and occluder_max_gap > max_gap else max_gap
    cands = _stitch_candidates(ends, long_gap, cos_lim)
    if long_gap > max_gap:
        cands = [c for c in cands if c[0] <= max_gap or _gap_crosses(c[3], c[4], occluders)]
    parent = list(range(len(spans)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    bridges: dict[int, list] = {}
    used_ends: set = set()
    for _d, i, j, ea, eb in sorted(cands, key=lambda c: c[0]):
        ka, kb = (i, tuple(ea)), (j, tuple(eb))
        if ka in used_ends or kb in used_ends or find(i) == find(j):
            continue
        if bridge_min_prob > 0 and prob is not None and bridge_support(prob, ea, eb) < bridge_min_prob:
            continue
        if max_lateral_px > 0 and lateral_offset(ends[i], ends[j], ea, eb) > max_lateral_px:
            continue
        used_ends.update((ka, kb))
        parent[find(i)] = find(j)
        bridges.setdefault(j, []).append((ea, eb))
        if bridges_out is not None:
            bridges_out.append(((float(ea[1]), float(ea[0])), (float(eb[1]), float(eb[0]))))
    groups: dict[int, list[int]] = {}
    for i in range(len(spans)):
        groups.setdefault(find(i), []).append(i)
    out = []
    import cv2
    for members in groups.values():
        m = np.zeros_like(spans[members[0]][0])
        area = conf = 0.0
        for i in members:
            m |= spans[i][0]
            a = float(spans[i][0].sum())
            area += a
            conf += a * spans[i][1]
        if draw_bridge:
            canvas = m.astype(np.uint8)
            for i in members:
                for ea, eb in bridges.get(i, []):
                    cv2.line(canvas, (int(ea[1]), int(ea[0])), (int(eb[1]), int(eb[0])), 1, bridge_width)
            m = canvas.astype(bool)
        out.append((m, conf / max(area, 1.0)))
    return out


def extract_spans(class_mask: np.ndarray, prob: np.ndarray, *, min_px: int = 30, min_len: int = 30,
                  max_turn_deg: float = 25.0, cluster_radius: int = 10, max_gap: float = 0.0,
                  gap_angle_deg: float = 15.0, draw_bridge: bool = True, min_span_len: int = 0,
                  xjunction_tol_deg: float = 0.0, xjunction_min_cross_deg: float = 30.0,
                  xjunction_far_tangent: bool = True, bridge_min_prob: float = 0.0,
                  bridges_out: list | None = None, max_lateral_px: float = 0.0,
                  own_component: bool = False, occluders: list | None = None,
                  occluder_max_gap: float = 0.0) -> list[tuple[np.ndarray, float]]:
    """v2.8 span extraction: junction-split spans, optional collinear gap bridging, then a minimum
    *stitched* span length gate (skeleton pixels). v2.9: optional X-junction matching."""
    spans = spans_from_class_mask(class_mask, prob, min_px=min_px, min_len=min_len,
                                  max_turn_deg=max_turn_deg, cluster_radius=cluster_radius,
                                  xjunction_tol_deg=xjunction_tol_deg,
                                  xjunction_min_cross_deg=xjunction_min_cross_deg,
                                  xjunction_far_tangent=xjunction_far_tangent, own_component=own_component)
    if max_gap > 0 or (occluders and occluder_max_gap > 0):
        spans = stitch_spans(spans, max_gap, gap_angle_deg, draw_bridge, prob=prob,
                             bridge_min_prob=bridge_min_prob, bridges_out=bridges_out,
                             max_lateral_px=max_lateral_px, occluders=occluders, occluder_max_gap=occluder_max_gap)
    if min_span_len > 0:
        spans = [(m, c) for m, c in spans if skeleton_length(m) >= min_span_len]
    return spans


# ----------------------------------------------------------------------------- v2.16: strand-level classification

from dataclasses import dataclass, field  # noqa: E402


@dataclass
class Strand:
    """One physical wire strand: geometry built once on the class-agnostic wire mask, one class for all of it."""

    wire_class: str
    mask: np.ndarray
    confidence: float                       # mean P(wire) over the strand's pixels
    class_share: dict[str, float] = field(default_factory=dict)  # summed class probability, normalised
    class_status: str = "confident"         # "confident" | "uncertain" (top share below the minimum)


def strand_spans(probs: np.ndarray, wire_ids: list[int], params: dict, *, tau: float | None = None,
                 min_class_share: float = 0.0, bridges_out: list | None = None,
                 class_names: tuple[str, ...] | None = None, mask: np.ndarray | None = None) -> list[Strand]:
    """v2.16: spans from the union of the wire classes, then one class per span.

    The v2.7-v2.15 extractor ran once per class on the per-pixel argmax, so a cable whose pixels flip between power
    and telecom became two spans of different classes, each with a hole where the other class won. Here:

    1. wire mask = argmax is a wire class (``tau`` None), or P(wire) = sum of wire-class probabilities >= ``tau``
    2. spans by the shipped geometry (junction split, collinear joins, bridging, own-component, length gate) on
       that mask, with P(wire) as the evidence map for bridging
    3. class evidence = summed probability of each wire class over the span's *observed* pixels (bridge pixels
       carry no appearance evidence); the span takes the top class; when its share of the evidence is below
       ``min_class_share`` the strand is marked ``uncertain`` (the class is kept for the CRS limits, which are
       identical for every class, but is not presented as established)

    Crossing wires stay separate: the junction rule still splits at crossings and joins only collinear pieces,
    so each wire of an X is its own strand and is classified from its own pixels."""
    from app.core.config import CLASSES
    names = class_names or CLASSES
    p_wire = probs[wire_ids].sum(0)
    if mask is not None:  # caller's wire mask (the app resamples the model-space argmax to the photo)
        mask = mask.astype(bool)
    elif tau is None:
        mask = np.isin(probs.argmax(0), wire_ids)
    else:
        mask = p_wire >= tau
    if not mask.any():
        return []
    spans = extract_spans(mask, p_wire, bridges_out=bridges_out, **params)
    out = []
    for m, conf in spans:
        obs = m & mask
        sel = obs if obs.any() else m
        mass = probs[wire_ids][:, sel].sum(1).astype(np.float64)
        tot = float(mass.sum()) or 1.0
        share = {names[c - 1]: round(float(v) / tot, 4) for c, v in zip(wire_ids, mass, strict=True)}
        k = int(np.argmax(mass))
        status = "uncertain" if mass[k] / tot < min_class_share else "confident"
        out.append(Strand(names[wire_ids[k] - 1], m, conf, share, status))
    return out
