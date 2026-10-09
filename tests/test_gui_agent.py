# tests/test_gui_agent.py —— GUI 操控层（蒸馏自 Mano-P）：感知 / 闭环 / 路由 / 隐私
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 感知：元素去重编号、文字清单、Set-of-Marks 标注（真 PIL）
#   ② 闭环：think→act→verify；expect 命中即完成；无进展即停；done 但期望未命中要如实报
#   ③ 动作解析与映射：白名单校正、元素→坐标、URL 归一、expect→verify
#   ④ 规划器路由：统一密钥网关为主、本地兜底、都没有→明确拒绝
#   ⑤ 隐私：截图永不出设备；文字清单可关
import io

import pytest

from core import gui_perception as P
from core.gui_grant import Grant
from core.gui_agent import (ACTION_SCHEMA_HINT, GatewayPlanner, GuiAgent,
                            LocalOllamaPlanner, PlannerUnavailable,
                            ScriptedPlanner, parse_action, pick_planner,
                            to_actuator_action)


# ═══ ① 感知 ═══
def test_merge_elements_dedupes_and_numbers():
    uia = [P.Element(0, "确定", "Button", [100, 200, 60, 30], "uia")]
    ocr = [P.Element(0, "确定", "Text", [101, 201, 58, 28], "ocr"),   # 与 UIA 重叠 → 丢
           P.Element(0, "提示文字", "Text", [100, 300, 120, 20], "ocr")]
    merged = P.merge_elements(uia, ocr, max_n=10)
    assert [e.name for e in merged] == ["确定", "提示文字"]
    assert [e.idx for e in merged] == [1, 2]                 # 1 起编号
    assert merged[0].center == [130, 215]
    assert "1" in P.screen_text(merged) and "@(130,215)" in P.screen_text(merged)


def test_annotate_draws_marks_on_real_image():
    from PIL import Image
    img = Image.new("RGB", (200, 120), (0, 0, 0))
    els = [P.Element(1, "按钮", "Button", [10, 10, 40, 20], "uia")]
    png = P.annotate(img, els)
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n"            # 真是 PNG
    assert Image.open(io.BytesIO(png)).size == (200, 120)


def test_perceive_notes_failures_instead_of_faking():
    out = P.perceive(capture_fn=lambda: (_ for _ in ()).throw(RuntimeError("no screen")),
                     uia_fn=lambda: ([], "uia_import_failed:ImportError"),
                     ocr_fn=lambda img: ([], "tesseract_import_failed:ImportError"))
    assert out["elements"] == []
    assert any("capture_failed" in n for n in out["notes"])
    assert any("uia_import_failed" in n for n in out["notes"])


def test_perceive_uses_injected_screen_and_elements():
    from PIL import Image
    img = Image.new("RGB", (300, 200), (10, 10, 10))
    out = P.perceive(capture_fn=lambda: img,
                     uia_fn=lambda: ([P.Element(0, "开始", "Button", [0, 0, 40, 20], "uia")], "ok"),
                     ocr_fn=lambda i: ([], "ok"))
    assert out["size"] == [300, 200] and len(out["elements"]) == 1
    assert out["marks_png"]


# ═══ ② 闭环 ═══
def _perc(pages):
    seq = {"i": 0}

    def fn():
        i = min(seq["i"], len(pages) - 1)
        seq["i"] += 1
        return {"elements": pages[i], "text": "", "notes": [], "marks_png": None}
    return fn


_A = {"idx": 1, "name": "开始菜单", "role": "Button", "rect": [0, 1000, 40, 40], "center": [20, 1020]}
_B = {"idx": 2, "name": "设置", "role": "MenuItem", "rect": [0, 900, 80, 30], "center": [40, 915]}


def test_loop_stops_when_expect_is_satisfied():
    ag = GuiAgent(planner=ScriptedPlanner([
        {"thought": "点开始菜单", "action": "click", "element": 1, "expect": "设置"},
        {"thought": "已出现设置", "action": "done", "expect": "设置"}]),
        perceive_fn=_perc([[_A], [_A, _B], [_A, _B]]), dry_run=True)
    r = ag.run("打开设置", expect="设置")
    assert r["ok"] is True and r["reason"] == "expect_satisfied"
    assert r["dry_run"] is True and r["steps"][0]["ok"] is True
    assert "屏幕有变化" in r["steps"][0]["note"]


def test_loop_stops_on_no_progress():
    same = [_A]
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "click", "element": 1}] * 5),
                  perceive_fn=_perc([same, same, same, same, same]), dry_run=True)
    r = ag.run("点了没反应的任务")
    assert r["ok"] is False and r["reason"] == "no_progress"
    assert len(r["steps"]) == 3                    # 连续 3 步无变化即停，不硬撑


def test_done_with_unmet_expect_is_reported_honestly():
    ag = GuiAgent(planner=ScriptedPlanner([{"thought": "自认为完成", "action": "done"}]),
                  perceive_fn=_perc([[_A]]), dry_run=True)
    r = ag.run("目标没达成", expect="不该出现的东西")
    assert r["ok"] is False and r["reason"] == "expect_unmet"


def test_planner_ask_returns_need_info():
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "ask", "text": "你要在哪个窗口操作？"}]),
                  perceive_fn=_perc([[_A]]), dry_run=True)
    r = ag.run("模糊任务")
    assert r["reason"] == "need_info" and r["ok"] is False


def test_dry_run_does_not_touch_actuator():
    class Act:
        def __init__(self): self.calls = []

        def run_action(self, a): self.calls.append(a); return {"ok": True}

    act = Act()
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "click", "element": 1}] * 3),
                  actuator=act, perceive_fn=_perc([[_A], [_A, _B]]), dry_run=True)
    ag.run("只规划")
    assert act.calls == []                         # dry_run 绝不碰鼠标


def test_allow_actions_routes_to_actuator():
    class Act:
        def __init__(self): self.calls = []

        def run_action(self, a): self.calls.append(a); return {"ok": True, "needs_confirm": True}

    act = Act()
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "click", "element": 1}]),
                  actuator=act, perceive_fn=_perc([[_A]]), allow_actions=True)
    r = ag.run("执行")
    assert r["reason"] == "needs_confirm" and len(act.calls) == 1
    assert ag.dry_run is False


# ═══ ③ 解析与映射 ═══
def test_parse_action_whitelists_and_falls_back_to_ask():
    assert parse_action('{"action":"click","element":2}')["element"] == 2
    assert parse_action("模型胡说八道")["action"] == "ask"
    assert parse_action('{"action":"rm -rf /"}')["action"] == "ask"        # 白名单外
    assert parse_action('x {"action":"hotkey","keys":["ctrl","s"]} y')["action"] == "hotkey"


def test_to_actuator_action_maps_element_and_url():
    a = to_actuator_action({"action": "click", "element": 1, "expect": "设置"},
                           [{"idx": 1, "center": [20, 1020], "name": "开始菜单"}])
    assert a.primitive == "click" and a.target["coords"] == [20, 1020]
    assert a.verify["probe"] == "设置"
    u = to_actuator_action({"action": "url", "text": "example.com"}, [])
    assert u.primitive == "launch" and u.args["cmd"] == "https://example.com"
    t = to_actuator_action({"action": "type", "text": "你好"}, [])
    assert t.risk == "write" and t.args["text"] == "你好"


# ═══ ④ 路由：统一密钥网关是主线 ═══
def test_gateway_planner_is_chosen_when_unified_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "tk-unified-route")
    monkeypatch.delenv("GUI_PLANNER", raising=False)
    p, kind = pick_planner()
    assert isinstance(p, GatewayPlanner) and kind.startswith("gateway:")


def test_local_is_fallback_when_gateway_key_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GBT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("GUI_PLANNER", "local")
    monkeypatch.setenv("GUI_MODEL", "qwen3:latest")
    monkeypatch.setenv("GUI_ALLOW_REMOTE_MODEL", "0")
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1")
    p, kind = pick_planner()
    assert isinstance(p, LocalOllamaPlanner) and kind.startswith("local:")


def test_no_planner_at_all_refuses(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GBT_LLM_API_KEY", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:1")      # 连不上
    monkeypatch.setenv("GUI_PLANNER", "gateway")
    with pytest.raises(PlannerUnavailable):
        pick_planner()


def test_gateway_planner_parses_model_json(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "tk-unified-parse")
    p = GatewayPlanner()

    class _Msg:
        content = '{"thought":"点它","action":"click","element":3}'

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        @staticmethod
        def create(**kw):
            assert kw["response_format"] == {"type": "json_object"}   # 要求 JSON 输出
            return _Resp()

    class _Chat:
        completions = _Completions()

    class _FakeGateway:
        chat = _Chat()

    p.key.client_for = lambda tid: _FakeGateway()          # 注入假网关（同一把钥匙的客户端）
    out = p.plan("点第三个元素", {"text": "[3] Button: 保存", "marks_png": None}, [])
    assert out["action"] == "click" and out["element"] == 3


# ═══ ⑤ 隐私闸门 ═══
def test_screenshot_never_leaves_device(monkeypatch):
    monkeypatch.setenv("GUI_LOCAL_ONLY", "1")
    with pytest.raises(PermissionError, match="截图"):
        P.assert_may_send("屏幕截图", image=True)
    P.assert_may_send("屏幕元素清单")                      # 文字清单允许（统一密钥主线）


def test_text_out_can_be_shut_off(monkeypatch):
    monkeypatch.setenv("GUI_ALLOW_TEXT_OUT", "0")
    with pytest.raises(PermissionError, match="GUI_ALLOW_TEXT_OUT"):
        P.assert_may_send("屏幕元素清单")


def test_schema_hint_mentions_all_primitives():
    for p in ("click", "type", "hotkey", "scroll", "drag", "move", "wait",
              "launch", "url", "done", "ask"):
        assert p in ACTION_SCHEMA_HINT


# ═══ ⑥ 纯视觉 + 授权（主人 2026-10-06 口径）═══
class _Act:
    """假执行层：auto_confirm=False 时如实报 needs_confirm（真执行层同语义）。"""

    def __init__(self):
        self.auto_confirm = False
        self.calls = []

    def run_action(self, a):
        if not self.auto_confirm:
            return {"ok": False, "needs_confirm": True}
        self.calls.append(a)
        return {"ok": True}


def _one_page(el=_A):
    def fn():
        return {"elements": [el], "text": "", "notes": [], "marks_png": None,
                "geometry": {"dpr": 1.0, "left": 0, "top": 0}}
    return fn


def test_vision_only_never_reads_accessibility_tree(monkeypatch):
    called = {"uia": 0}

    def uia():
        called["uia"] += 1
        return ([], "ok")

    from PIL import Image
    img = Image.new("RGB", (100, 80), (0, 0, 0))
    out = P.perceive(capture_fn=lambda: img, uia_fn=uia,
                     ocr_fn=lambda i: ([], "ok"), vision_only=True)
    assert called["uia"] == 0                       # ★纯视觉：完全不碰无障碍接口
    assert out["vision_only"] is True
    assert any("未读无障碍树" in n for n in out["notes"])


def test_vision_only_default_from_env(monkeypatch):
    monkeypatch.setenv("GUI_VISION_ONLY", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "tk-vision-only")
    ag = GuiAgent(perceive_fn=_one_page(), dry_run=True, max_steps=1)
    assert ag.vision_only is True


def test_coords_are_converted_from_image_pixels_to_screen():
    a = to_actuator_action({"action": "click", "coords": [200, 100]}, [],
                           geom={"dpr": 1.5, "left": 0, "top": 0})
    assert a.target["coords"] == [133, 67]          # 高 DPI 下不换算就会点偏


def test_drag_maps_endpoint_and_refuses_without_one():
    ok = to_actuator_action({"action": "drag", "element": 1, "to_element": 2},
                            [{"idx": 1, "center": [10, 10]}, {"idx": 2, "center": [90, 90]}])
    assert ok.primitive == "drag" and ok.args["to"] == [90, 90] and ok.risk == "write"
    assert to_actuator_action({"action": "drag", "element": 1},
                              [{"idx": 1, "center": [1, 1]}]) is None   # 没终点不瞎拖


def test_risky_action_stops_without_grant():
    act = _Act()
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "type", "text": "删除全部", "element": 1}]),
                  actuator=act, perceive_fn=_one_page(), allow_actions=True)
    r = ag.run("清空回收站")
    assert r["reason"] == "needs_confirm" and act.calls == []      # 没授权 → 一步都不做


def test_master_grant_unlocks_any_action(monkeypatch):
    monkeypatch.setenv("GUI_GRANT_SECRET", "grant-secret-test")
    from core.gui_grant import issue
    act = _Act()
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "type", "text": "卸载全部", "element": 1},
                                           {"action": "done"}]),
                  actuator=act, perceive_fn=_one_page(), allow_actions=True)
    r = ag.run("卸载程序", grant=issue(scope=["*"], ttl=60, note="主人授权"))
    assert r["ok"] is True and len(act.calls) == 1                 # ★授权即无禁区


def test_grant_scope_limits_primitives(monkeypatch):
    monkeypatch.setenv("GUI_GRANT_SECRET", "grant-secret-test")
    from core.gui_grant import issue
    act = _Act()
    ag = GuiAgent(planner=ScriptedPlanner([{"action": "type", "text": "x", "element": 1}]),
                  actuator=act, perceive_fn=_one_page(), allow_actions=True)
    r = ag.run("输入", grant=issue(primitives=["click"], ttl=60))
    assert r["reason"] == "needs_confirm" and act.calls == []      # 越权 → 拒


def test_tampered_or_expired_grant_is_refused(monkeypatch):
    monkeypatch.setenv("GUI_GRANT_SECRET", "grant-secret-test")
    from core.gui_grant import issue
    good = issue(scope=["*"], ttl=60)
    bad = good.replace('"*"', '"type"')                            # 改内容不改签名 → 篡改
    ok, why, _ = Grant.verify_action(bad, primitive="type")
    assert ok is False and "bad_signature" in why
    ok2, why2, _ = Grant.verify_action(issue(scope=["*"], ttl=-1), primitive="type")
    assert ok2 is False and why2.startswith("grant_expired")


def test_grant_step_limit(monkeypatch):
    monkeypatch.setenv("GUI_GRANT_SECRET", "grant-secret-test")
    from core.gui_grant import issue
    tok = issue(scope=["*"], ttl=60, max_steps=1)
    ok, why, _ = Grant.verify_action(tok, primitive="click", step=2)
    assert ok is False and "grant_step_limit" in why
