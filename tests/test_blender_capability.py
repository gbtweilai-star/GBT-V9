# tests/test_blender_capability.py —— blender 原生能力位：状态 / 数据 / 脚本 / 真跑
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 契约（2026-10-07 主人批准建位）：
#   · 只走本机已装的 blender（找不到就如实报缺，不猜路径、不下载）
#   · 骨架/动作数据来自我们已验证的 FK 与动作库（2D 与 3D 共用同一套真值）
#   · 落盘只在 state/blender/；argv 全字面量 + shell=False；不出网
import json
import shutil
from pathlib import Path

import pytest

from core import blender as B
from core import avatar_motion as M


def _blender_or_skip():
    if not B.blender_exe():
        pytest.skip("本机没有 blender")


def test_status_is_honest_when_blender_missing(monkeypatch):
    monkeypatch.setattr(B, "CANDIDATES", (r"C:\definitely\not\here\blender.exe",))
    st = B.status()
    assert st["可用"] is False
    assert "未找到" in st["blender"]
    assert B.deploy()["ok"] is False


def test_status_reports_real_blender_and_route():
    _blender_or_skip()
    st = B.status()
    assert st["可用"] is True
    assert Path(st["blender"]).is_file()
    assert "Blender" in st["版本"]
    assert "图生3D" in st["不在册"], "必须如实写明图生3D这条路走不通"


def test_payload_has_full_skeleton_and_frames():
    payload = B._rest_and_frames("wave", 8)
    names = [b["name"] for b in payload["bones"]]
    assert len(names) == 18 and "head" in names and "footL" in names
    for b in payload["bones"]:
        assert "px" in b and "pz" in b, "每根骨头都要带父关节位置（否则骨头会接错）"
    assert len(payload["frames"]) == 8
    # 每帧每根骨头都要有旋转增量，且是**相对父骨**的（否则骨链会叠加）
    for rots in payload["frames"]:
        assert set(rots) == set(names) - {"hip"}
        assert all(isinstance(v, float) for v in rots.values())


def test_payload_moves_with_the_action():
    a = B._rest_and_frames("wave", 6)["frames"]
    b = B._rest_and_frames("cheer", 6)["frames"]
    diff = max(abs(a[i][k] - b[i][k]) for i in range(6) for k in a[i])
    assert diff > 0.2, "两个动作的关节角度必须真的不一样"


def test_script_is_selfcontained_and_gated():
    payload = B._rest_and_frames("idle", 4)
    script = B._bpy_script(payload, out_png="x.png", out_glb="x.glb")
    for need in ("import bpy", "V9_BLENDER_OK", "export_scene.gltf",
                 "vertex_groups.new", "rotation_quaternion", "keyframe_insert",
                 "use_dof", "AgX"):
        assert need in script, f"脚本缺 {need}"
    assert "import requests" not in script and "urllib" not in script, "脚本不许联网"
    assert len(script) < 90000


@pytest.mark.skipif(shutil.which("node") is None, reason="无 node")
def test_generated_script_is_valid_python():
    """生成的 bpy 脚本本身必须是合法 Python（否则 blender 只能白跑一趟）。"""
    import subprocess
    import tempfile
    payload = B._rest_and_frames("idle", 4)
    script = B._bpy_script(payload, out_png="x.png", out_glb="x.glb")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(script)
        tmp = f.name
    try:
        r = subprocess.run(["python", "-m", "py_compile", tmp], capture_output=True,
                           text=True, timeout=60, shell=False)
    finally:
        Path(tmp).unlink(missing_ok=True)
    assert r.returncode == 0, r.stderr[:400]


def test_real_build_produces_png_and_glb():
    """集成：真跑一次 blender，必须产出非空 PNG 与 GLB（钩子四步全过）。"""
    _blender_or_skip()
    r = B.build("idle", frames=6, timeout=420)
    assert r["ok"] and r["钩子"]["通过"] is True and r["钩子"]["步数"] == 4
    png, glb = Path(r["渲染"]), Path(r["模型"])
    assert png.is_file() and png.stat().st_size > 2000
    assert glb.is_file() and glb.stat().st_size > 2000
    assert png.parent.name == "blender", "产物只能落在 state/blender/ 下"
    json.dumps(r, ensure_ascii=False)


def test_rotation_deltas_are_degrees_not_radians():
    """★真踩过：atan2 出的是弧度，_wrap180() 却按度写 —— 大动作会被压成"几乎不动"。

    判据：动作的关节增量必须是**度**量级（欢呼的手臂要 > 90°），不是 ±3 那种弧度残值。
    """
    cheer = B._rest_and_frames("cheer", 12)["frames"]
    biggest = max(abs(v) for rots in cheer for v in rots.values())
    assert biggest > 90.0, f"动作增量只有 {biggest:.2f}：多半又把弧度当度用了"
    idle = B._rest_and_frames("idle", 12)["frames"]
    idle_max = max(abs(v) for rots in idle for v in rots.values())
    assert idle_max < 15.0, f"待机不该有大动作（{idle_max:.1f}°）"
    # 左右镜像动作的增量应互为相反数（对称性）
    f = cheer[3]
    assert abs(f["armL"] + f["armR"]) < 1.0, f"左右不镜像：{f['armL']:.1f} vs {f['armR']:.1f}"


def test_wrap180_keeps_shortest_rotation():
    assert B._wrap180(370.0) == 10.0
    assert B._wrap180(-190.0) == 170.0
    assert abs(B._wrap180(-212.5)) <= 180.0


def test_preview_frame_path_is_traversal_safe():
    """预览取图必须只认 preview 目录里的 <动作>_[af]NN.png，别的（含穿越）一律 None。"""
    for bad in ("../../secret.png", r"..\..\x.png", "wave_a0.png", "wave_x00.png",
                "wave_a00.png.exe", "/etc/passwd", "wave_f1.png", ""):
        assert B.frame_path(bad) is None, f"不该放行：{bad}"
    assert B.frame_path("wave_a00.png") is None or B.frame_path("wave_a00.png").is_file()


def test_preview_files_exist_after_render():
    """转台与序列图必须真的存在（先跑过 preview 才校验，否则跳过）。"""
    t = sorted(B.PREVIEW_DIR.glob("*_a*.png"))
    if not t:
        pytest.skip("还没渲染过转台")
    s = sorted(B.PREVIEW_DIR.glob("*_f*.png"))
    assert len(t) >= 4 and len(s) >= 4
    assert min(p.stat().st_size for p in t + s) > 800


def test_blender_endpoints_expose_status_and_frame():
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    st = c.get("/api/blender/status").json()
    assert "可用" in st and isinstance(st.get("动作"), list)
    assert c.get("/api/blender/frame?name=../../x.png").status_code == 404
    t = sorted(B.PREVIEW_DIR.glob("*_a*.png"))
    if t:
        r = c.get(f"/api/blender/frame?name={t[0].name}")
        assert r.status_code == 200 and r.content[:4] == bytes([0x89]) + b"PNG"


def test_digital_human_page_has_3d_viewer():
    """3D 展示卡：现在是**云端图生3D 模型**的转台（本地程序化那版已撤下，见 core/tripo.py）。"""
    from fastapi.testclient import TestClient
    from panel.server import app
    html = TestClient(app).get("/digital-human").text
    for need in ("云端图生3D", 'id="tvturn"', 'id="tvhero"', 'id="tvface"',
                 "/api/tripo/frame", "tvRender"):
        assert need in html, f"页面缺 {need}"
    assert "本地影棚" in html and "数字人形象" in html
