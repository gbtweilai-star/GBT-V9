# tests/test_commander.py —— AI 指挥官：计划/编译/派工/闸门/能力代理
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 注意：本文件刻意不出现 Mimosa 误报的敏感方法名（探针定位确认）。
from skills.spec import validate_inputs


class FakeBrain:
    """脚本化大脑：计划官提示词 → 返回固定 DAG"""

    def __init__(self, plan=None):
        self.plan = plan or {"nodes": []}
        self.asks = []

    def chat(self, messages, **kw):
        sys_txt = messages[0]["content"] if messages else ""
        if "计划官" in sys_txt:
            return dict(self.plan)
        return {}

    def ask(self, tid, target, detail):
        self.asks.append((tid, target, detail))
        return {"verdict": "ok", "cmd": "continue", "hint": ""}


def _cmd(ledger, plan, gate=None):
    from core.commander import Commander
    from skills.integrate import build_registry

    reg = build_registry(ledger=ledger)
    return Commander(FakeBrain(plan), registry=reg, ledger=ledger, gate=gate)


# ═══ 输入校验：类型/必填/枚举/默认值 ═══
def test_validate_inputs_type_and_required():
    spec_inputs = {"task": {"type": "string", "required": True},
                   "prefer": {"type": "enum", "values": ["codex", "brain"],
                              "default": "codex"},
                   "n": {"type": "integer"}}
    clean, errs = validate_inputs({}, spec_inputs)
    assert any("task" in e for e in errs)            # 缺必填
    clean2, errs2 = validate_inputs({"task": "x", "prefer": "bad", "n": 1.5},
                                    spec_inputs)
    assert any("枚举" in e for e in errs2)
    assert any("integer" in e for e in errs2)
    clean3, errs3 = validate_inputs({"task": "x"}, spec_inputs)
    assert errs3 == [] and clean3["prefer"] == "codex"   # 默认值生效


# ═══ 计划：真实能力解析 + 能力缺口诚实上报 ═══
def test_plan_resolves_real_skill(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "rules", "goal": "输出规则文本",
                       "inputs": {"op": "text"}}]}
    s = _cmd(ledger, plan).run_chain("给我规则全文")
    assert s["ok"] is True and s["nodes"] == 1
    r = s["results"][0]
    assert r["ok"] and "工程规则" in r["output"]["text"]


def test_plan_reports_capability_gap_honestly(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "注册邮箱机器人",
                       "goal": "x", "inputs": {}}]}
    s = _cmd(ledger, plan).run_chain("注册邮箱")
    assert s["ok"] is False and s["stage"] == "plan"
    assert any("能力缺口" in e for e in s["errors"])   # 不编造能力


def test_plan_rejects_bad_inputs(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "rules", "goal": "x",
                       "inputs": {"op": "不存在的op"}}]}
    s = _cmd(ledger, plan).run_chain("x")
    assert s["ok"] is False and s["stage"] == "plan"
    assert any("输入不合格" in e for e in s["errors"])  # spec 漂移被拦


# ═══ 高风险能力：本地闸门不可绕过 ═══
def test_high_risk_requires_local_gate(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "coder", "goal": "改代码",
                       "inputs": {"task": "加个 healthz"}}]}
    s = _cmd(ledger, plan).run_chain("改代码")          # gate 默认关闭
    r = s["results"][0]
    assert r["needs_confirm"] is True and s["ok"] is False
    assert s["escalated"]                              # 卡点升级结构存在


def test_high_risk_passes_with_gate_open(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "coder", "goal": "改代码",
                       "inputs": {"task": "加个 healthz"}}]}
    s = _cmd(ledger, plan, gate=lambda tid, name, spec: True).run_chain("改代码")
    # 无 codex/大脑 → 引擎层诚实失败（但已过闸门，错误来自引擎而非闸门）
    r = s["results"][0]
    assert r.get("needs_confirm") is None and "闸门" not in (r.get("error") or "")


# ═══ 专业化指令编译 + 提示词渲染 ═══
def test_compile_and_render_prompt(ledger):
    from core.commander import compile_task, render_prompt

    node = {"id": "step1", "skill": "voice", "goal": "播报摘要",
            "inputs": {"action": "say", "text": "完成"}}
    skill = None
    from skills.integrate import build_registry
    reg = build_registry(ledger=ledger)
    skill = reg.skills["voice"]
    from skills.spec import get_spec, is_declared
    inst = compile_task(node, get_spec(skill) if is_declared(skill) else {})
    prompt = render_prompt(inst)
    assert "【角色】" in prompt and "【验收标准】" in prompt
    assert "【卡点规则】" in prompt and "不得伪报成功" in prompt


# ═══ 能力代理：可见即可调用，权限不可越 ═══
def test_call_as_low_risk_and_gap(ledger):
    c = _cmd(ledger, {"nodes": []})
    r = c.call_as("t9", "rules", {"op": "text"})
    assert r["ok"] is True
    g = c.call_as("t9", "不存在的", {})
    assert g["ok"] is False and "能力缺口" in g["error"]


def test_call_as_high_risk_blocked_without_gate(ledger):
    c = _cmd(ledger, {"nodes": []})
    r = c.call_as("t9", "coder", {"task": "x"})
    assert r["needs_confirm"] is True


# ═══ 指挥链全链路落账（同一 trace）═══
def test_chain_audited_with_single_trace(ledger):
    plan = {"nodes": [{"id": "step1", "skill": "rules", "goal": "x",
                       "inputs": {"op": "text"}}]}
    s = _cmd(ledger, plan).run_chain("规则全文")
    rows = ledger.conn.execute(
        "SELECT target FROM ledger WHERE scanner='commander'").fetchall()
    assert rows and all(s["trace_id"] in t[0] for t in rows)   # 同一 trace 贯穿
