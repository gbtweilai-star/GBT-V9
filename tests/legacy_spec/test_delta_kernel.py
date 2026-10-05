# tests/test_delta_kernel.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import numpy as np, cv2, pytest
from body.delta_kernel import delta_metrics, heatmap_png, ALGORITHM, COLORMAP

def _img(seed, w=64, h=64):
    rng = np.random.default_rng(seed); return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)

def test_same_source_invariant():
    a = _img(1); b = a.copy(); b[10:20, 10:20] = (255, 0, 0)   # 局部改动
    k = delta_metrics(a, b, noise_threshold=6.0)
    png = heatmap_png(a, b, compare_spec={"algorithm": ALGORITHM, "colormap": COLORMAP,
                                          "threshold": 6.0, "max_delta_e": 40.0})
    rgba = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_UNCHANGED)
    colored = (rgba[:, :, 3] > 0)
    # ★ 有色像素占比必须 == effect_score; 且与 changed 逐像素一致
    assert colored.sum() == k["changed"].sum()
    assert abs(colored.mean() - k["effect_score"]) < 1e-9

def test_threshold_is_authorship():
    a = _img(2); b = a.copy(); b += 3                          # 轻微变化
    k_low  = delta_metrics(a, b, noise_threshold=1.0)
    k_high = delta_metrics(a, b, noise_threshold=10.0)
    assert k_low["effect_score"] > k_high["effect_score"]      # 阈值不同 → 结论不同(所以必须绑定)

def test_unknown_version_rejected():
    a = _img(3); b = a.copy()
    with pytest.raises(ValueError, match="unsupported_evidence_render_version"):
        heatmap_png(a, b, compare_spec={"algorithm": "lab-delta-e76-v9", "colormap": COLORMAP,
                                        "threshold": 6.0})

def test_deterministic_png():
    a = _img(4); b = a.copy(); b[:5, :5] = 0
    spec = {"algorithm": ALGORITHM, "colormap": COLORMAP, "threshold": 6.0, "max_delta_e": 40.0}
    assert heatmap_png(a, b, compare_spec=spec) == heatmap_png(a, b, compare_spec=spec)  # 同 OpenCV 版本内字节相同
