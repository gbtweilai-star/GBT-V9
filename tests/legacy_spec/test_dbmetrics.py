# tests/test_dbmetrics.py —— 监控与告警状态机测试
# 纪律: 时间全部显式注入 now, 绝不 sleep; 纯内存, 离线秒跑
from __future__ import annotations
import asyncio, sqlite3

import pytest

from panel import db as panel_db
from panel import dbmetrics as dm


# ═══════════ ① 滞后（enter/exit 窗口）═══════════
def test_alert_hysteresis_and_intermittent_windows():
    m = dm.AlertStateMachine(dm.AlertConfig(
        enter_windows=3, exit_windows=2, cooldown_s=0))
    bad  = {"pool_used_ratio": 0.85, "lock_waits": 0, "lock_state": "ok"}
    good = {"pool_used_ratio": 0.5,  "lock_waits": 0, "lock_state": "ok"}

    assert m.evaluate(bad,  now=100)["level"] == "ok"   # 1/3
    assert m.evaluate(good, now=101)["level"] == "ok"   # ★间歇越线 → 计数重置
    assert m.evaluate(bad,  now=102)["level"] == "ok"   # 又从 1 开始
    assert m.evaluate(bad,  now=103)["level"] == "ok"
    assert m.evaluate(bad,  now=104)["level"] == "warn" # 连续 3 窗才升级

    assert m.evaluate(good, now=105)["level"] == "warn" # 1 窗回落不够
    assert m.evaluate(good, now=106)["level"] == "ok"   # 连续 2 窗才解除


# ═══════════ ② 锁等待语义 ═══════════
def test_lock_wait_is_immediately_critical():
    m = dm.AlertStateMachine(dm.AlertConfig(enter_windows=5))
    r = m.evaluate({"lock_waits": 1, "lock_state": "ok",
                    "pool_used_ratio": 0.1}, now=100)
    assert r["level"] == "crit" and r["reasons"]        # ★不等窗口


def test_unknown_lock_state_is_not_reported_as_zero_or_ok():
    m = dm.AlertStateMachine(dm.AlertConfig(enter_windows=2))
    sample = {"lock_waits": None, "lock_state": "unknown"}
    m.evaluate(sample, now=100)
    r = m.evaluate(sample, now=101)
    assert r["level"] == "warn"
    assert any("未知" in x for x in r["reasons"])
    assert r["level"] != "crit"                         # 未知 ≠ 确认有锁


# ═══════════ ③ 冷却与升级（注入时间）═══════════
def test_cooldown_and_severity_escalation_use_injected_time():
    m = dm.AlertStateMachine(dm.AlertConfig(
        enter_windows=1, exit_windows=1, cooldown_s=60))
    warning  = {"p95_ms": 600, "lock_waits": 0, "lock_state": "ok"}
    critical = {"p95_ms": 10,  "lock_waits": 1, "lock_state": "ok"}
    healthy  = {"p95_ms": 10,  "lock_waits": 0, "lock_state": "ok"}

    assert m.evaluate(warning,  now=100)["emit"] is True
    escalated = m.evaluate(critical, now=101)
    assert escalated["level"] == "crit" and escalated["emit"] is True  # 升级不吃 warn 的冷却

    assert m.evaluate(healthy, now=102)["level"] == "ok"
    reentered = m.evaluate(warning, now=110)
    assert reentered["level"] == "warn"
    assert reentered["emit"] is False                    # ★warn 仍在自己的冷却期内


# ═══════════ ④ 直方图分位（桶上界语义）═══════════
def test_empty_histogram_returns_none():
    assert dm.QueryMetrics().percentile_ms(0.95) is None


def test_histogram_exact_bucket_boundary():
    m = dm.QueryMetrics()
    for _ in range(100):
        m.record("SELECT * FROM t WHERE id = 1", 5.0, sample=False)
    assert m.percentile_ms(0.95) == 5.0                  # 恰落桶上界


def test_histogram_percentile_crosses_bucket():
    m = dm.QueryMetrics()
    for _ in range(94):
        m.record("SELECT * FROM t WHERE id = 1", 1.0, sample=False)
    m.record("SELECT * FROM t WHERE id = 2", 2.0, sample=False)
    assert m.percentile_ms(0.95) == 2.0                  # 第 95 个样本落入下一桶


# ═══════════ ⑤ 指纹对抗：绝不泄参 ═══════════
@pytest.mark.parametrize("sql,secrets", [
    ("SELECT * FROM t WHERE x='PRIVATE''TOKEN' AND n=42",
     ["PRIVATE", "TOKEN", "42"]),
    ("select * from t where x='PRIVATE_TOKEN--suffix' and n=9",
     ["PRIVATE_TOKEN", "suffix", "9"]),                  # ★字面量里的 -- 不是注释
    ("SELECT * FROM t WHERE \"PRIVATE_COLUMN\" = 'secret' /* PRIVATE_COMMENT */",
     ["PRIVATE_COLUMN", "secret", "PRIVATE_COMMENT"]),
    ("SELECT * FROM t -- PRIVATE_LINE_COMMENT\n WHERE id=123",
     ["PRIVATE_LINE_COMMENT", "123"]),
])
def test_fingerprint_never_exposes_literals_or_comments(sql, secrets):
    fp, shape = dm.fingerprint(sql)
    assert fp
    for secret in secrets:
        assert secret.lower() not in shape.lower()


# ═══════════ ⑥ Top-K 有界且保热指纹 ═══════════
def test_top_k_is_bounded_and_keeps_hot_fingerprint():
    m = dm.QueryMetrics()
    m.MAX_TOPK = 3
    hot_fp, _ = dm.fingerprint("SELECT * FROM t WHERE id=1")
    for _ in range(10):
        m.record("SELECT * FROM t WHERE id=1", 10, sample=False)
    for i in range(4):
        m.record(f"SELECT * FROM t WHERE id={i + 100}", 10, sample=False)

    snap = m.snapshot()
    assert len(snap["top_slow_sql"]) <= 3
    assert hot_fp in {row["fp"] for row in snap["top_slow_sql"]}   # ★热的不被挤掉


def test_snapshot_marks_metrics_per_worker():
    snap = dm.QueryMetrics().snapshot()
    assert snap["scope"] == "per-worker"
    assert isinstance(snap["worker_id"], str) and snap["worker_id"]


# ═══════════ ⑦ 埋点不伤业务 ═══════════
class FakeCursor:
    async def fetchall(self): return [{"value": 7}]
    async def close(self): pass

class FakeSQLiteConn:
    def __init__(self, error=None): self.error = error
    async def execute(self, sql, params):
        if self.error: raise self.error
        return FakeCursor()


def test_busy_locked_counter_increments(monkeypatch):
    metrics = dm.QueryMetrics()
    monkeypatch.setattr(panel_db, "METRICS", metrics)
    db = panel_db.PanelDB(
        FakeSQLiteConn(sqlite3.OperationalError("database is locked")), "sqlite")
    with pytest.raises(sqlite3.OperationalError):
        asyncio.run(db.fetch_all("SELECT 1"))
    assert metrics.busy_locked == 1


def test_metrics_instrumentation_failure_does_not_break_query(monkeypatch):
    metrics = dm.QueryMetrics()
    monkeypatch.setattr(panel_db, "METRICS", metrics)

    def broken_record(*a, **k): raise RuntimeError("metrics unavailable")
    monkeypatch.setattr(metrics, "record", broken_record)

    db = panel_db.PanelDB(FakeSQLiteConn(), "sqlite")
    assert asyncio.run(db.fetch_all("SELECT 1")) == [{"value": 7}]   # 业务照常
