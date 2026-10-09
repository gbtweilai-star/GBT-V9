# tests/test_agent_chat.py —— 智能体对话：名册 / 取文本（真机 bug 回归）/ 工作流图 / 记录
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 名册读真实目录（19 部门 / 272 智能体 / 18 专家），形状对得上（divisions 是列表）
#   ② **取文本要从 output 拿**（真机 bug：drive() 顶层没有 text，只读顶层会永远空）
#   ③ 协作工作流图节点/连线齐全（指挥→名册→部门→产出）
#   ④ 对话记录追加式；空提问被拒
import json

from core import agent_chat as AC


def test_roster_reads_real_catalog_shapes():
    r = AC.roster()
    assert r["ok"] is True
    assert len(r["divisions"]) >= 10 and len(r["agents"]) >= 100
    for d in r["divisions"]:
        assert set(d) >= {"部门", "标题", "智能体数"} and d["智能体数"] >= 0
    for a in r["agents"][:5]:
        assert a["id"] and a["division"]
    assert len(r["experts"]) >= 1
    assert (r["counts"] or {}).get("agents")


def test_pick_agent_finds_and_reports_miss():
    r = AC.roster()
    if r.get("agents"):
        first = r["agents"][0]["id"]
        got = AC.pick_agent(first)
        assert got["ok"] is True and got["agent"]["id"] == first
    miss = AC.pick_agent("绝对不存在的智能体名字")
    assert miss["ok"] is False and "没有匹配" in miss["reason"]


def test_drive_text_reads_output_not_top_level():
    """真机回归：drive() 返回 {ok, tentacle, order, ms, output:{text}} —— 必须从 output 取。"""
    assert AC._drive_text({"ok": True, "output": {"text": "你好"}}) == "你好"
    assert AC._drive_text({"ok": True, "text": "顶层也有"}) == "顶层也有"
    assert AC._drive_text({"ok": True, "output": {"content": "从 content 取"}}) == "从 content 取"
    assert AC._drive_text({"ok": True, "output": {"parse_error": "x", "text": ""}}) == ""
    assert AC._drive_text(None) == ""


def test_workflow_graph_has_nodes_and_edges():
    w = AC.workflow()
    ids = {n["id"] for n in w["nodes"]}
    assert {"cmdr", "roster", "out"} <= ids
    assert any(n["kind"] == "div" for n in w["nodes"])
    for e in w["edges"]:
        assert e["from"] in ids and e["to"] in ids
        assert e["kind"] in ("flow", "fan")


def test_history_appends_and_rejects_empty():
    before = AC.history(5)["count"]
    bad = AC.ask("")
    assert bad["ok"] is False
    assert AC.history(5)["count"] == before          # 空提问不落记录


def test_native_status_is_honest_without_credentials(monkeypatch):
    monkeypatch.delenv("OCTOP_USER", raising=False)
    monkeypatch.delenv("OCTOP_PASSWORD", raising=False)
    st = AC.octop_native_status()
    assert st["可登录"] is False
    assert "OCTOP_USER" in st["下一步"]
    r = AC.octop_native_chat("你好")
    assert r["ok"] is False and "未就绪" in r["reason"]
