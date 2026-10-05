# tests/test_devour_frames.py
import numpy as np, pytest
from body.devour_frames import _hist_dist, DevourFrames

def test_hist_dist_bounds():
    a = np.array([1, 2, 3, 4]); b = np.array([1, 2, 3, 4])
    assert _hist_dist(a, b) == pytest.approx(0.0)            # 同分布 → 0
    c = np.array([0, 0, 0, 10]); assert 0 < _hist_dist(a, c) <= 1.0

def test_warmth_separates_red_vs_blue():
    red  = np.zeros((32, 32, 3), np.uint8); red[:, :, 0]  = 200
    blue = np.zeros((32, 32, 3), np.uint8); blue[:, :, 2] = 200
    assert DevourFrames._warmth(red) > 0 > DevourFrames._warmth(blue)

def test_hist_sat_invariant_to_hue_when_gray():
    gray = np.full((32, 32, 3), 128, np.uint8)
    h = DevourFrames._histograms(gray)
    assert h["H"].sum() == 0                                  # 全灰 → 色相加权和为 0
