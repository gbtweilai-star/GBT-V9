# tests/test_specs.py —— 能力规格声明全覆盖 + 扫描会话心跳的严谨速率
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# SQL 全部为字面量 + 参数绑定；本文件属 SQLite 测试层（conftest 强制）。
import time
from pathlib import Path

import pytest

from skills.spec import registry_spec_report, validate_spec


# ═══ 业务能力：注册表里每个能力都必须有合格 spec() ═══
def test_registry_specs_all_declared_and_valid():
    from skills.integrate import build_registry

    reg = build_registry()
    assert set(reg.skills) >= {"rules", "codex", "coder", "voice",
                               "diagram", "image"}, "核心业务能力缺注册"
    rep = registry_spec_report(reg)
    missing = [n for n, r in rep.items() if not r["declared"]]
    invalid = {n: r["errors"] for n, r in rep.items() if r["errors"]}
    assert not missing, f"未声明 spec(): {missing}"
    assert not invalid, f"spec() 不合格: {invalid}"


# ═══ 骨架能力：INFRA 四件同样必须有合格 spec() ═══
def test_infra_specs_declared_and_valid():
    from core.inference.backends import InferenceRouter
    from core.orchestration.team import Orchestrator
    from workflows.engine import WorkflowEngine

    for inst in (InferenceRouter([]), Orchestrator(None),
                 WorkflowEngine(None)):
        errs = validate_spec(inst.spec())
        assert errs == [], f"{type(inst).__name__}: {errs}"
        assert inst.spec()["inputs"], f"{type(inst).__name__} 没有 inputs"

    import tempfile
    from build.model_optimize.pipeline import ModelBuildPipeline

    mb = ModelBuildPipeline(
        workdir=str(Path(tempfile.mkdtemp()) / "mb"), require_approval=True)
    errs = validate_spec(mb.spec())
    assert errs == [], errs
    # 部署资格类能力必须标 high risk（人工闸门）
    assert mb.spec()["risk"] == "high"


# ═══ 高风险写操作必须显式标注（工作流闸门依据）═══
def test_write_capabilities_marked_high_risk():
    from skills.engine import CoderRouter
    from skills.native_codex import CodexTool

    assert CoderRouter(None, workspace=".").spec()["risk"] == "high"
    assert CodexTool().spec()["risk"] == "high"


# ═══ spec 与 run() 行为对齐抽查 ═══
def test_coder_run_missing_task_is_structured_failure():
    from skills.engine import CoderRouter

    r = CoderRouter(None, workspace=".").run(None, {})
    assert r.ok is False and "task" in (r.error or "")


def test_voice_run_say_enqueues(ledger):
    from senses.voice import VoiceAdapter

    va = VoiceAdapter(ledger=ledger)
    r = va.run(None, {"action": "say", "text": "自检", "event_id": "spec-1"})
    assert r.ok is True and r.output["queued"] is True
    dup = va.run(None, {"action": "say", "text": "自检", "event_id": "spec-1"})
    assert dup.output["queued"] is False            # event_id 去重生效


def test_rules_run_text_matches_spec():
    from skills.rules import EngineeringRules

    r = EngineeringRules().run(None, {"op": "text"})
    assert r.ok is True and "工程规则" in r.output["text"]


# ═══ 扫描会话心跳：事件/小时的严谨计算 ═══
def _seed_scan_window(led, tid="t1", active_sec=600, events=12,
                      started_ago=3600):
    """造一个已结束的扫描会话：active_sec 秒活跃、每 5s 一个心跳"""
    from senses.scan_sessions import ensure_tables

    ensure_tables(led)
    now = time.time()
    start = now - started_ago
    end = start + active_sec
    from senses.sqldialect import txn
    with txn(led) as cur:
        cur.execute("""INSERT INTO scan_sessions
            (session_id,run_id,trace_id,tentacle_id,started_at,last_heartbeat,
             ended_at,expected_interval,state,end_reason)
            VALUES(?,?,NULL,?,?,?,?,5,'closed','closed')""",
            ("sess-spec", "run-spec", tid, start, end, end))
        ts = start
        while ts <= end:
            cur.execute("""INSERT OR IGNORE INTO scan_heartbeats
                (session_id,ts,work_units) VALUES(?,?,1)""", ("sess-spec", ts))
            ts += 5
        # 关联的扫描事件落账本（回拨时间戳到窗口内）
        for i in range(events):
            led.log(tid, f"f{i}.py", "scanned", "")
            cur.execute("""UPDATE ledger SET ts=? WHERE rowid IN
                (SELECT rowid FROM ledger WHERE target=?
                 ORDER BY ts DESC LIMIT 1)""", (start + 60, f"f{i}.py"))
    return start, end


def test_scan_rate_computed_with_session(ledger):
    from panel.throughput import tentacle_throughput

    _seed_scan_window(ledger, active_sec=600, events=12)
    out = tentacle_throughput(ledger, hours=2)
    t1 = next(t for t in out["tentacles"] if t["id"] == "t1")
    assert t1["scan_events"] == 12
    assert t1["scan_events_per_hour"] is not None          # 有会话 → 真速率
    assert t1["scan_rate_note"] in ("", None)


def test_scan_rate_null_without_session(ledger):
    """没有扫描会话 → 速率必须 None 且带原因（不拿窗口总量冒充）"""
    from panel.throughput import tentacle_throughput

    for i in range(10):
        ledger.log("t9", f"g{i}.py", "scanned", "")
    out = tentacle_throughput(ledger, hours=2)
    t9 = next(t for t in out["tentacles"] if t["id"] == "t9")
    assert t9["scan_events"] == 10
    assert t9["scan_events_per_hour"] is None
    assert t9["scan_rate_note"]                            # 有诚实原因
