# tests/test_repair.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.alerts import AlertManager
from senses.gap_alerts import GapAlertBridge, GapEvent
from senses.repair import RepairPlanner, RepairWorker, GapContext, RepairResult


class FakeVoice:
    def say_alert(self, a): return {"queued": True}


@pytest.fixture
def setup(ledger):
    am = AlertManager(ledger)
    gb = GapAlertBridge(ledger, am, voice=FakeVoice(), poll_interval=99)
    planner = RepairPlanner(has_buffered=lambda g: g.gap_id == "g-buf",
                            can_resample=lambda g: g.gap_id == "g-res",
                            is_live_passed=lambda g: g.gap_id == "g-live")
    calls = {"recover": []}
    gb.on_verified_clean_segment = lambda t, **k: calls["recover"].append(t)
    rw = RepairWorker(ledger, planner=planner, gap_bridge=gb,
                      writeback=lambda g: RepairResult(status="restored",
                          restored=g.missing, artifact_sha="deadbeef"),
                      resample=lambda g: RepairResult(status="resampled",
                          resampled=g.missing))
    return rw, gb, calls


def test_classify_three_causes(setup):
    rw, _, _ = setup
    assert rw.planner.classify(GapContext("g-buf","t1")) == ("writeback_failed","writeback")
    assert rw.planner.classify(GapContext("g-res","t1")) == ("not_captured","resample")
    assert rw.planner.classify(GapContext("g-live","t1")) == ("live_passed","none")


def test_writeback_restores_and_recovers(setup):
    rw, gb, calls = setup
    rw.enqueue(GapContext("g-buf","t1", trace_id="tr1", missing=5))
    rw.run_once()
    # 校验通过 → 触发恢复
    assert calls["recover"] == ["t1"]
    st = rw.stats()
    assert st["restored_frames"] == 5


def test_resample_does_not_recover(setup):
    rw, gb, calls = setup
    rw.enqueue(GapContext("g-res","t1", trace_id="tr1", missing=7))
    rw.run_once()
    assert calls["recover"] == []            # 重采绝不恢复
    assert rw.stats()["resampled_frames"] == 7


def test_live_gap_permanent_no_retry(setup, ledger):
    rw, _, _ = setup
    rw.enqueue(GapContext("g-live","t1", trace_id="tr1", missing=3))
    assert rw.run_once() == 0                # 不入队执行
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM repair_jobs WHERE gap_id='g-live'")
        assert cur.fetchone()[0] == "permanent"
    assert rw.stats()["permanent_frames"] == 3


def test_idempotent_enqueue(setup):
    rw, _, _ = setup
    for _ in range(3):
        rw.enqueue(GapContext("g-buf","t1", missing=5))
    with rw.led._tx() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM repair_jobs")
        assert cur.fetchone()[0] == 1


def test_retry_backoff_then_dead_letter(ledger):
    gb = GapAlertBridge(ledger, AlertManager(ledger), poll_interval=99)
    rw = RepairWorker(ledger, gap_bridge=gb, max_attempts=2, base_backoff=1,
                      writeback=lambda g: RepairResult(status="failed", error="io"))
    rw.enqueue(GapContext("g-buf","t1", missing=1, cause_hint="writeback_failed"))
    rw.run_once()                            # 第 1 次失败 → 退回 queued
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM repair_jobs WHERE gap_id='g-buf'")
        assert cur.fetchone()[0] == "queued"
    rw.concurrency, rw.led.dialect_force = 2, None
    # 强推重试
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("UPDATE repair_jobs SET next_retry_at=0 WHERE gap_id='g-buf'")
    rw.run_once()                            # 第 2 次失败 → dead_letter
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM repair_jobs WHERE gap_id='g-buf'")
        assert cur.fetchone()[0] == "dead_letter"


def test_attempts_written_for_drilldown(setup, ledger):
    rw, _, _ = setup
    rw.enqueue(GapContext("g-buf","t1", trace_id="tr1", missing=5))
    rw.run_once()
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT trace_id, attempt_no, status FROM repair_attempts")
        r = cur.fetchone()
        assert r == ("tr1", 1, "restored")   # 可在流水线里按 trace 下钻
