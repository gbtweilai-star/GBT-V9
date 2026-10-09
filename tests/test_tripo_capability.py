# tests/test_tripo_capability.py —— 云上图生3D 能力位：只读状态 / 安全 / 接口
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律：测试**不打云 API、不消耗额度**（联网生成类只在显式开启时才跑）。
import os
import re
from pathlib import Path

import pytest

from core import tripo as T


def test_status_shape_and_no_secret_leak():
    """状态里不能出现密钥本体（只报来源与余额）。"""
    st = T.status()
    assert set(("CLI", "配置", "密钥来源", "路线")) <= set(st)
    key = T._profile_key()
    blob = str(st)
    assert (not key) or (key not in blob), "状态里泄漏了密钥"
    assert "tsk_" not in blob


def test_clean_env_drops_client_id():
    """跑 CLI 时必须去掉 TRIPO_API_KEY（那个 tcli_ 客户端 ID 会让服务端 401）。"""
    os.environ["TRIPO_API_KEY"] = "tcli_fake_for_test"
    try:
        env = T._clean_env()
        assert "TRIPO_API_KEY" not in env
    finally:
        os.environ.pop("TRIPO_API_KEY", None)


def test_safe_url_blocks_ssrf_targets():
    """URL 边界：只允许白名单域名 + /v2/openapi 路径；其它一律拒。"""
    for bad in ("/v2/openapi/../../etc/passwd", "//evil.com/x", "/v2/openapi/x?y=1"):
        with pytest.raises(ValueError):
            T._safe_url(bad)
    ok = T._safe_url("/v2/openapi/user/balance")
    assert ok.startswith("https://api.tripo3d.ai/v2/openapi/")


def test_render_frame_name_is_traversal_safe():
    """渲染取图只认 state/tripo/render 下的白名单文件名。"""
    pat = re.compile(r"[A-Za-z0-9_]+_(hero|face|turn\d{2})\.png")
    assert pat.fullmatch("front2_hero.png") and pat.fullmatch("mv_turn03.png")
    for bad in ("../../secret.png", "front2_hero.png.exe", "a/b_hero.png", "hero.png", ""):
        assert not pat.fullmatch(bad)


def test_endpoints_expose_status_labels_and_frame_guard():
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    st = c.get("/api/tripo/status").json()
    assert "路线" in st and "密钥来源" in st
    lb = c.get("/api/tripo/labels").json()["标签"]
    assert isinstance(lb, list) and all("." not in x for x in lb)
    assert c.get("/api/tripo/frame?name=../../x.png").status_code == 404
    assert c.get("/api/tripo/frame?name=hero.png").status_code == 404
    ms = c.get("/api/tripo/models").json()["模型"]
    assert isinstance(ms, list)
    if ms:
        assert all(".." not in m["路径"] for m in ms)


def test_page_shows_cloud_3d_card():
    from fastapi.testclient import TestClient
    from panel.server import app
    html = TestClient(app).get("/digital-human").text
    for need in ("云端图生3D", 'id="tvturn"', 'id="tvhero"', 'id="tvface"', "tvRender"):
        assert need in html, f"页面缺 {need}"


@pytest.mark.skipif(os.environ.get("V9_TRIPO_LIVE") != "1",
                    reason="联网生成会消耗云端额度：设 V9_TRIPO_LIVE=1 才跑")
def test_live_status_only():
    st = T.status()
    assert st.get("可用") is True
