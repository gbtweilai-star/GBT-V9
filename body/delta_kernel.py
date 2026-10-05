# body/delta_kernel.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: ΔE 图与 changed mask 【只此一份】; 阈值只在 compare_spec; 算法/colormap 带版本;
#       版本不认识就拒绝渲染, 绝不悄悄重着色老证据 (等于篡改历史结论)。
import cv2
import numpy as np

ALGORITHM = "lab-delta-e76-v1"      # 算法名+版本
COLORMAP  = "amber-red-v1"          # 色带名+版本
MAX_DELTA_E = 40.0                  # 绝对饱和上限 (非按图分位归一!)


def delta_metrics(before_rgb: np.ndarray, after_rgb: np.ndarray, *, noise_threshold: float) -> dict:
    """唯一内核: 返回 ΔE 图 + 由它派生的全部标量。effect_metrics 必须也走这里。"""
    if before_rgb.dtype != np.uint8 or after_rgb.dtype != np.uint8:
        raise ValueError("frames_must_be_rgb_uint8")
    if before_rgb.shape != after_rgb.shape or before_rgb.ndim != 3 or before_rgb.shape[2] != 3:
        raise ValueError("frame_shape_mismatch")
    if not np.isfinite(noise_threshold) or noise_threshold < 0:
        raise ValueError("invalid_noise_threshold")

    a = cv2.cvtColor(before_rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)  # 浮点 Lab
    b = cv2.cvtColor(after_rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2LAB)
    delta_e = np.sqrt(np.sum((a - b) ** 2, axis=2, dtype=np.float32))   # 逐像素 ΔE76
    changed = delta_e > noise_threshold                                # 唯一 changed 定义

    h, w = changed.shape
    ys, xs = np.nonzero(changed)
    gray = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2GRAY)
    mag = cv2.magnitude(cv2.Sobel(gray, cv2.CV_32F, 1, 0, 3), cv2.Sobel(gray, cv2.CV_32F, 0, 1, 3))
    edges = mag > float(mag.mean())

    return {
        "delta_e": delta_e,                       # ← 热图用的图
        "changed": changed,                       # ← effect_score/alpha 用的掩码
        "visual_delta": float(delta_e.mean() / 100.0),
        "effect_score": float(changed.mean()),    # 变化像素占比
        "alpha_edge": float(np.count_nonzero(changed & edges) / max(1, np.count_nonzero(edges))),
        "mask_centroid": ([float(xs.mean() / w), float(ys.mean() / h)] if len(xs) else None),
    }


# ── 固定色带: 绝对刻度, 不做按图分位归一 ──
_STOPS = (
    (0.00, (255, 220, 80),  48),   # 刚过阈值: 淡黄
    (0.25, (255, 180,  0),  96),
    (0.60, (255,  90,  0), 160),   # 琥珀
    (1.00, (255,   0,  0), 220),   # 红
)


def heatmap_rgba(kernel: dict, *, noise_threshold: float, max_delta_e: float = MAX_DELTA_E) -> np.ndarray:
    de, mask = kernel["delta_e"], kernel["changed"]
    # 绝对刻度: (de - 阈值) / (上限 - 阈值) —— 安静的帧不会被"分位归一"强行染艳
    t = np.clip((de - noise_threshold) / max(1e-6, max_delta_e - noise_threshold), 0, 1)

    rgba = np.zeros((*de.shape, 4), dtype=np.uint8)     # 未变化像素 → 全透明(结果帧透出来)
    for i, (p, c0, a0) in enumerate(_STOPS[:-1]):
        q, c1, a1 = _STOPS[i + 1]
        sel = mask & (t >= p) & (t < q)
        f = np.clip((t[sel] - p) / (q - p), 0, 1)[:, None]
        rgba[sel, :3] = np.rint(np.array(c0) + f * (np.array(c1) - c0)).astype(np.uint8)
        rgba[sel, 3]  = np.maximum(1, np.rint(a0 + f[:, 0] * (a1 - a0))).astype(np.uint8)

    # ★契约: "有颜色" ⟺ 内核标记为 changed —— 两者必须逐像素一致
    assert np.array_equal(rgba[:, :, 3] > 0, mask), "heatmap_color_mismatch_vs_kernel"
    return rgba


def heatmap_png(before_rgb, after_rgb, *, compare_spec: dict) -> bytes:
    if compare_spec.get("algorithm") != ALGORITHM or compare_spec.get("colormap") != COLORMAP:
        raise ValueError("unsupported_evidence_render_version")   # 版本不符 → 拒绝, 不重着色
    threshold = float(compare_spec["threshold"])
    kernel = delta_metrics(before_rgb, after_rgb, noise_threshold=threshold)
    rgba = heatmap_rgba(kernel, noise_threshold=threshold,
                        max_delta_e=float(compare_spec.get("max_delta_e", MAX_DELTA_E)))
    ok, buf = cv2.imencode(".png", cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA),
                           [cv2.IMWRITE_PNG_COMPRESSION, 9])
    if not ok:
        raise RuntimeError("png_encode_failed")
    return buf.tobytes()
