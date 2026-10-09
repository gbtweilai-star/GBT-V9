# tests/test_page_registry.py —— 12 个按键的注册与双向绑定（路由/接口/对称性都能验）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖主人要求："这些按键能不能全部设计好，以及全部连接双向绑定好"
#   ① 12 个按键齐全、id 唯一、路由与图标都在
#   ② 每个按键声明的接口在应用里**真实存在**（不许指着不存在的接口说"绑好了"）
#   ③ 绑定成对对称（page→resource 与 resource→page 两向齐全）
#   ④ 导航由注册表生成：按键数一致、无外跳、当前页高亮
#   ⑤ 绑定登记进变更日志 + 固化可回滚
import json

import pytest

from core import deploy_ledger as J
from core import page_registry as PR


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("core.solidify.BASE", tmp_path / "solid")


def test_all_buttons_are_registered():
    """按键数：主人点名的 +「智能体对话/工作流/原生大脑/对话/AI 终端」+「AI 朋友圈」＝ 21。"""
    assert len(PR.PAGES) == 21, '在册页面数（新增 AI 朋友圈后为 21）'
    ids = [p.id for p in PR.PAGES]
    assert len(set(ids)) == 21
    assert any(p.路由 == "/api/friends/page" for p in PR.PAGES)   # AI 朋友圈（EigenFlux 只读接入）
    assert any(p.路由 == "/agents" for p in PR.PAGES)
    assert any(p.路由 == "/workflow" for p in PR.PAGES)      # 工作流独立页必须有独立按键
    assert any(p.路由 == "/brain" for p in PR.PAGES)         # 原生大脑独立页
    assert any(p.路由 == "/chat" for p in PR.PAGES)          # APP 多功能对话
    assert any(p.路由 == "/terminal" for p in PR.PAGES)      # AI 终端对话
    assert any(p.路由 == "/blueprint" for p in PR.PAGES)     # 3D 蓝图
    assert any(p.路由 == "/voice" for p in PR.PAGES)         # 语音操控中心
    for p in PR.PAGES:
        assert p.路由.startswith("/") and p.图标 and p.标题 and p.一句说明 and p.分组
        assert p.接口 and p.资源 and p.反向入口          # 双向绑定三件套都不能缺


def test_every_declared_api_really_exists():
    a = PR.audit()
    assert a["接口缺失"] == [], a["接口缺失"]
    assert a["路由问题"] == [], a["路由问题"]
    assert a["ok"] is True
    assert a["路由表规模"] > 100


def test_bindings_are_symmetric_pairs():
    b = PR.bindings()
    assert len(b) >= 60                                  # 资源绑定 + 同组页面互联
    for key, v in b.items():
        assert "↔" in key
        assert v["page_to_resource"] and v["resource_to_page"]
        assert v["对称"] is True
    assert PR.audit()["不对称绑定"] == []


def test_nav_is_generated_from_registry_without_external_links():
    from skills.ui_design import nav_html
    nav = nav_html("/kits")
    for p in PR.PAGES:
        assert p.标题 in nav, p.标题
        assert f'href="{p.路由}"' in nav
    assert 'class="navbtn on"' in nav or "class='navbtn on'" in nav   # 当前页高亮
    assert "target=_blank" not in nav and "http://" not in nav and "https://" not in nav
    assert nav.count("navbtn") >= 12


def test_bind_all_registers_and_solidifies():
    r = PR.bind_all()
    assert r["ok"] is True
    assert r["登记条数"] == len(PR.deploy_rows()) >= 90
    assert r["固化"]["ok"] and r["固化"]["name"] == "page_registry"
    rows = J.recent(400)["rows"]
    # 12 条是"页面本身"（键里没有 ↔），其余是成对绑定
    page_rows = [x for x in rows if x["kind"] == "deploy" and x["where"].startswith("page:")
                 and "↔" not in x["where"]]
    assert len(page_rows) == len(PR.PAGES)
    assert any(x["kind"] == "solidify" and "page_registry" in x["where"] for x in rows)
    # 固化可回滚
    from core import solidify as S
    PR.bind_all()                                        # 第二次 → 两版
    rb = S.rollback("page_registry")
    assert rb["ok"] and rb["verified"] is True


def test_scan_records_page_changes_with_where():
    PR.bind_all()
    first = PR.scan()
    assert first["items"] >= 90 and len(first["added"]) >= 90
    second = PR.scan()
    assert second["added"] == [] and second["changed"] == [] and second["removed"] == []
    # 人为改一句说明 → 下一次扫描记成"修改"，带 before/after
    page = [p for p in PR.PAGES if p.id == "ble"][0]
    old = page.一句说明
    page.一句说明 = old + "（改）"
    try:
        got = PR.scan()
    finally:
        page.一句说明 = old
    assert "page:ble" in got["changed"], got["changed"][:3]
    rows = J.recent(400)["rows"]
    mod = [x for x in rows if x["kind"] == "modify" and x["where"] == "page:ble"][0]
    assert mod["before"]["一句说明"] == old and mod["after"]["一句说明"].endswith("（改）")


def test_status_is_serializable_and_complete():
    st = PR.status()
    assert st["按键数"] == len(PR.PAGES) and len(st["按键"]) == len(PR.PAGES), "按键数必须等于在册页面数"
    assert st["审计"]["ok"] is True
    assert json.dumps(st, ensure_ascii=False)            # 面板直接吐 JSON
    for item in st["按键"]:
        assert item["绑定向"] == "双向" and item["接口数"] >= 1
