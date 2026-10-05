# tests/test_media_monitor.py —— 生成队列指标/告警：0≠无数据、闸门口径、死信 critical
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time

import pytest

from audit.ledger import Ledger
from media.queue import JobQueue, ensure_tables
from media.metrics import queue_stats
from panel.alerts import AlertManager


@pytest.fixture
def media(ledger):
    ensure_tables(ledger)
    return ledger, JobQueue(ledger)


def _events(led, event, n, ago=0):
    import uuid
    with led._tx() as cur:
        for _ in range(n):
            cur.execute("""INSERT INTO media_job_events
                (event_id,job_id,attempt_no,event,ts,detail)
                VALUES(?,?,?,?,?,?)""",
                (uuid.uuid4().hex[:16], "j-x", 1, event, time.time() - ago, None))


# ═══ 指标口径：0 与 无数据 分开 ═══
def test_empty_queue_is_zero_observed(media):
    led, _ = media
    s = queue_stats(led)
    assert s["coverage"] == "observed"
    assert s["depth"] == {"queued": 0, "running": 0, "dead": 0}
    assert s["failure_rate"] is None                      # 无尝试 → null，不是 0%
    assert s["failure_rate_kind"] == "no_data"


def test_failure_rate_uses_attempt_window(media):
    led, _ = media
    _events(led, "failed", 1)
    _events(led, "completed", 3)
    s = queue_stats(led)
    assert s["failure_rate"] == round(1 / 4, 4)           # failed/(failed+completed)
    assert s["failure_rate_kind"] == "observed"


def test_backoff_jobs_not_counted_as_waiting(media):
    led, q = media
    q.enqueue("p", "stage", "s", {"x": 1})
    w = q.claim("w")
    q.fail(w["job_id"], w["lease_owner"], "boom")         # 进入退避（next_run_at 未来）
    s = queue_stats(led)
    assert s["depth"]["queued"] == 1
    assert s["wait"]["backoff_count"] == 1                # 退避单列
    assert s["wait"]["oldest_runnable_age"] == 0          # 没有可运行等待 → 0 是真 0


# ═══ 死信与告警 ═══
def test_dead_letter_creates_critical_incident(media):
    led, q = media
    am = AlertManager(led, lambda: {"backend_ok": True}, recover_confirm=1)
    from media.monitor import MediaMonitor
    mon = MediaMonitor(led, am, voice=None, interval=999,
                       queue=q, thresholds={"fail_min_attempts": 999})
    j = q.enqueue("p", "stage", "s", {"x": 1}, max_attempts=1)
    w = q.claim("w")
    q.fail(j["job_id"], w["lease_owner"], "boom")         # 1 次即死信
    mon._last_dead = 0
    mon.sample_once()
    fired = led.conn.execute(
        "SELECT level FROM alert_events WHERE alert_key='media.dead' "
        "AND transition='fired'").fetchall()
    assert fired and fired[0][0] == "critical"


def test_incident_fires_once_per_episode(media):
    led, _ = media
    am = AlertManager(led, lambda: {"backend_ok": True})
    r1 = am.observe_incident("media.queue.depth", "o1", level="critical", value=999)
    r2 = am.observe_incident("media.queue.depth", "o2", level="critical", value=999)
    r3 = am.recover_incident("media.queue.depth")
    assert (r1.transition, r2.transition, r3.transition) == \
        ("fired", "active", "recovered")
    n = led.conn.execute(
        "SELECT COUNT(*) FROM alert_events WHERE alert_key='media.queue.depth' "
        "AND transition='fired'").fetchone()[0]
    assert n == 1                                         # 边沿触发不刷屏


def test_depth_thresholds_media_monitor(media):
    led, q = media
    for i in range(3):                                    # 3 > warn 阈值(调低)
        q.enqueue(f"p{i}", "stage", "s", {"i": i})
    am = AlertManager(led, lambda: {"backend_ok": True})
    from media.monitor import MediaMonitor
    mon = MediaMonitor(led, am, interval=999, queue=q,
                       thresholds={"depth_warn": 2, "depth_crit": 99,
                                   "fail_min_attempts": 999})
    mon.sample_once()
    fired = led.conn.execute(
        "SELECT level FROM alert_events WHERE alert_key='media.queue.depth' "
        "AND transition='fired'").fetchall()
    assert fired and fired[0][0] == "warning"
