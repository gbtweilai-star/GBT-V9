"""数字人元数据位（core.avatar_meta）的守门测试。

主人要求（2026-10-08）："她的元数据你是不是都没接入啊！"
这里钉住：① 元数据是真读数（ffprobe/哈希/扫盘）且字段齐 ② 版本是内容哈希、会随资产变
③ 校验逐项可判定、缺什么都列出来 ④ 接口能读到她是谁。
"""

import json
import re

import pytest

from core import avatar_meta as AM


@pytest.fixture(scope="module")
def meta():
    return AM.build(record=False)


def test_meta_has_identity_persona_voice_looks(meta):
    for k in ("id", "名", "人格", "嗓子", "形象", "动作", "版本", "built_at", "校验", "口径"):
        assert k in meta, f"元数据缺字段 {k}"
    assert meta["id"] == "v9-human"
    assert meta["人格"].get("名"), "人格必须有名字（来自 core.persona 真读数）"
    assert str(meta["嗓子"].get("锁定语音", "")).startswith("zh-TW-"), "嗓子必须是台湾女声"


def test_version_is_content_hash():
    v = AM.build(record=False)["版本"]
    assert re.fullmatch(r"[0-9a-f]{12}", v), "版本必须是内容哈希（12 位十六进制）"


def test_clip_rows_are_real_readings(meta):
    assert len(meta["动作"]) == len(AM.CLIPS) == 11
    for c in meta["动作"]:
        assert {"名", "文件", "有件", "字节", "海报", "透明", "哈希"}.issubset(c)
        if c["有件"]:
            assert c["字节"] > 20000 and c["哈希"], "有件就必须有字节与哈希（真读数）"
            assert c.get("fps") and c.get("时长s"), "有件就必须读到 fps/时长"


def test_alpha_judgement_uses_alpha_mode_not_pix_fmt():
    """透明判据必须是 alpha_mode=1（VP9 的 alpha 是独立平面，pix_fmt 仍是 yuv420p）。"""
    from pathlib import Path
    idle = Path("state/tripo/render/idle.webm")
    if not idle.is_file():
        pytest.skip("没有 idle.webm")
    row = AM._probe(idle)
    assert row.get("透明") is True
    assert row.get("像素格式") == "yuv420p"


def test_checks_are_judgeable_and_list_gaps(meta):
    chk = meta["校验"]
    assert chk["总项"] >= 40 and isinstance(chk["缺"], list)
    assert chk["通过"] + len(chk["缺"]) == chk["总项"], "通过+缺 必须等于总项"
    for row in chk["明细"]:
        assert set(["项", "过", "读数"]) == set(row)


def test_api_route_exists():
    from panel import voice_page as VP
    paths = {getattr(r, "path", "") for r in VP.router.routes}
    assert "/api/avatar/meta" in paths, "元数据必须能被接口读到（接入，不是躺着）"
