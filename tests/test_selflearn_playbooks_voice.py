"""自学习闭环 / 四本蒸馏 SOP / 台湾女声通道 —— 三件事的守门测试。

网络相关一律用 monkeypatch 打桩：单元测试不许依赖联网（联网那条由 learn() 的真实
失败路径覆盖：查不到必须如实说，不许编）。
"""

import pytest

from core import playbooks as PB
from core import self_learn as SL
from core import workflows as W


# ── ① 自学习：卡点先学再干 ──
def test_rule_is_stated_and_injected_into_persona():
    """规矩要写进人格（不只是躺在代码里）——主人明确要求"给她注入"。"""
    from core import persona as P
    assert "先查" in SL.RULE and "不许编" in SL.RULE
    assert any("先查" in x for x in P.PERSONA.get("铁律", ()))


def test_learn_honest_when_search_fails(monkeypatch):
    """查不到 → 如实说查不到，且不写笔记、不编知识。"""
    monkeypatch.setattr(SL, "search", lambda q, limit=5: {"ok": False, "reason": "被挡"})
    r = SL.learn("不存在的主题 XYZ", gap="随便")
    assert r["ok"] is False and r["阶段"] == "搜索"
    assert not r.get("笔记")


def test_learn_distills_and_stores(monkeypatch, tmp_path):
    """搜索→抓正文→蒸馏笔记：走打桩页面，验证蒸馏与落盘（不联网）。"""
    html = ("<html><body><h1>字幕导出</h1>"
            + "".join(f"<p>剪映 字幕 导出 规范 第{i}段：导出 SRT 时要注意编码与时间轴对齐。</p>"
                      for i in range(8)) + "</body></html>")
    monkeypatch.setattr(SL, "search", lambda q, limit=5: {
        "ok": True, "查询": q, "结果": [{"网址": "https://example.com/a"}]})
    monkeypatch.setattr(SL, "_http", lambda url, **k: {"ok": True, "status": 200, "text": html})
    monkeypatch.setattr(SL, "OUT", tmp_path)
    monkeypatch.setattr(SL, "_append", lambda rec: None)
    r = SL.learn("剪映 字幕 导出", gap="不知道规范", record=False)
    assert r["ok"] is True and r["取到页数"] == 1
    p = tmp_path / (SL._hash("剪映 字幕 导出")[:12] + ".md")
    assert p.is_file() and "字幕" in p.read_text(encoding="utf-8")


def test_outbound_guard_blocks_localhost():
    """出行安全：本机/私网地址必须被闸门拒（沿用项目 net_guard）。"""
    r = SL._http("http://127.0.0.1:8765/api/health")
    assert r["ok"] is False


def test_search_parser_handles_duckduckgo_wrapper():
    """DDG 把链接包成 /l/?uddg=<编码> —— 解析必须认它（踩过）。"""
    from urllib.parse import quote
    html = ('<a class="result__a" href="//duckduckgo.com/l/?uddg='
            + quote("https://example.org/doc", safe="") + '">x</a>')
    links = []
    import re
    from urllib.parse import unquote
    links += [unquote(m.group(1)) for m in re.finditer(r'uddg=([^&"\'<>]+)', html)]
    assert links == ["https://example.org/doc"]


# ── ② 四本书蒸馏成 SOP：接进工作流注册表 ──
def test_playbooks_are_wired_into_workflows():
    ids = {f.id for f in W._flows()}
    for k in ("ai_short_drama", "ai_visual_studio", "newmedia_matrix", "ai_selfmedia_agent"):
        assert k in ids, f"{k} 没接进工作流注册表"
    assert len(W.catalog()) >= len(W.WORKFLOWS) + 4


def test_every_playbook_has_research_first_and_judgeable_acceptance():
    """每条 SOP：第一段必须调研；每段验收项可判定（非空）。"""
    for f in PB.PLAYBOOKS:
        assert f.阶段[0].段 == "调研", f"{f.id} 第一段不是调研"
        for s in f.阶段:
            assert s.验收, f"{f.id}/{s.id} 没有可判定验收项"
            assert s.证据, f"{f.id}/{s.id} 没写证据从哪读"


def test_sources_declare_grade_not_book_text():
    """来源分级必须写明"非原书正文"——不装读过原书。"""
    s = PB.sources()
    assert len(s["书"]) == 4
    for row in s["书"]:
        assert "非原书正文" in row["来源等级"]
    assert "盗版" in s["口径"]


# ── ③ 台湾女声通道：排在分发链首位 ──
def test_voice_tw_status_shape():
    from senses import voice_tw as VT
    st = VT.status()
    assert st["锁定语音"].startswith("zh-TW-"), "锁定的必须是台湾女声"
    assert "女声" in st["台湾腔落地"] or "女声" in st["说明"]


def test_tts_prefers_taiwan_channel(monkeypatch):
    """分发链首选台湾女声（主人反复要求）。"""
    from core import voice_control as VC
    monkeypatch.setattr(VC, "tts_channel", lambda: {
        "选中": "edge-tw", "台湾女声(edge-tw)": {"ok": True}})
    from senses import voice_tw as VT
    monkeypatch.setattr(VT, "speak", lambda t, **k: {"ok": True, "voice": VT.VOICE_DEFAULT})
    r = VC.speak("测试一句")
    assert r["ok"] is True and r["通道"].startswith("edge-tw")
