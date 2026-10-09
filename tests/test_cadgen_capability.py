"""text-to-cad（cadgen）能力位的测试：可用性、越界拒绝、格式校验、清单形状。

不联网、不跑 CAD 内核（重的部分靠冒烟件手工验过），只钉"接线与护栏"。
"""

import shutil
from pathlib import Path

import pytest

from core import cadgen as C


def test_status_shape():
    st = C.status()
    assert set(["cadgen", "可用", "pin", "产物目录", "能力"]).issubset(st)
    assert st["pin"].startswith("cadgen==")


def test_build_rejects_outside_state_cad(tmp_path):
    """越界写：state/cad 之外的脚本必须被拒（钩子第一关就该拦）。"""
    outside = tmp_path / "evil.py"
    outside.write_text("from cadgen import build123d as bd\n", encoding="utf-8")
    if not C._exe():
        pytest.skip("本机没有 cadgen")
    with pytest.raises(Exception):
        C.build(outside)


def test_build_rejects_missing_file():
    with pytest.raises(Exception):
        C.build("state/cad/__not_here__.py")


def test_convert_rejects_unknown_format():
    r = C.convert("state/cad/smoke/STEP/bracket.step", "obj")
    assert r["ok"] is False and "不支持" in r["原因"]


def test_convert_missing_step():
    r = C.convert("state/cad/smoke/STEP/__nope__.step", "stl")
    assert r["ok"] is False


def test_list_parts_shape():
    parts = C.list_parts()
    assert isinstance(parts, list)
    for p in parts:
        assert {"名", "相对", "大小KB"} == set(p)
        assert p["相对"].endswith((".step", ".stp", ".stl", ".glb", ".3mf"))


def test_smoke_artifacts_exist_if_built():
    """冒烟件若已构建，STEP 必须非空（证明这条链真出过件）。"""
    step = Path("state/cad/smoke/STEP/bracket.step")
    if not step.is_file():
        pytest.skip("冒烟件还没构建")
    assert step.stat().st_size > 1000


def test_doctor_reports_kernel_when_available():
    if not (C._exe() and shutil.which("uvx")):
        pytest.skip("cadgen/uvx 不在")
    st = C.status()
    assert "doctor" in st
