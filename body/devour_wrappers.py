# body/devour_wrappers.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 阈值分两层(ΔE 门限 vs 比例门限), 不许混用; 身份不一致/样本不足 → unknown 而非 failed;
#       digest 用规范化 JSON 保证确定性; 多点 refs 全进 summary, 单点才填 baseline/result 列。
from __future__ import annotations
import hashlib, json, time
import numpy as np
from body.delta_kernel import delta_metrics, ALGORITHM, COLORMAP, MAX_DELTA_E

VISUAL = {"color", "effects", "mask", "keying", "keyframe"}


class DevourVerify:
    def __init__(self, calib: dict | None = None):
        # calib 存的是【比例/决策】门限, 与 ΔE 门限分开
        self.calib = calib or {"noise_threshold": None, "effect_floor": 0.05,
                               "outside_max": 0.03, "local_ratio": 3.0}

    # ═══════ ① 采样时刻: 返回配对点 {sample_id, region, before_s, after_s} ═══════
    def verification_times(self, op: dict) -> list[dict]:
        kind = op.get("op")
        if kind in VISUAL:
            a, b = float(op["start_s"]), float(op["end_s"])
            pts = [{"sample_id": f"in{i}", "region": "inside", "before_s": t, "after_s": t}
                   for i, t in enumerate([a + (b - a) * x for x in (.2, .5, .8)])]
            pts += [{"sample_id": f"out{i}", "region": "outside", "before_s": t, "after_s": t}
                    for i, t in enumerate([max(0.0, a - 0.5), b + 0.5])]
            return pts
        # 结构操作: 在编辑点前后各采一点, after_s 按时间映射(剪切位移/变速缩放)
        at = float(op.get("at_s", op.get("start_s", 0.0)))
        return [{"sample_id": f"pr{i}", "region": "structural",
                 "before_s": t, "after_s": self._map_after_time(op, t)}
                for i, t in enumerate([max(0.0, at - 0.5), at, at + 0.5])]

    @staticmethod
    def _map_after_time(op, t):
        kind, at = op.get("op"), float(op.get("at_s", 0.0))
        if kind == "cut":                      # 删 [at,to): 其后整体左移
            to = float(op.get("to_s", at))
            return t - (to - at) if t >= to else t
        if kind == "speed":                    # 变速后, 编辑点之后按倍率伸缩
            r = float(op.get("rate", 1.0))
            return at + (t - at) * r
        return t                               # split/reverse: 位置不变(由 UI 后置条件判定)

    # ═══════ ② 配对求指标: 按 (sample_id, region) 配对; 身份不一致 → 不可验证 ═══════
    async def delta_metrics_for_pair(self, baseline, after, times, *, read, noise_threshold):
        buckets = {"inside": [], "outside": [], "structural": []}
        unverifiable = []
        for s_b, s_a, t in zip(baseline, after, times):
            ident_ok = (s_b.get("source_frame_id") not in (None, "unknown")
                        and s_b["source_frame_id"] == s_a.get("source_frame_id"))
            if not ident_ok or s_b.get("identity_confidence") == "none":
                unverifiable.append(t["sample_id"]); continue
            before, aft = await read(s_b["ref"]), await read(s_a["ref"])
            k = delta_metrics(before, aft, noise_threshold=noise_threshold)
            buckets[t["region"]].append({
                "sample_id": t["sample_id"], "effect_score": k["effect_score"],
                "visual_delta": k["visual_delta"], "alpha_edge": k["alpha_edge"],
                "mask_centroid": k["mask_centroid"],
                "fg_bg_divergence": k.get("fg_bg_divergence"),
                "effect_value": k.get("effect_value", k["visual_delta"])})
        inside = buckets["inside"]; outside = buckets["outside"]
        return {
            "inside": inside, "outside": outside, "structural": buckets["structural"],
            "unverifiable": unverifiable,
            "inside_mean": float(np.mean([x["effect_score"] for x in inside])) if inside else None,
            "outside_max": max((x["effect_score"] for x in outside), default=None),
        }

    # ═══════ ③ 结论 + compare_spec ═══════
    def verdict_and_spec(self, op: dict, m: dict, *, ui_ok: bool | None = None):
        spec = {"algorithm": ALGORITHM, "version": 1, "colormap": COLORMAP,
                "threshold": self.calib.get("noise_threshold"), "max_delta_e": MAX_DELTA_E,
                "effect_floor": self.calib["effect_floor"],
                "outside_max": self.calib["outside_max"],
                "local_ratio": self.calib["local_ratio"]}
        if spec["threshold"] is None:
            return "unknown", spec                      # 未标定 → 不妄断
        if m["unverifiable"]:
            return "unknown", spec                      # 身份不可确认

        kind = op.get("op")
        if kind in VISUAL:
            if len(m["inside"]) < 3:
                return "unknown", spec                  # 样本不足
            if kind == "keyframe":
                return ("verified" if self._keyframe_ok(m, spec) else "unknown"), spec
            if kind in ("mask", "keying"):
                return ("verified" if self._mask_ok(m, spec) else "failed"), spec
            return ("verified" if self._visual_ok(m, spec) else "failed"), spec

        # 结构操作: 以 UI 时间线/标尺后置条件为准, 帧差仅辅助
        if ui_ok is None or len(m["structural"]) < 3:
            return "unknown", spec
        return ("verified" if ui_ok else "failed"), spec

    def _visual_ok(self, m, spec) -> bool:
        i, o = m["inside_mean"], m["outside_max"] or 0.0
        return (i >= spec["effect_floor"] and o <= spec["outside_max"]
                and i >= spec["local_ratio"] * max(o, 1e-6))

    def _mask_ok(self, m, spec) -> bool:
        return (all(x["fg_bg_divergence"] is not None and x["fg_bg_divergence"] >= 0.12
                    and x["alpha_edge"] >= 0.15 for x in m["inside"])
                and (m["outside_max"] or 0.0) <= spec["outside_max"])

    def _keyframe_ok(self, m, spec) -> bool:
        vals = [x["effect_value"] for x in m["inside"] if x["effect_value"] is not None]
        cents = [x["mask_centroid"] for x in m["inside"] if x["mask_centroid"]]
        value_moves = len(vals) >= 3 and max(vals) - min(vals) >= 0.08
        mask_moves = len(cents) >= 3 and max(_dist(cents[0], p) for p in cents[1:]) >= 0.03
        return value_moves or mask_moves

    # ═══════ ④ 证据行(确定性 digest) ═══════
    def evidence_row(self, trace_id, op, verdict, baseline, after, summary, *,
                     now_epoch=None, retain_days=30):
        canonical = json.dumps(summary, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        eid = f"E-{digest[:16]}"
        # 多点: refs 全在 summary; 只有单点才填 baseline_ref/result_ref 列
        b_ref = baseline[0]["ref"] if len(baseline) == 1 else None
        r_ref = after[0]["ref"] if len(after) == 1 else None
        now = now_epoch if now_epoch is not None else int(time.time())
        return (eid, trace_id, op["op"], verdict, b_ref, r_ref, digest, canonical,
                now, now + retain_days * 86400)

    # ═══════ ⑤ ΔE 噪声门限标定(与决策门限分开) ═══════
    def calibrate_noise_threshold(self, static_samples, *, floor=2.0, margin=1.5):
        """static_samples: 同一静态源帧的多次采集; 估 ΔE 噪声 → 门限。"""
        base, des = static_samples[0], []
        for f in static_samples[1:]:
            des.append(float(np.percentile(delta_metrics(base, f, noise_threshold=0.0)["delta_e"], 99)))
        nt = max(floor, float(np.median(des)) * margin) if des else 6.0
        self.calib["noise_threshold"] = nt
        return self.calib


def _dist(a, b): return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5
