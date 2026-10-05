# tests/test_devour_wrappers.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import numpy as np, pytest
from body.devour_wrappers import DevourVerify

def _img(seed, w=64, h=64):
    return np.random.default_rng(seed).integers(0, 255, (h, w, 3), dtype=np.uint8)

class FakeRead:
    def __init__(self, m): self.m = m
    async def __call__(self, ref): return self.m[ref]

async def _pair(before, after, region="inside", conf="high", sid_b="s1", sid_a="s1"):
    return ([{"ref":"b","source_frame_id":sid_b,"identity_confidence":conf}],
            [{"ref":"a","source_frame_id":sid_a,"identity_confidence":conf}],
            [{"sample_id":"x","region":region,"before_s":1,"after_s":1}])

@pytest.mark.asyncio
async def test_localized_change_passes():
    d = DevourVerify({"noise_threshold":6.0,"effect_floor":0.05,"outside_max":0.03,"local_ratio":3.0})
    a = _img(1); b = a.copy(); b[10:30,10:30] = (255,0,0)
    bl, af, tm = await _pair(a, b); bl[0]["ref"]="b"; af[0]["ref"]="a"
    m = await d.delta_metrics_for_pair(bl, af, tm, read=FakeRead({"b":a,"a":b}), noise_threshold=6.0)
    m["inside"] = m["inside"]*3; m["inside_mean"] = 0.12; m["outside_max"] = 0.01   # 3 点达标
    v, spec = d.verdict_and_spec({"op":"color","start_s":1,"end_s":3}, m)
    assert v == "verified" and spec["threshold"] == 6.0

@pytest.mark.asyncio
async def test_identity_mismatch_is_unknown():
    d = DevourVerify({"noise_threshold":6.0,"effect_floor":0.05,"outside_max":0.03,"local_ratio":3.0})
    a = _img(2); b = a.copy(); b[:5,:5]=0
    bl, af, tm = await _pair(a, b, sid_b="s1", sid_a="s2")     # 源帧身份不一致
    m = await d.delta_metrics_for_pair(bl, af, tm, read=FakeRead({"b":a,"a":b}), noise_threshold=6.0)
    v, _ = d.verdict_and_spec({"op":"color","start_s":1,"end_s":3}, m)
    assert v == "unknown"                                     # 不是 failed

@pytest.mark.asyncio
async def test_keyframe_single_sample_is_unknown():
    d = DevourVerify({"noise_threshold":6.0,"effect_floor":0.05,"outside_max":0.03,"local_ratio":3.0})
    m = {"inside":[{"effect_score":.1,"effect_value":.1,"mask_centroid":[0,0],
                    "fg_bg_divergence":.2,"alpha_edge":.2,"visual_delta":.1}],
         "outside":[], "structural":[], "unverifiable":[], "inside_mean":.1, "outside_max":0.0}
    v, _ = d.verdict_and_spec({"op":"keyframe","start_s":1,"end_s":3}, m)
    assert v == "unknown"                                     # 单帧不足以判"生效"

def test_evidence_row_digest_deterministic():
    d = DevourVerify({"noise_threshold":6.0})
    s = {"op":"color","verdict":"verified","metrics":{"inside_mean":0.12}}
    bl=[{"ref":"b"}]; af=[{"ref":"a"}]
    r1 = d.evidence_row("t","op",s and {"op":"color"}, "verified", bl, af, s, now_epoch=1000)
    r2 = d.evidence_row("t",{"op":"color"}, "verified", bl, af, s, now_epoch=1000)
    assert r1[6] == r2[6] and r1[0] == r2[0]                  # digest/evidence_id 相同

def test_whole_frame_change_fails_locality():
    d = DevourVerify({"noise_threshold":6.0,"effect_floor":0.05,"outside_max":0.03,"local_ratio":3.0})
    m = {"inside":[{}]*3, "outside":[{}], "structural":[], "unverifiable":[],
         "inside_mean":0.30, "outside_max":0.28}              # 区间外也大变
    v, _ = d.verdict_and_spec({"op":"effects","start_s":1,"end_s":3}, m)
    assert v == "failed"                                      # 不局部 → 失败
