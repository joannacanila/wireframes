"""Section 3.6.4, Algorithm 4."""

import cv2
import numpy as np
import pytest

from app.pipeline.skeleton import extract_centerline, longest_path, pixel_graph, prune_spurs, zhang_suen


def zs_reference(img: np.ndarray) -> np.ndarray:
    """Literal per-pixel transcription of the Zhang-Suen conditions in Section 3.6.4."""
    img = (img > 0).astype(np.uint8).copy()
    h, w = img.shape
    g = lambda r, c: img[r, c] if 0 <= r < h and 0 <= c < w else 0  # noqa: E731
    while True:
        changed = False
        for step in (0, 1):
            rm = []
            for r in range(h):
                for c in range(w):
                    if not img[r, c]:
                        continue
                    p = [g(r - 1, c), g(r - 1, c + 1), g(r, c + 1), g(r + 1, c + 1),
                         g(r + 1, c), g(r + 1, c - 1), g(r, c - 1), g(r - 1, c - 1)]
                    b = sum(p)
                    a = sum(p[i] == 0 and p[(i + 1) % 8] == 1 for i in range(8))
                    p2, _, p4, _, p6, _, p8, _ = p
                    ok = (p2 * p4 * p6 == 0 and p4 * p6 * p8 == 0) if step == 0 else \
                         (p2 * p4 * p8 == 0 and p2 * p6 * p8 == 0)
                    if 2 <= b <= 6 and a == 1 and ok:
                        rm.append((r, c))
            for r, c in rm:
                img[r, c] = 0
            changed |= bool(rm)
        if not changed:
            return img.astype(bool)


@pytest.mark.parametrize("seed", range(6))
def test_vectorised_zhang_suen_equals_literal_algorithm(seed):
    rng = np.random.default_rng(seed)
    m = np.zeros((60, 90), np.uint8)
    for _ in range(4):
        cv2.line(m, tuple(int(v) for v in rng.integers(0, 90, 2)), tuple(int(v) for v in rng.integers(0, 60, 2)),
                 1, int(rng.integers(2, 9)))
    np.testing.assert_array_equal(zhang_suen(m), zs_reference(m))


def test_thinning_gives_one_pixel_line():
    m = np.zeros((40, 200), np.uint8)
    m[15:26, 10:190] = 1
    s = zhang_suen(m)
    assert s[:, 30:170].sum(axis=0).max() == 1


def test_graph_has_no_spurious_triangle_junctions():
    m = np.zeros((80, 400), np.uint8)
    cv2.polylines(m, [np.array([[10, 60], [200, 20], [390, 55]], np.int32)], False, 1, 7)
    adj = pixel_graph(zhang_suen(m))
    degrees = [len(v) for v in adj.values()]
    assert max(degrees) <= 2 and degrees.count(1) == 2


def test_short_spur_is_pruned_and_long_branch_kept():
    m = np.zeros((120, 400), np.uint8)
    cv2.line(m, (10, 60), (390, 60), 1, 5)          # main wire
    cv2.line(m, (200, 60), (200, 40), 1, 3)         # 20 px spur  (< L_min = 30)
    cv2.line(m, (100, 60), (100, 5), 1, 3)          # 55 px branch (>= L_min)
    adj = pixel_graph(zhang_suen(m))
    removed = prune_spurs(adj, 30)
    assert 10 <= removed <= 25
    rows = {r for r, _ in adj}
    assert min(rows) < 15  # long branch survives
    assert not any(c == 200 and r < 55 for r, c in adj)  # spur is gone


def test_longest_path_by_double_bfs_on_tree():
    adj = {(0, i): set() for i in range(10)}
    for i in range(9):
        adj[(0, i)].add((0, i + 1)); adj[(0, i + 1)].add((0, i))
    adj[(1, 4)] = {(0, 4)}; adj[(0, 4)].add((1, 4))
    path = longest_path(adj)
    assert len(path) == 10 and {path[0], path[-1]} == {(0, 0), (0, 9)}


def test_centerline_is_ordered_from_left_and_in_global_coordinates():
    m = np.zeros((300, 600), np.uint8)
    cv2.line(m, (550, 250), (60, 120), 1, 6)
    cl = extract_centerline(m, 30, 20)
    assert cl.ok
    assert cl.points[0, 0] < cl.points[-1, 0]
    assert abs(cl.points[0, 0] - 60) < 6 and abs(cl.points[-1, 0] - 550) < 6
    assert np.all(np.diff(cl.points[:, 0]) >= 0)


def test_too_short_and_empty():
    m = np.zeros((50, 50), np.uint8)
    assert extract_centerline(m).reason == "empty_mask"
    cv2.line(m, (10, 10), (20, 12), 1, 3)
    out = extract_centerline(m, 30, 20)
    assert not out.ok and out.reason == "too_short"
