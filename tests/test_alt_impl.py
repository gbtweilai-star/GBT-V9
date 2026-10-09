# tests/test_alt_impl.py —— 替代实现真能跑（视频/音乐/混音/母带，本机 ffmpeg）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 这些用例的价值：证伪"只写一行登记就说已接"。每条都要求**真出产物 + 真证据**。
# 没有 ffmpeg 的环境自动跳过（不许假装跑过）。
import os

import pytest

from core import alt_impl as A

HAS_FFMPEG = A.have_ffmpeg()["ok"]
pytestmark = pytest.mark.skipif(not HAS_FFMPEG, reason="本机没有 ffmpeg")


def test_material_prefers_decodable_and_reports_stubs():
    mat = A.material()
    # 要么选到真能解码的素材，要么如实说没有（并给兜底说明）
    assert mat["ok"] or mat.get("兜底")
    if mat["ok"]:
        assert "devoured" in mat["来源"]
    for t in mat["试过"]:
        assert set(t) >= {"候选", "首帧可解码", "字节"}
    # 5 字节占位文件必须被判为不可解码
    stub = A.FRAME_FIRST
    if stub.is_file() and stub.stat().st_size < 2048:
        assert A.decodable(stub) is False


def test_probe_whitelist_rejects_unknown_name():
    bad = A.probe("../../etc/passwd")
    assert bad["ok"] is False and "白名单" in bad["reason"]
    with pytest.raises(ValueError):
        A.media_path("别想乱来.mp4")


def test_shorts_loop_produces_real_video():
    got = A.shortvideo_loop(title="测试成片")
    assert got["ok"] is True, got
    assert len(got["步"]) >= 2 and all(s["ok"] for s in got["步"]), got["步"]
    p = got["产物"]
    assert p["成片"] and p["sha256"] and p["字节"] and p["字节"] > 10_000
    assert (p["时长s"] or 0) > 0
    # 竖屏：9:16
    v = A.probe(p["成片"])
    assert v["video"]["w"] == 1080 and v["video"]["h"] == 1920


def test_music_loop_masters_to_target_loudness():
    got = A.music_loop()
    assert got["ok"] is True, got
    steps = {s["步"]: s for s in got["步"]}
    assert all(s["ok"] for s in got["步"])
    m = got["最终产物"]
    assert m["bytes"] and m["sha256"]
    before = m.get("母带前") or {}
    after = m.get("母带后") or {}
    assert before.get("integrated_lufs") and after.get("integrated_lufs")
    # 母带要真的把响度推到目标附近（-14 LUFS ±2）
    assert abs(float(after["integrated_lufs"]) + 14.0) <= 2.0, after
    assert steps  # 步骤证据存在


def test_manifest_records_every_run_with_evidence():
    """每次执行都要留证：主文件与滚动档案合起来，记录**只增不减**。

    真踩过：主文件写满是 rows[-200:] 直接截断 —— 旧的被静默丢掉，与"每一次都要有
    完整记录"冲突。现在溢出进 append-only 档案，一条不丢。
    """
    import json
    from core import alt_impl as _A
    def _total():
        n = len(A.manifest()["rows"])
        arch = _A.media_path("alt_manifest_archive.jsonl")
        if arch.is_file():
            n += sum(1 for ln in arch.read_text(encoding="utf-8").splitlines() if ln.strip())
        return n
    before = _total()
    got = A.synth_bed(seconds=3.0)
    assert got.get("ok") is True
    after = _total()
    assert after == before + 1, f"记录必须一条不丢（{before} → {after}）"
    rows = A.manifest()["rows"]
    assert rows, "主文件不该空"
    last = rows[-1] if rows[-1]["step"] == "synth_bed" else next(
        (r for r in reversed(rows) if r["step"] == "synth_bed"), None)
    assert last and last["ok"] is True
    assert last["evidence"].get("bytes") and last["evidence"].get("sha256")


def test_status_reports_real_artifacts():
    A.synth_bed(seconds=3.0)
    st = A.status()
    assert st["ffmpeg"]["ok"] is True
    assert len(st["执行器"]) >= 5
    assert st["产物"]["alt_bed.wav"]
    assert st["最近结果"].get("synth_bed", {}).get("ok") is True
