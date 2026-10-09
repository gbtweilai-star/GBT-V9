"""旧件退役机制（core.retire）的守门测试。

主人要求（2026-10-08）："设计新件就把旧件顺手拉到回收站；别重启之后把旧的当新的。"
这里钉住：① 干跑不动物件 ② 中间帧只在同名 webm 已合成后才算垃圾 ③ 在跑面板可枚举
④ 规矩已写进人格铁律。
"""

import pytest

from core import retire as R


def test_dry_run_does_not_touch_anything(tmp_path):
    """干跑只报告，不动任何文件（回收站也不进）。"""
    r = R.sweep(dry_run=True)
    assert set(["ok", "候选", "干跑"]).issubset(r)
    assert r["干跑"] is True


def test_candidates_only_list_known_junk_classes():
    for row in R.candidates():
        assert {"路径", "相对", "为什么", "大小MB"}.issubset(row)
        assert row["相对"], "相对路径不能为空"
        # 绝不许出现源码/别的项目
        assert not row["相对"].startswith("core/")
        assert "GBT小土豆V8" not in row["路径"]


def test_frames_are_kept_until_webm_exists(tmp_path, monkeypatch):
    """中间帧目录只有在同名 .webm 已合成（>20KB）时才算垃圾。"""
    import core.retire as RR
    monkeypatch.setattr(RR, "STATE", tmp_path)
    (tmp_path / "blender/mixamo/frames_idle").mkdir(parents=True)
    (tmp_path / "blender/mixamo/frames_idle/f_0001.png").write_bytes(b"x" * 100)
    # 没有 webm → 不算垃圾
    monkeypatch.setattr(RR, "JUNK_PATTERNS", (("blender/mixamo/frames_*", "透明动画的中间帧（同名的 .webm 已合成即是垃圾）"),))
    assert RR.candidates() == []
    # webm 到位 → 算垃圾
    (tmp_path / "blender/mixamo/idle.webm").write_bytes(b"y" * 30000)
    rows = RR.candidates()
    assert len(rows) == 1 and "frames_idle" in rows[0]["相对"]


def test_keeps_newest_and_retires_the_rest(tmp_path, monkeypatch):
    """同类过程文件只留最近几个，其余退役（别把证据也一次清光）。"""
    import core.retire as RR
    monkeypatch.setattr(RR, "STATE", tmp_path)
    (tmp_path / "preview").mkdir(parents=True)
    for i in range(9):
        p = tmp_path / "preview" / f"_look{i}.png"
        p.write_bytes(b"z" * 50)
        import os, time
        os.utime(p, (time.time() - (9 - i) * 60, time.time() - (9 - i) * 60))
    monkeypatch.setattr(RR, "JUNK_PATTERNS", (("preview/_look*.png", "过程截图（只留最近几张）"),))
    rows = RR.candidates()
    assert len(rows) == 9 - RR.KEEP_NEWEST, "应当只留 KEEP_NEWEST 张最新"


def test_running_panels_is_enumerable():
    ports = R.running_panels()
    assert isinstance(ports, list)
    for x in ports:
        assert "端口" in x and 8000 < x["端口"] < 9000


def test_rule_is_injected_into_persona():
    from core import persona as P
    rules = " ".join(P.PERSONA.get("铁律", ()))
    assert "回收站" in rules and "不许把旧的当新的" in rules
