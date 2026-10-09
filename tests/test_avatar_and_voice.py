# tests/test_avatar_and_voice.py —— 数字人骨架 + 语音通道（台湾腔落地方式要如实）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 骨架：18 个关节、父子链完整、FK 算得出坐标（9 动作 × 多相位不 NaN）
#   ② 动作库齐全；情绪→动作映射对得上
#   ③ 说话态：speaking=True 时口型有开合（>0），不说不张嘴
#   ④ 语音通道：SAPI 可用性/语音选择是真探测；台湾腔落地方式必须写明（克隆 or 韵律）
import json

import pytest

from core import avatar_rig as AR


def test_skeleton_is_complete_and_linked():
    assert len(AR.JOINTS) == 18
    for j, meta in AR.JOINTS.items():
        if meta["parent"] is not None:
            assert meta["parent"] in AR.JOINTS, f"{j} 的父关节不存在"
        assert meta["len"] >= 0
    # 必有的全身部位
    for part in ("hip", "spine", "neck", "head", "armL", "armR", "foreL", "foreR",
                 "thighL", "thighR", "shinL", "shinR", "footL", "footR"):
        assert part in AR.JOINTS, part


def test_all_clips_produce_valid_poses():
    assert set(AR.CLIPS) >= {"idle", "wave", "bow", "nod", "clap", "think", "walk",
                             "cheer", "point"}
    for clip in AR.CLIPS:
        for t in (0.0, 0.13, 0.5, 0.77):
            pos = AR.joints(AR.pose(clip, t))
            assert len(pos) == len(AR.JOINTS)
            for x, y in pos.values():
                assert isinstance(x, float) and isinstance(y, float)
                assert -1e4 < x < 1e4 and -1e4 < y < 1e4
        svg = AR.to_svg(AR.pose(clip, 0.3))
        assert svg.startswith("<svg") and svg.count("circle") > 10


def test_mood_maps_to_clip():
    assert AR.clip_for_mood("乐") == "cheer"
    assert AR.clip_for_mood("哀") == "bow"
    assert AR.clip_for_mood("思考") == "think"
    assert AR.clip_for_mood("未知情绪") == "idle"


def test_speaking_opens_mouth_and_rest_is_closed():
    quiet = AR.speak_state("idle", 0.2, speaking=False)
    talk = AR.speak_state("idle", 0.2, speaking=True, amp=0.8)
    assert quiet["mouth"] == 0.0
    assert talk["mouth"] > 0.05 and talk["speaking"] is True
    assert 'rx=' in talk["svg"]                      # 口型真的画出来了


def test_every_clip_actually_moves_not_just_valid_coords():
    """真机教训：动作名加了、动作数据没加 → 坐标合法但**一动不动**（静态站姿），
    旧测试只查"坐标是不是数字"所以照样通过。这里必须查角度真的随相位变化。"""
    phases = (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.8, 0.95)
    frozen = []
    for clip in AR.CLIPS:
        base = AR.pose(clip, 0.0)
        spread = max(
            max(abs(p[k] - base[k]) for k in set(p) | set(base) if k != "mouth")
            for p in (AR.pose(clip, t) for t in phases)
        )
        if spread < 0.5:
            frozen.append(clip)
    assert not frozen, f"这些动作是僵死的（页面上一动不动）：{frozen}"


def test_held_gesture_clips_ramp_in_and_breath_layer_is_present():
    """摆姿势类动作（敬礼/竖大拇指/托腮…）必须靠包络起势，且任何姿势都要有呼吸微动。"""
    from core import avatar_motion as M
    assert M._HELD                                     # 集合非空
    for c in sorted(M._HELD):
        assert M.offsets(c, 0.0).get("armR", 0.0) == 0.0        # 起势从 0 开始（可循环）
        assert abs(M.offsets(c, 0.45).get("armR", 0.0)) > 5      # 保持段抬到位
    # 呼吸层：连最静的 idle 之外也有 spine 微动
    assert abs(M.offsets("tired", 0.25).get("spine", 0.0)
               - M.offsets("tired", 0.0).get("spine", 0.0)) > 0.1


def test_blink_actually_closes_eyes():
    a = AR.speak_state("idle", 0.1, blink=0.0)
    b = AR.speak_state("idle", 0.1, blink=1.0)
    assert a["blink"] == 0.0 and b["blink"] == 1.0
    assert a["svg"] != b["svg"]                        # 眼高真的变了
    assert 'ry="4.2"' in a["svg"]                      # 睁眼
    assert 'ry="4.2"' not in b["svg"]                  # 闭眼
    # 按墙钟算出来的眨眼要落在 [0,1]；眨眼是短事件（~0.16s / 3.2s），必须密集采样
    vals = [AR.blink(i * 0.01, seed=0.3) for i in range(800)]     # 8 秒 @100Hz
    assert all(0.0 <= v <= 1.0 for v in vals)
    assert min(vals) == 0.0 and max(vals) > 0.9                    # 有睁到底也有闭到底
    closed = sum(1 for v in vals if v > 0.2) / len(vals)
    assert 0.01 < closed < 0.2, f"眨眼占比 {closed:.3f} 不像正常眨眼"   # 5% 左右，不是一直闭着


def test_sequences_play_step_by_step():
    seqs = AR.sequences()
    assert set(seqs) >= {"问候", "欢迎", "感谢", "加油", "告别", "自信"}
    for name in seqs:
        assert len(seqs[name]) >= 2
    mid = AR.sequence_state("加油", 0.5)
    assert mid["combo"] == "加油" and "/" in mid["step"]
    assert mid["clip"] in seqs["加油"]
    # 不认识的名字 → 当单动作处理，不炸
    assert AR.sequence_state("不存在的组合", 0.3)["clip"] == "不存在的组合"


def test_status_reports_details_not_just_names():
    st = AR.status()
    assert st["骨架关节数"] == 18
    assert set(st["动作库"]) == set(AR.CLIPS)
    assert st["动作数"] == len(AR.CLIPS) >= 32
    assert len(st["动作细节"]) == len(AR.CLIPS)
    assert set(st["动作分类"].values()) == {"社交", "情绪", "日常"}
    assert st["组合动作"] == AR.sequences()
    assert "眨眼" in st
    assert json.dumps(st, ensure_ascii=False)


def test_avatar_api_exposes_combo_and_blink():
    import asyncio
    from panel import capability_page as CP
    d = asyncio.run(CP.api_avatar_state(clip="", combo="问候", t=0.4))
    assert d["combo"] == "问候" and d["关节数"] == 18 and "svg" in d
    d2 = asyncio.run(CP.api_avatar_state(clip="salute", t=0.3))
    assert d2["clip"] == "salute" and "blink" in d2


def test_sapi_voice_pick_is_real_and_taiwan_state_is_honest():
    from senses import voice_sapi as VS
    got = VS.voices()
    assert set(got) >= {"ok", "voices", "reason"}
    pick = VS.pick_voice()
    # 有没有 zh-TW 语音库都要如实说；选中的语音必须是系统真的报出来的那些
    if pick.get("ok"):
        names = [v["name"] for v in got.get("voices") or []]
        assert pick["voice"] in names
        assert isinstance(pick["taiwan"], bool)
    st = VS.status()
    assert "台湾腔落地" in st
    assert ("克隆" in st["台湾腔落地"]) or ("韵律" in st["台湾腔落地"])
    # 装台湾语音的方法要能给出来（否则用户没法自己补齐）
    assert "台灣" in st["装台湾语音的方法"] or "台湾" in st["装台湾语音的方法"]


def test_taiwanize_and_prosody_style_exist():
    from senses.voice_sapi import taiwanize
    assert taiwanize("屏幕上的视频信息质量") != "屏幕上的视频信息质量"   # 用词被台湾化
    from body.prosody import STYLES, DEFAULT_STYLE
    assert DEFAULT_STYLE == "文静台湾腔"
    st = STYLES["文静台湾腔"]
    assert st.rate < 1.0 and st.pitch > 0 and st.softeners


def test_tts_server_status_exposes_taiwan_path(monkeypatch):
    import senses.voice_server as S
    st = S.status()
    assert "台湾腔" in st and "合法中文方言" in st
    assert "不含台湾" in st["合法中文方言"]           # 如实：内置方言没有台湾
    # 参考音接入要给得能用：说清放哪里 + 用哪个接口指定
    ref = st["参考音接入"]
    assert "voice_ref" in ref and "/api/voice/ref" in ref


def test_asr_channel_is_honest_about_what_is_installed():
    """听：免费离线 ASR。装了中文识别器就说可用，没装就说清怎么装 —— 不许含糊。"""
    from senses import voice_sapi as VS
    got = VS.recognizers()
    assert set(got) >= {"ok", "recognizers", "reason"}
    for r in got.get("recognizers") or []:
        assert r["culture"] and r["id"]
    st = VS.asr_status(fresh=True)
    assert st["离线"] is True and st["占显存"] == 0 and st["需凭据"] is False
    if st["可用"]:
        assert st["语言"].lower().startswith("zh")
        assert st["识别器"] in [r["id"] for r in got["recognizers"]]
    else:
        assert "识别器" in st["说明"] or "没有" in st["说明"]
    assert "装中文识别器的方法" in st and "语音" in st["装中文识别器的方法"]
    # 缓存必须真的生效（面板每几秒轮询一次，不能每次都起 PowerShell）
    a, b = VS.asr_status(), VS.asr_status()
    assert a["可用"] == b["可用"]


def test_asr_round_trip_transcribes_its_own_tts_when_available():
    """闭环自证：TTS 合成 → ASR 转写回来 → 比对。没有中文识别器就跳过（不装样子）。"""
    from senses import voice_sapi as VS
    if not VS.asr_status(fresh=True)["可用"]:
        pytest.skip("本机没有中文听写识别器")
    r = VS.selftest("今天天气不错")
    assert r["stage"] == "done"
    assert r["wav_bytes"] > 1024                       # 真的合成了音频
    assert r["转写"], "转写不能是空的"
    assert r["ok"] and r["命中率"] >= 0.5               # 自己的音自己听得出来
