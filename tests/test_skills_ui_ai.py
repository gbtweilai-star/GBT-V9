# tests/test_skills_ui_ai.py —— 术语 Skill / UI 设计 Skill（审美自检）/ AI 指挥中心问询
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import asyncio

import pytest

from skills import terminology as T
from skills import ui_design as U


def run(coro):
    return asyncio.run(coro)


# ═══ 术语 Skill：教你怎么说 ═══
def test_owner_plain_words_map_to_terms():
    r = T.translate("鼠标放上去那张大一点")
    assert r["matched"] is True
    assert r["term"] == "悬停抬起 + 邻位让位"
    assert r["intent"] == "hover_lift_yield" and r["skill"] == "ui.design"
    assert r["params"]["lift_px"] == 4 and r["params"]["neighbors"] == "yield"


def test_unknown_phrase_is_not_guessed():
    r = T.translate("把月亮摘下来装兜里")
    assert r["matched"] is False and r["reason"] == "no_term"
    assert r["candidates"] and "怎么说" in r["hint"] or "说清" in r["hint"]


def test_suggest_teaches_how_to_say_it():
    s = T.suggest("放上去那长大", limit=3)
    assert s and s[0]["term"] == "悬停抬起 + 邻位让位"
    assert s[0]["say"]                      # 必须给出更准的说法


def test_explain_both_directions():
    plain = T.explain("hover_lift_yield")
    assert plain["term"] == "悬停抬起 + 邻位让位" and plain["skill"] == "ui.design"
    assert T.explain("不存在的术语")["error"]


def test_catalog_groups_by_skill():
    c = T.catalog()
    assert c["count"] >= 15 and "ui.design" in c["by_skill"]
    assert any(t["term"] == "悬停抬起 + 邻位让位" for t in c["by_skill"]["ui.design"])


def test_extra_terms_from_env(monkeypatch):
    monkeypatch.setenv("TERMS_EXTRA_JSON",
                       '[{"term":"老板口吻","intent":"boss_tone","skill":"voice",'
                       '"aliases":["给我整利索点"]}]')
    assert T.translate("给我整利索点")["intent"] == "boss_tone"


# ═══ UI 设计 Skill：负责实现 + 审美自检 ═══
def test_implement_hover_lift_uses_transform_and_yield():
    out = U.implement("hover_lift_yield", {"lift_px": 4, "duration_ms": 160})
    css = out["css"]
    assert "translateY(-4px)" in css                    # 抬起用 transform（不重排）
    assert ":hover" in css and "gap:" in css            # 邻位让位
    assert "box-shadow" in css
    assert out["tokens_used"] and not out.get("error")


def test_implement_unknown_intent_refuses():
    out = U.implement("让页面飞起来")
    assert "error" in out and "术语" in out["hint"]


def test_empty_state_must_say_why():
    out = U.implement("empty_state_honest")
    assert "原因" in out["html"] and "不是 0" in out["html"]


def test_audit_flags_hardcoded_colors_and_offscale_spacing():
    bad = ".x{color:#ff00ff;padding:13px;margin:7px}"
    rep = U.audit(css=bad)
    kinds = {i["kind"] for i in rep["issues"]}
    assert "hardcoded_color" in kinds and "off_scale_spacing" in kinds
    good = U.audit(css=".x{color:var(--text);padding:16px;gap:8px}")
    assert not [i for i in good["issues"] if i["kind"] in ("hardcoded_color",
                                                          "off_scale_spacing")]


def test_audit_reports_real_contrast():
    rep = U.audit()
    assert rep["contrast"]["text_on_bg"] >= U.CONTRAST_MIN["body"]   # 正文达标
    assert rep["ok"] is True                                        # 令牌本身没硬伤


def test_nav_is_uniform_across_pages():
    nav = U.nav_html("/command")
    for label in ("总控台", "AI 指挥中心", "总能力/连接", "流水线部署", "三套工具包",
                  "Octop 能力桥", "云插件中枢", "数据库编队", "蓝牙操控", "媒体监控",
                  "数字人", "API 文档", "智能体对话"):
        assert label in nav
    assert "navbtn on" in nav                                      # 当前页高亮（按键态）
    # 全内置纪律：导航里不许有外跳（不开新窗口、不指向别的服务）
    assert "target=_blank" not in nav and "target=\"_blank\"" not in nav
    assert "http://" not in nav and "https://" not in nav


def test_page_renders_with_tokens_and_optional_dock():
    html = U.Page(title="测试页", body="<div class=card>hi</div>",
                  current="/command").render()
    assert "--accent" in html and "var(--bg)" in html                # 令牌生效
    assert "aiask" in html                                          # 停靠坞在
    html2 = U.Page(title="无坞", body="x", dock=False).render()
    assert "aiask" not in html2


# ═══ AI 指挥中心：口语进来 → 术语 → 只读读数（或教怎么说）═══
class FakeView:
    revision, stale, safe_sentence = 3, False, "吞噬能已存 35 帧"


def test_ai_ask_translates_and_reads(monkeypatch):
    import panel.ai_center as AC

    async def fake_read_all(db, **kw):
        # read_all 返回的是 ToolResult.as_dict()，即 dict（不是对象）
        return {"devour": {"revision": FakeView.revision, "stale": FakeView.stale,
                           "safe_sentence": FakeView.safe_sentence}}
    monkeypatch.setattr("body.tools.view.read_all", fake_read_all)
    out = run(AC.ai_ask(AC.Ask(text="现在什么情况"), db=None))
    assert out["understood"] is True and out["term"] == "问一下当前读数"
    assert "吞噬能已存 35 帧" in out["say"]


def test_ai_ask_teaches_when_it_does_not_understand():
    import panel.ai_center as AC
    out = run(AC.ai_ask(AC.Ask(text="随便来点炫酷的"), db=None))
    assert out["understood"] is False and out["suggestions"]
    assert "怎么说" in out["say"] or "没听懂" in out["say"]


def test_command_page_has_all_sections():
    import panel.ai_center as AC
    html = AC._command_page()
    assert "AI 指挥中心" in html
    for token in ("对话操控台", "触手矩阵", "能力全景", "/api/ai/ask", "zh-TW"):
        assert token in html
    assert "--accent" in html                                        # 走设计令牌
