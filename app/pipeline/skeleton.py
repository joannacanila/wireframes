"""Centerline extraction (Section 3.6.4, Algorithm 4).

1. Zhang-Suen thinning of the binary mask, vectorised over the whole array.
   For pixel P1 with neighbours P2..P9 clockwise from the pixel above:
       B = number of non-zero neighbours,
       A = number of 0 -> 1 transitions in P2, P3, ..., P9, P2.
   Sub-iteration 1 removes P1 if 2 <= B <= 6, A == 1, P2*P4*P6 == 0, P4*P6*P8 == 0.
   Sub-iteration 2 uses P2*P4*P8 == 0 and P2*P6*P8 == 0. Repeat until stable.
2. Graph of skeleton pixels with eight-neighbour connectivity.
3. Repeatedly remove terminal branches shorter than L_min pixels.
4. Longest path by two breadth-first searches.
5. Fewer than n_min points -> "too short to fit".
6. Order the points from the left end.

Graph note: a Zhang-Suen skeleton can contain "corner" pixels where a diagonal edge and
the two orthogonal edges around it form a 3-cycle. Keeping those diagonal edges makes
ordinary path pixels look like junctions. A diagonal edge is therefore dropped when the
two pixels already share an orthogonal neighbour (m-adjacency). This removes only
redundant edges: the connected components are exactly those of the 8-connected graph.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

Pixel = tuple[int, int]  # (row, col)


# ----------------------------------------------------------------------------- thinning


def _neighbours(img: np.ndarray) -> list[np.ndarray]:
    """P2..P9 for every pixel of a zero-padded uint8 image (returned unpadded)."""
    p = np.pad(img, 1)
    c = slice(1, -1)
    return [
        p[:-2, c],    # P2  north
        p[:-2, 2:],   # P3  north-east
        p[c, 2:],     # P4  east
        p[2:, 2:],    # P5  south-east
        p[2:, c],     # P6  south
        p[2:, :-2],   # P7  south-west
        p[c, :-2],    # P8  west
        p[:-2, :-2],  # P9  north-west
    ]


def zhang_suen(mask: np.ndarray, max_passes: int = 10_000) -> np.ndarray:
    """Zhang-Suen thinning. Returns a boolean one-pixel-wide skeleton."""
    img = (np.asarray(mask) > 0).astype(np.uint8)
    for _ in range(max_passes):
        changed = False
        for step in (0, 1):
            n = _neighbours(img)
            p2, p3, p4, p5, p6, p7, p8, p9 = n
            b = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            seq = n + [p2]
            a = sum(((seq[i] == 0) & (seq[i + 1] == 1)).astype(np.uint8) for i in range(8))
            if step == 0:
                c1 = (p2 * p4 * p6) == 0
                c2 = (p4 * p6 * p8) == 0
            else:
                c1 = (p2 * p4 * p8) == 0
                c2 = (p2 * p6 * p8) == 0
            remove = (img == 1) & (b >= 2) & (b <= 6) & (a == 1) & c1 & c2
            if remove.any():
                img[remove] = 0
                changed = True
        if not changed:
            break
    return img.astype(bool)


# --------------------------------------------------------------------------------- graph

_ORTHO = ((-1, 0), (0, 1), (1, 0), (0, -1))
_DIAG = ((-1, 1), (1, 1), (1, -1), (-1, -1))


def pixel_graph(skel: np.ndarray) -> dict[Pixel, set[Pixel]]:
    """Adjacency of skeleton pixels (eight-neighbour, redundant diagonals removed)."""
    on = skel.astype(bool)
    h, w = on.shape
    pts = set(zip(*np.nonzero(on), strict=True))
    pts = {(int(r), int(c)) for r, c in pts}
    adj: dict[Pixel, set[Pixel]] = {p: set() for p in pts}
    for r, c in pts:
        for dr, dc in _ORTHO:
            q = (r + dr, c + dc)
            if q in pts:
                adj[(r, c)].add(q)
        for dr, dc in _DIAG:
            q = (r + dr, c + dc)
            if q in pts and (r + dr, c) not in pts and (r, c + dc) not in pts:
                adj[(r, c)].add(q)
    return adj


def _remove(adj: dict[Pixel, set[Pixel]], nodes: list[Pixel]) -> None:
    for p in nodes:
        for q in adj.pop(p, ()):
            if q in adj:
                adj[q].discard(p)


def prune_spurs(adj: dict[Pixel, set[Pixel]], l_min: int) -> int:
    """Remove terminal branches shorter than ``l_min`` pixels until none is removed.

    A terminal branch runs from an endpoint (degree 1) to the first junction
    (degree >= 3), exclusive of the junction. Branches that end at another endpoint
    form an isolated path; they are not spurs and are left for the length check.
    Returns the number of pixels removed.
    """
    removed_total = 0
    while True:
        # junction -> shortest terminal branch ending at it
        shortest: dict[Pixel, list[Pixel]] = {}
        for e in [p for p, nb in adj.items() if len(nb) == 1]:
            branch, junction = _trace_branch(adj, e, l_min)
            if junction is not None and len(branch) < l_min:
                if junction not in shortest or len(branch) < len(shortest[junction]):
                    shortest[junction] = branch
        if not shortest:
            return removed_total
        # Only the shortest spur per junction is removed in a pass. When two short arms
        # meet at one junction (a fork at the end of a wire), removing both would cut
        # the main line back; after one is removed the other becomes part of the line.
        for branch in shortest.values():
            _remove(adj, branch)
            removed_total += len(branch)


def _trace_branch(
    adj: dict[Pixel, set[Pixel]], endpoint: Pixel, limit: int
) -> tuple[list[Pixel], Pixel | None]:
    """Walk from an endpoint to the first junction. Returns (branch pixels, junction).

    The junction is None when the walk ends at another endpoint (an isolated path) or
    exceeds ``limit`` pixels (the branch is long enough to keep, so tracing can stop).
    """
    branch = [endpoint]
    prev, cur = None, endpoint
    while len(branch) <= limit:
        nxt = [q for q in adj[cur] if q != prev]
        if len(nxt) != 1:
            return branch, None
        prev, cur = cur, nxt[0]
        if len(adj[cur]) >= 3:
            return branch, cur
        branch.append(cur)
    return branch, None


def _bfs_farthest(adj: dict[Pixel, set[Pixel]], start: Pixel) -> tuple[Pixel, dict[Pixel, Pixel | None]]:
    parent: dict[Pixel, Pixel | None] = {start: None}
    queue = deque([start])
    last = start
    while queue:
        last = queue.popleft()
        for q in adj[last]:
            if q not in parent:
                parent[q] = last
                queue.append(q)
    return last, parent


def _components(adj: dict[Pixel, set[Pixel]]) -> list[set[Pixel]]:
    seen: set[Pixel] = set()
    comps = []
    for p in adj:
        if p in seen:
            continue
        _, parent = _bfs_farthest(adj, p)
        comp = set(parent)
        seen |= comp
        comps.append(comp)
    return comps


def longest_path(adj: dict[Pixel, set[Pixel]]) -> list[Pixel]:
    """Two breadth-first searches per component; the longest result over components.

    On a tree the second BFS gives the exact longest path (graph diameter). Skeleton
    loops are rare after pruning; on them the result is the BFS diameter estimate,
    which is what Algorithm 4 specifies.
    """
    best: list[Pixel] = []
    for comp in _components(adj):
        seed = min(comp, key=lambda p: (p[1], p[0]))  # deterministic start: leftmost pixel
        u, _ = _bfs_farthest(adj, seed)
        v, parent = _bfs_farthest(adj, u)
        path = []
        node: Pixel | None = v
        while node is not None:
            path.append(node)
            node = parent[node]
        if len(path) > len(best):
            best = path
    return best


# ------------------------------------------------------------------------------ public


@dataclass(frozen=True)
class Centerline:
    ok: bool
    points: np.ndarray  # n x 2 array of (x, y) in image pixels, ordered from the left end
    skeleton_pixels: int
    pruned_pixels: int
    reason: str | None = None  # "empty_mask" | "too_short"


def extract_centerline(mask: np.ndarray, l_min: int = 30, n_min: int = 20) -> Centerline:
    """Algorithm 4. Works on the mask's bounding box for speed; coordinates are global."""
    m = np.asarray(mask) > 0
    rows = np.flatnonzero(m.any(axis=1))
    cols = np.flatnonzero(m.any(axis=0))
    if rows.size == 0:
        return Centerline(False, np.empty((0, 2)), 0, 0, "empty_mask")
    r0, r1, c0, c1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
    crop = m[r0:r1, c0:c1]

    skel = zhang_suen(crop)
    adj = pixel_graph(skel)
    n_skel = len(adj)
    pruned = prune_spurs(adj, l_min)
    path = longest_path(adj)
    if len(path) < n_min:
        pts = np.array([(c + c0, r + r0) for r, c in path], dtype=np.float64).reshape(-1, 2)
        return Centerline(False, pts, n_skel, pruned, "too_short")
    pts = np.array([(c + c0, r + r0) for r, c in path], dtype=np.float64)
    if pts[0, 0] > pts[-1, 0] or (pts[0, 0] == pts[-1, 0] and pts[0, 1] > pts[-1, 1]):
        pts = pts[::-1]
    return Centerline(True, pts, n_skel, pruned)
