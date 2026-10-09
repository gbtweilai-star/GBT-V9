# tests/test_pages_live_ready.py —— 页面"随时能实战"的硬检查
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："我说的所有能力都要能随时进入实战的。"
#
# 这一组测试守的是**页面真的活着**，不是"文件里有这个字"：
#   ① 每个页面的内联 JS 必须能解析 —— 一块 JS 语法坏了整页所有卡片全哑
#      （真机踩过：Python 三引号里写 '\n' 变成真换行把 JS 字符串截断；JS 里相邻字符串
#       字面量 ''+x+'' 在 Python 允许、在 JS 是语法错误 → 后端卡永远显示"—"）
#   ② 导航只能有一份、且带胶囊布局（真踩过：skin 缺 .navbtns{display:flex} → 导航挤成一坨）
#   ③ 身体服务必须真的起来（快照采集/见证探测），否则只读读数永远"过期"
import json
import re
import subprocess

import pytest

NODE = ("const vm=require('vm');const s=Buffer.from(process.argv[1],'base64').toString('utf8');"
        "try{new vm.Script(s);console.log('OK')}catch(e){console.log('ERR '+e.message)}")


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    import panel.server as S
    return TestClient(S.app)


def _all_pages(client):
    from core.page_registry import PAGES
    pages = list(PAGES.values()) if isinstance(PAGES, dict) else list(PAGES)
    return [(p.路由, client.get(p.路由).text) for p in pages]


def _js_ok(code: str) -> tuple:
    """把一段 JS 交给 node 解析（base64 走 argv，避开一切转义坑）。"""
    import base64
    try:
        p = subprocess.run(["node", "-e", NODE,
                            base64.b64encode(code.encode("utf-8")).decode("ascii")],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"没有 node 可用：{type(exc).__name__}")
    out = (p.stdout or b"").decode("utf-8", "replace").strip()
    return (out == "OK"), out


def test_every_page_inline_js_parses(client):
    """一块 JS 坏了整页卡片全哑 —— 这是"看着有、其实不能用"的头号来源。"""
    bad = []
    for route, html in _all_pages(client):
        for i, (attrs, code) in enumerate(re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)):
            if not code.strip() or "json" in attrs.lower():
                continue
            ok, msg = _js_ok(code)
            if not ok:
                bad.append(f"{route} 块{i}: {msg[:80]}")
    assert not bad, "这些页面的脚本解析不了（整页卡片会哑）：\n" + "\n".join(bad)


def test_nav_is_single_and_has_pill_layout(client):
    """导航一份就够；胶囊布局（flex 容器）必须在 skin 里也有一份，否则总控台这类页面挤成一坨。"""
    from skills import ui_design as U
    skin, base = U.skin_css(), U.base_css()
    for css in (base, skin):
        assert ".navbtns" in css and "display:flex" in css, "两条路径都要有导航布局"
        assert "a.navbtn" in css
    html = client.get("/").text
    assert html.count("class=top") == 1, "只该有一份统一导航"
    assert "navbtns" in html
    # 手写导航已被移除（不该再出现第二排"导航"条）
    assert "▣ 总控台</a> ·" not in html


def test_body_services_start_with_per_part_status(client):
    """身体服务必须真的起：快照采集/见证探测各自独立，若整块被跳过会静默失能。"""
    import panel.server as S
    import inspect
    src = inspect.getsource(S)
    # 启动装饰器必须在（真踩过：这个装饰器被误删 → 整块服务从未启动，日志还一声不响）
    assert re.search(r'@app\.on_event\("startup"\)\s*\nasync def _start_body_services', src), \
        "_start_body_services 丢了启动装饰器"
    # 内部必须分项 try（一个子服务失败不许拖垮快照采集）
    assert 'parts["snapshot_collect"]' in src or "_ok(\"snapshot_collect\"" in src, \
        "快照采集要单独 try，别的子服务失败也不能拖垮它"
    h = client.get("/api/health").json()
    assert "collect" in h and "body_parts" in h, "/api/health 要报身体服务分项"


def test_console_cards_have_their_element_ids(client):
    """JS 写的元素 id 必须在 HTML 里存在 —— 否则脚本静默不生效（卡片永远"—"）。"""
    html = client.get("/").text
    js = "".join(re.findall(r"<script[^>]*>(.*?)</script>", html, re.S))
    ids_written = set(re.findall(r"\$\('#([a-z0-9\-]+)'\)", js)) | \
        set(re.findall(r"getElementById\('([a-z0-9\-]+)'\)", js))
    missing = sorted(i for i in ids_written
                     if f'id={i}' not in html and f'id="{i}"' not in html
                     and f"id='{i}'" not in html)
    assert not missing, f"这些 id 被 JS 写但页面里没有：{missing}"


def test_page_control_executor_on_every_page_with_consent_gate(client):
    """她能操控**任何页面**，但前提是用户同意 —— 这条守两件事：每页都有执行器 + 闸门生效。"""
    from core import page_control as PC
    PC.consent(grant=False)
    from core.page_registry import PAGES
    pages = list(PAGES.values()) if isinstance(PAGES, dict) else list(PAGES)
    missing = [p.路由 for p in pages if "id=v9ctl" not in client.get(p.路由).text]
    assert not missing, f"这些页面没有她的执行器：{missing}"
    # 未授权：一律拒绝
    r = PC.submit("go", {"path": "/cloud"})
    assert r["ok"] is False and r.get("需要授权") is True
    # 白名单：外链 / 未知动作一律拒
    assert PC.validate("go", {"path": "http://evil.example"})["ok"] is False
    assert PC.validate("eval", {"x": 1})["ok"] is False
    # 授权后：她说一句就能投递，且指令真的排进队列
    PC.consent(grant=True, by="测试")
    got = PC.execute("去云插件页")
    assert got["ok"] and got["动作"][0]["action"] == "go"
    assert got["动作"][0]["args"]["path"] == "/cloud"
    cmds = PC.next_commands()
    assert cmds and cmds[0]["action"] == "go"
    # 撤销：队列清空、再下单被拒
    PC.consent(grant=False)
    assert PC.next_commands() == []
    assert PC.submit("go", {"path": "/db"})["ok"] is False
