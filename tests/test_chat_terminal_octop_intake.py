# tests/test_chat_terminal_octop_intake.py —— 两个新对话面板 + Octop 能力接入台账 + v2 视觉层
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：补 AI 终端对话面板与 APP 独立多功能对话面板（品牌统一 GBT小土豆V9）；
#   把 Octop 原本的能力挨个接入并实现；全站按同一套未来+3D 风格设计。
import re

import pytest


@pytest.fixture()
def chat_tmp(tmp_path, monkeypatch):
    from core import chat_sessions as CS
    monkeypatch.setattr(CS, "CHAT_DIR", tmp_path / "chat")
    monkeypatch.setattr(CS, "INDEX", tmp_path / "chat" / "index.json")
    return CS


# ─────────── 会话存储：留得住、可改名、可移除（改名留底不粉碎） ───────────
def test_sessions_persist_and_first_line_becomes_title(chat_tmp):
    CS = chat_tmp
    s = CS.new(mode="大脑记忆")
    sid = s["session"]["id"]
    assert s["session"]["brand"] == "GBT小土豆V9", "品牌必须统一"
    CS.append(sid, "user", "妈妈生日要买什么")
    CS.append(sid, "assistant", "买花")
    rows = CS.sessions()
    assert rows and rows[0]["count"] == 2
    assert rows[0]["title"] == "妈妈生日要买什么", "首句当标题"
    hist = CS.history(sid)
    assert [m["role"] for m in hist] == ["user", "assistant"]


def test_session_id_is_path_safe(chat_tmp):
    CS = chat_tmp
    s = CS.new()
    sid = s["session"]["id"]
    # 恶意 id 不该能穿越出去
    bad = "../../etc/passwd"
    assert CS.append(bad, "user", "x")["ok"] is False
    assert CS.history(bad) == []
    assert CS.get(bad) is None


def test_session_mode_rename_and_drop_keeps_a_backup(chat_tmp):
    CS = chat_tmp
    sid = CS.new()["session"]["id"]
    assert CS.set_mode(sid, mode="工作流", agent="策划智能体")["session"]["mode"] == "工作流"
    assert CS.rename(sid, "重要会话")["session"]["title"] == "重要会话"
    d = CS.drop(sid)
    assert d["ok"] and CS.get(sid) is None
    assert (CS.CHAT_DIR / (sid + ".jsonl.deleted")).exists(), "移除要留底，不是立即粉碎"


def test_chat_modes_are_the_five_declared():
    from core import chat_sessions as CS
    assert set(CS.MODES) == {"日常对话", "大脑记忆", "指挥读数", "工作流", "工具"}


# ─────────── APP 多功能对话面板：五个模式都走真件 ───────────
def test_chat_page_and_api(chat_tmp):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.chat_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/chat")
    assert r.status_code == 200
    for marker in ("独立多功能对话", "会话", "GBT小土豆V9", "大脑记忆", "工作流"):
        assert marker in r.text
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', r.text)
    assert c.get("/api/chat/status").json()["品牌"] == "GBT小土豆V9"
    sid = c.post("/api/chat/new", json={"mode": "工作流"}).json()["session"]["id"]
    for mode, text in (("工作流", "短视频"), ("工具", "media.queue"), ("大脑记忆", "妈妈生日")):
        d = c.post("/api/chat/send", json={"id": sid, "text": text, "mode": mode}).json()
        assert d["ok"] and d.get("reply"), f"{mode} 模式必须给真答复"
    assert len(c.get("/api/chat/history", params={"id": sid}).json()["rows"]) >= 6


def test_chat_mode_switch_is_persisted(chat_tmp):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.chat_page import router
    app = FastAPI(); app.include_router(router); c = TestClient(app)
    sid = c.post("/api/chat/new", json={"mode": "日常对话"}).json()["session"]["id"]
    c.post("/api/chat/mode", json={"id": sid, "mode": "工具", "agent": "x"})
    got = [r for r in c.get("/api/chat/sessions").json()["rows"] if r["id"] == sid][0]
    assert got["mode"] == "工具" and got["agent"] == "x"


# ─────────── AI 终端面板：白名单派发、未知命令老实说、不碰 shell ───────────
def test_terminal_page_and_commands():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.terminal_page import router
    app = FastAPI(); app.include_router(router); c = TestClient(app)
    r = c.get("/terminal")
    assert r.status_code == 200 and "GBT小土豆V9" in r.text and "term" in r.text
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', r.text)
    assert "ask" in c.get("/api/terminal/help").json()["help"]
    d = c.post("/api/terminal/run", json={"line": "status"}).json()
    assert d["ok"] and any("闭环" in x["text"] or "大脑" in x["text"] for x in d["lines"])
    d = c.post("/api/terminal/run", json={"line": "brain 妈妈生日"}).json()
    assert any("m" in x["text"] or "命中" in x["text"] or "↳" in x["text"] or x["text"]
               for x in d["lines"])
    d = c.post("/api/terminal/run", json={"line": "rm -rf /"}).json()
    assert "不认识" in d["lines"][0]["text"], "未知命令必须老实说不认识，不许执行"
    assert "clear" in [x.get("cls") or "" for x in c.post(
        "/api/terminal/run", json={"line": "clear"}).json()["lines"]] or \
        c.post("/api/terminal/run", json={"line": "clear"}).json()["lines"][0].get("clear")
    assert c.post("/api/terminal/run", json={"line": ""}).json()["ok"] is False


def test_terminal_never_shells_out():
    """终端命令必须是**白名单派发**：源码里不出现 shell 执行。"""
    import inspect
    from panel import terminal_page as TP
    src = inspect.getsource(TP)
    needles = ("sub" + "process", "os." + "system", "shell" + "=" + "True",
               "po" + "pen", "ev" + "al(")
    for bad in needles:
        assert bad not in src, f"终端面板不该出现动态执行/命令拼接"


# ─────────── Octop 能力逐个接入台账 ───────────
def test_octop_intake_covers_all_native_pages_with_a_status():
    from core import octop_intake as OI
    from core import octop_fusion as OF
    it = OI.intake()
    assert it["原生页面数"] == len(OF.PAGES) == 64, "64 个原生页一个都不能漏"
    assert sum(it["状态分布"].values()) == 64
    for r in it["行"]:
        assert r["状态"] in ("原生实现", "站内内嵌", "待接"), r
        assert r["V9落点"].startswith("/"), f"{r['标题']} 没写 V9 落点"
        assert r["承载"], f"{r['标题']} 没写怎么承担"
    # 关键面必须是"原生实现"（不能只靠内嵌）
    native = {r["id"] for r in it["行"] if r["状态"] == "原生实现"}
    for must in ("chat", "sessions", "workbench-terminal", "agents-admin", "memory",
                 "knowledge-bases", "tasks", "skills", "plugins", "terminal", "dashboard"):
        assert must in native, f"{must} 应当是原生实现（V9 有对应页面），不该只内嵌"


def test_octop_caps_counts_are_real():
    from core import octop_intake as OI
    c = OI.caps().get("counts") or {}
    for k in ("divisions", "agents", "experts", "plugins", "skills", "v9_tools",
              "total_capabilities"):
        assert k in c, f"能力目录缺 {k}"
    assert c["agents"] >= 200 and c["v9_tools"] >= 18


def test_octop_page_shows_the_intake_ledger():
    """页面由 server 的 /octop 转发到 octop_page.octop_page()，两处都要能看到台账。"""
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.octop_page import router, octop_page
    app = FastAPI(); app.include_router(router)
    from fastapi.responses import HTMLResponse
    app.get("/octop", response_class=HTMLResponse)(octop_page)
    c = TestClient(app)
    # 路由自带 /api/octop 前缀；/octop 由 server 转发到同一个函数
    for route in ("/octop", "/api/octop/page"):
        r = c.get(route)
        assert r.status_code == 200, f"{route} 打不开"
        assert "逐个接入台账" in r.text and "原生实现" in r.text and "站内内嵌" in r.text


# ─────────── v2 视觉层：更未来 + 3D，且两条路径都有 ───────────
def test_v2_visual_layer_present_on_both_css_paths():
    from skills import ui_design as U
    for css in (U.base_css(), U.skin_css()):
        for marker in ("v9drift", "sh2-d3", "neon-edge", ".term", ".chatwrap", ".sess", ".seg"):
            assert marker in css, f"v2 视觉层缺 {marker}（base 与 skin 都必须有）"
    v = U.css_vars()
    for token in ("--sh2-d1", "--sh2-d2", "--sh2-d3", "--neon-edge", "--neon-violet"):
        assert token + ":" in v or token + " :" in v, f"缺令牌 {token}"
    # 3D 抬升与减动效保护
    assert "rotateX" in U.base_css() and "prefers-reduced-motion" in U.base_css()


def test_v2_does_not_break_token_resolution():
    import re
    from skills import ui_design as U
    defined = set(re.findall(r"(--[A-Za-z0-9_\-]+):", U.css_vars()))
    used = set(re.findall(r"var\((--[A-Za-z0-9_\-]+)", U.base_css() + U.skin_css()))
    assert not (used - defined), f"v2 引入了未定义变量：{sorted(used - defined)}"


def test_all_pages_still_inside_unified_system():
    from core import capability_panels as CP
    u = CP.ui_unify()
    assert u["体系外"] == [], f"体系外：{u['体系外']}"
    assert u["在体系内"] == u["页面数"] >= 17
    assert u["残留自写 button 规则合计"] == 0
