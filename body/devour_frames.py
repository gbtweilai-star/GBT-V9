# body/devour_frames.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: source_frame_id 必须与"调色/特效"无关 —— 取自时间线模型(media_id+clip+src_pts),
#       退而求其次用【原始源媒体帧】的边缘哈希; 只剩渲染预览时一律 identity=unknown;
#       帧存内容寻址仓库; 验证期间 ref 必须 pin 住, 不许被 R2 回收。
from __future__ import annotations
import hashlib, io, os, time
import numpy as np
import cv2

EPS = 1e-9


class DevourFrames:
    def __init__(self, executor, store, timeline, *, preview_roi="preview"):
        self.exec = executor          # 提供 screenshot(roi)->np.ndarray(RGB) 与窗口/DPI 信息
        self.store = store            # 内容寻址工件库(本地+可选 R2), 有 pin()/unpin()
        self.tl = timeline            # 时间线模型: clip_at(t), clip 含 {id, media_identity, source_in, start}
        self.roi_name = preview_roi
        self._cache: dict[tuple, dict] = {}

    # ═══════════ ① 取帧（暂停 + 仅预览 ROI + DPI 正确） ═══════════
    async def _grab_preview(self, project_id: str) -> np.ndarray:
        await self.exec.pause_playback()                 # 交给 actuator: 暂停, 不 seek
        roi = await self.exec.locate_roi(self.roi_name)  # 物理像素矩形, PerMonitorV2 感知
        if roi is None or roi.conf < 0.9:
            raise RuntimeError("preview_roi_not_found")
        # 连续两帧稳定才算站稳（防转场/播放残留）
        prev = None
        for _ in range(5):
            img = await self.exec.screenshot(roi)        # 只裁预览面板, 排除工具栏/时间线/选中框
            if prev is not None and self._mean_abs(img, prev) < 1.0:
                return img
            prev = img
            await self.exec.sleep(0.06)
        return prev

    async def snapshot_at(self, project_id: str, sec: float) -> dict:
        """固定时刻采一帧并落不可变仓库; 返回 sample dict。"""
        key = (project_id, round(sec, 3))
        if key in self._cache:
            return {**self._cache[key], "cached": True}
        await self.exec.seek_to(sec)                     # 播放头到位
        img = await self._grab_preview(project_id)       # RGB uint8
        h, w = img.shape[:2]
        digest = hashlib.sha256(img.tobytes()).hexdigest()   # 内容寻址
        ref = self.store.put(digest, self._encode(img), kind="frame")  # 去重写
        src_id, conf = self._source_frame_id(sec)        # ← 与调色无关的身份
        sample = {
            "ref": ref, "sha256": digest, "source_frame_id": src_id,
            "identity_confidence": conf, "roi": (w, h),
            "**hist": self._histograms(img),
            "color_temp_proxy": self._warmth(img),
            "captured_at": time.time(),
        }
        self._cache[key] = sample
        return sample

    # ═══════════ ② 源帧身份（与调色无关） ═══════════
    def _source_frame_id(self, sec: float) -> tuple[str, str]:
        clip = self.tl.clip_at(sec)
        if clip and clip.media_identity:
            src_pts = clip.source_in + (sec - clip.start)
            s = f"{clip.media_identity}|{clip.id}|{src_pts:.3f}"
            return hashlib.sha256(s.encode()).hexdigest()[:32], "high"
        # 退路: 读【原始源媒体】在该 pts 的帧, 用边缘哈希（调色不改边缘）; 需 media 路径已知
        f = self.tl.original_frame_at(sec)               # None 则降级
        if f is not None:
            return self._edge_hash(f), "low"
        return "unknown", "none"                         # 只剩渲染预览 → 无法确认身份

    @staticmethod
    def _edge_hash(bgr: np.ndarray) -> str:
        g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        g = cv2.GaussianBlur(g, (3, 3), 0)
        gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        bits = (mag[::8, ::8] > mag.mean()).astype(np.uint8).tobytes()   # 结构位图哈希
        return hashlib.sha256(bits).hexdigest()[:32]

    # ═══════════ ③ 帧差 ═══════════
    async def frame_diff_refs(self, ref_a: str, ref_b: str) -> float:
        a, b = self._load(ref_a), self._load(ref_b)
        if a.shape != b.shape:
            a = cv2.resize(a, (b.shape[1], b.shape[0]))
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean() / 255.0)

    # ═══════════ ④ 调色指标 ═══════════
    async def color_metrics_delta(self, ref_a: str, ref_b: str) -> dict:
        a, b = self._load(ref_a), self._load(ref_b)
        hist_a, hist_b = self._histograms(a), self._histograms(b)
        return {"luma": _hist_dist(hist_a["L"], hist_b["L"]),
                "sat":  _hist_dist(hist_a["S"], hist_b["S"]),
                "hue":  _hist_dist(hist_a["H"], hist_b["H"]),
                "warmth_delta": self._warmth(b) - self._warmth(a)}

    def targeted_color_score(self, x: dict, op: dict) -> float:
        """只统计本次调色【目标通道】的变化，避免别的通道噪声误判。"""
        return float(x.get(op.get("channel", "luma"), 0.0))

    def color_score(self, x: dict) -> float:
        """区间外只要【任一通道】明显变化就算越界。"""
        return float(max(x["luma"], x["sat"], x["hue"]))

    @staticmethod
    def _histograms(img_rgb: np.ndarray) -> dict:
        lab = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2Lab)
        hsv = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2HSV)
        L = np.histogram(lab[:, :, 0], bins=32, range=(0, 255))[0]
        S = np.histogram(hsv[:, :, 1], bins=32, range=(0, 255))[0]
        # 色相按饱和度加权（低饱和像素的色相不可信）
        H = np.histogram(hsv[:, :, 0], bins=36, range=(0, 180), weights=hsv[:, :, 1])[0]
        return {"L": L, "S": S, "H": H}

    @staticmethod
    def _warmth(img_rgb: np.ndarray) -> float:
        r = img_rgb[:, :, 0].astype(np.float64) + 1.0
        b = img_rgb[:, :, 2].astype(np.float64) + 1.0
        return float(np.median(np.log(r / b)))     # 暖度代理(不是开尔文)

    # ═══════════ ⑤ 特效指标 ═══════════
    async def effect_metrics(self, ref_a: str, ref_b: str, op: dict) -> dict:
        a, b = self._load(ref_a), self._load(ref_b)
        la = cv2.cvtColor(a, cv2.COLOR_RGB2Lab).astype(np.float32)
        lb = cv2.cvtColor(b, cv2.COLOR_RGB2Lab).astype(np.float32)
        de = np.sqrt(((lb - la) ** 2).sum(axis=2))          # 逐像素 ΔE
        changed = de > self._noise_threshold()              # 标定过的噪声门限
        h, w = de.shape
        ys, xs = np.nonzero(changed)
        return {
            "visual_delta":  float(de.mean() / 100.0),
            "effect_score":  float(changed.mean()),
            "fg_bg_divergence": self._fg_bg_div(la, lb, h, w),
            "alpha_edge":    _alpha_edge(a, b, changed),
            "mask_centroid": [float(xs.mean() / w), float(ys.mean() / h)] if len(xs) else None,
            "effect_value":  float(de.mean() / 100.0),   # 视觉强度代理, 非滑杆值
        }

    def _fg_bg_div(self, la, lb, h, w) -> float:
        cy, cx = int(h * .12), int(w * .12)                  # 中心块 vs 边框
        def gh(x, s): return np.histogram(x[s[0]:s[1], s[2]:s[3], 0], bins=32, range=(0, 255))[0]
        c = (cy, h - cy, cx, w - cx)
        bdr = [(0, h, 0, max(1, cx)), (0, h, w - max(1, cx), w)]
        d_before = _hist_dist(gh(la, c), sum((gh(la, s) for s in bdr)))
        d_after  = _hist_dist(gh(lb, c), sum((gh(lb, s) for s in bdr)))
        return float(d_after - d_before)                     # 中心/边缘分离度的变化

    # ═══════════ 存/取 与 pin ═══════════
    def pin(self, refs):   self.store.pin(refs)      # 验证期间不许被 R2 水位回收
    def unpin(self, refs): self.store.unpin(refs)
    def _load(self, ref):  return cv2.imdecode(np.frombuffer(self.store.get(ref), np.uint8), cv2.IMREAD_COLOR)[:, :, ::-1]
    def _encode(self, img): return cv2.imencode(".png", img[:, :, ::-1])[1].tobytes()
    def _noise_threshold(self): return getattr(self, "_nt", 6.0)   # ΔE 噪声门限, 由基线噪声标定


def _hist_dist(p, q, eps=EPS) -> float:
    """归一化卡方距离, 值域 [0,1]; 对称、对直方图形状敏感。"""
    p = p.astype(np.float64) / (p.sum() + eps)
    q = q.astype(np.float64) / (q.sum() + eps)
    return float(0.5 * np.sum((p - q) ** 2 / (p + q + eps)))


def _alpha_edge(a, b, changed) -> float:
    g = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
    mag = cv2.magnitude(cv2.Sobel(g, cv2.CV_32F, 1, 0, 3), cv2.Sobel(g, cv2.CV_32F, 0, 1, 3))
    edges = mag > mag.mean()
    if edges.sum() == 0:
        return 0.0
    return float((changed & edges).sum() / edges.sum())    # 变化是否集中在边缘(抠像/蒙版证据)
