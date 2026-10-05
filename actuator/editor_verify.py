# actuator/editor_verify.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 验证必须"同源帧比同源帧"(source_frame_id 一致); 区间内要变、区间外不许变;
#       证据不足就 unknown, 绝不把"没测到"当成"没生效"或"生效了"。
from statistics import mean
from actuator.editor_touch import Step, ActionDef

IN_MIN, OUT_MAX, LOCAL_RATIO = 0.08, 0.03, 3.0
KEYFRAME_MIN, MASK_MOVE_MIN = 0.08, 0.03


def _times(op):
    a, b = op["start_s"], op["end_s"]
    return {"inside":  [a + (b - a) * x for x in (.2, .5, .8)],
            "outside": [max(0.0, a - 0.5), b + 0.5]}


async def _capture_set(t, op):
    out = {"inside": [], "outside": []}
    for region, pts in _times(op).items():
        for sec in pts:
            x = await t.devour.snapshot_at(op["project_id"], sec)
            y = await t.devour.snapshot_at(op["project_id"], sec)   # 同点复采估噪声
            if x["source_frame_id"] != y["source_frame_id"]:
                raise RuntimeError("source_frame_mismatch")
            out[region].append({"sample": y, "sec": sec,
                                "noise": await t.devour.frame_diff_refs(x["ref"], y["ref"])})
    return out


async def _post_samples(t, op, baseline):
    post = {"inside": [], "outside": []}
    for region in post:
        for it in baseline[region]:
            old = it["sample"]
            new = await t.devour.snapshot_at(op["project_id"], it["sec"])
            if new["source_frame_id"] != old["source_frame_id"]:
                raise RuntimeError("source_frame_mismatch")
            post[region].append((old, new, await t.devour.frame_diff_refs(old["ref"], new["ref"])))
    return post


def _localized(inside, outside, noise_floor=0.0):
    """区间内变化显著、区间外几乎不变、且内≫外 —— 一次抓‘改错了整段’。”"""
    i, o = mean(inside), max(outside, default=0.0)
    floor = max(IN_MIN, 3 * noise_floor)
    return i >= floor and o <= OUT_MAX and i >= LOCAL_RATIO * max(o, 1e-6)

def build_color(t, op):
    st = {}

    async def capture(_t, _o): st["base"] = await _capture_set(t, op)

    async def applied(_t, _o):
        post = await _post_samples(t, op, st["base"])
        inside  = [t.devour.color_metrics_delta(a, b) for a, b, _ in post["inside"]]
        outside = [t.devour.color_metrics_delta(a, b) for a, b, _ in post["outside"]]
        st["ev"] = {"inside": inside, "outside": outside,
                    "noise": max(i["noise"] for i in st["base"]["inside"])}
        return _localized(
            [t.devour.targeted_color_score(x, op) for x in inside],   # 只认目标通道
            [t.devour.color_score(x) for x in outside],               # 区间外任何变色
            noise_floor=st["ev"]["noise"])

    return [Step(desc="同帧基线",              do=capture,  expect=lambda _t,_o: bool(st.get("base"))),
            Step(desc="应用调色/滤镜/LUT",     do=lambda _t,_o: t.executor.apply_color(op),
                 expect=applied)]

def _dist(a, b): return ((a[0]-b[0])**2 + (a[1]-b[1])**2) ** 0.5


def build_effects(t, op):
    st = {}

    async def capture(_t, _o): st["base"] = await _capture_set(t, op)

    async def applied(_t, _o):
        post = await _post_samples(t, op, st["base"])
        inside  = [t.devour.effect_metrics(a, b, op) for a, b, _ in post["inside"]]
        outside = [t.devour.effect_metrics(a, b, op) for a, b, _ in post["outside"]]

        # ① 蒙版/抠像：必须前景背景差异 + 边缘/alpha 证据同时成立，不看单像素差
        if op["kind"] in {"mask", "keying"}:
            return (all(x["fg_bg_divergence"] >= .12 and x["alpha_edge"] >= .15 for x in inside)
                    and all(x["effect_score"] <= .25 for x in outside))

        # ② 关键帧：必须是"值随时间变化"或"蒙版质心位移"——单帧变化不足以通过
        if op.get("keyframes"):
            vals = [x["effect_value"] for x in inside if x["effect_value"] is not None]
            cent = [x["mask_centroid"] for x in inside if x["mask_centroid"]]
            value_moves = len(vals) >= 3 and max(vals) - min(vals) >= KEYFRAME_MIN
            mask_moves  = len(cent) >= 3 and max(_dist(cent[0], p) for p in cent[1:]) >= MASK_MOVE_MIN
            return value_moves or mask_moves

        # ③ 普通特效：目标区间内出现，且区间外没有同等变化（局部性）
        return (all(x["effect_score"] >= .65 for x in inside)
                and all(x["effect_score"] <= .25 for x in outside)
                and _localized([x["visual_delta"] for x in inside],
                               [x["visual_delta"] for x in outside]))

    return [Step(desc="同帧基线", do=capture, expect=lambda _t,_o: bool(st.get("base"))),
            Step(desc=f"应用特效/{op['kind']}", do=lambda _t,_o: t.executor.apply_effect(op),
                 expect=applied)]


COLOR   = ActionDef("editor.color.grade",   pre=lambda t,o: t.executor.window_ok(), build=build_color)
EFFECTS = ActionDef("editor.effects.apply", pre=lambda t,o: t.executor.window_ok(), build=build_effects)

# 落 evidence（供面板下钻 / 事后核对"她是不是真改了"）
# evidence 形状（示例，非代码）:
# {"project_id":..., "op":..., "interval":[a,b],
#  "baseline_refs":[...], "result_refs":[...], "source_frame_ids":[...],
#  "metrics":{...}, "thresholds":{...}, "localized":bool, "verdict":"verified|failed|unknown"}
