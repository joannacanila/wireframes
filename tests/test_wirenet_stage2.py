"""WireNet Stage 2 pieces: label tightening, copy-paste, procedural dangling wires, loss."""

import numpy as np
import pytest

from ml.wirenet_stage2 import ABANDONED, IGNORE, draw_dangling, instance_bank, paste, tighten_abandoned


def test_loose_polygon_is_tightened_and_a_wireless_one_ignored():
    tgt = np.zeros((100, 200), np.uint8)
    tgt[10:60, 20:80] = ABANDONED        # loose region around a wire
    tgt[70:95, 120:190] = ABANDONED      # a region with no wire at all (mislabel)
    like = np.zeros_like(tgt, bool)
    like[30:33, 20:80] = True            # the actual wire inside the first region
    out, stats = tighten_abandoned(tgt, like)
    assert stats == {"kept": 1, "ignored": 1}
    assert (out[30:33, 20:80] == ABANDONED).all()
    assert (out[10:30, 20:80] == IGNORE).all() and (out[70:95, 120:190] == IGNORE).all()
    assert (out[tgt == 0] == 0).all()     # background untouched


def test_paste_and_synthesis_label_what_they_draw():
    rng = np.random.default_rng(0)
    src = np.full((200, 200, 3), 200, np.uint8)
    tgt = np.zeros((200, 200), np.uint8)
    src[100:103, 20:180] = 30
    tgt[100:103, 20:180] = ABANDONED
    bank = instance_bank([(src, tgt, "a")])
    assert len(bank) == 1
    img = np.full((512, 512, 3), 180, np.uint8)
    t = np.zeros((512, 512), np.uint8)
    paste(img, t, *bank[0], rng)
    assert (t == ABANDONED).sum() > 200 and img[t == ABANDONED].mean() < 120  # dark wire, labelled
    img2 = np.full((512, 512, 3), 200, np.uint8)
    t2 = np.zeros((512, 512), np.uint8)
    draw_dangling(img2, t2, rng)
    assert (t2 == ABANDONED).sum() > 150 and img2[t2 == ABANDONED].mean() < 110


def test_stage2_loss_ignores_ignored_pixels():
    torch = pytest.importorskip("torch")
    from ml.wirenet_stage2 import stage2_loss
    logits = torch.zeros(1, 5, 32, 32, requires_grad=True)
    tgt = torch.full((1, 32, 32), IGNORE, dtype=torch.long)
    tgt[0, 10, :] = ABANDONED
    loss = stage2_loss(logits, tgt, torch.ones(5), torch.log(torch.full((5,), 0.2)))
    loss.backward()
    g = logits.grad[0].abs().sum(0)
    assert torch.isfinite(loss) and g[10].sum() > 0 and g[0].sum() < g[10].sum() * 0.5


def test_paste_and_synthesis_avoid_letterbox_padding():
    from ml.wirenet_stage2 import content_mask
    rng = np.random.default_rng(1)
    img = np.full((512, 512, 3), 114, np.uint8)
    img[200:512] = np.random.default_rng(2).integers(100, 230, (312, 512, 3))  # photo in the lower part
    content = content_mask(img)
    assert not content[:190].any() and content[220:].mean() > 0.95
    for seed in range(5):
        t = np.zeros((512, 512), np.uint8)
        draw_dangling(img.copy(), t, np.random.default_rng(seed), content)
        assert not (t[:190] == ABANDONED).any()
    _ = rng


def test_abandoned_coverage_is_robust_to_loose_labels():
    from ml.wirenet_stage2 import abandoned_coverage
    gt = np.zeros((200, 200), bool)
    gt[20:180, 90:110] = True                 # loose, 20 px wide polygon
    thin = np.zeros_like(gt)
    thin[20:180, 99:101] = True               # the 2 px wire inside it
    assert abandoned_coverage(thin, gt) == (1, 1, int(thin.sum()), int(thin.sum()))
    flood = np.ones_like(gt)
    hits, n, inside, px = abandoned_coverage(flood, gt)
    assert hits == 1 and inside / px < 0.25  # perfect recall, poor precision
