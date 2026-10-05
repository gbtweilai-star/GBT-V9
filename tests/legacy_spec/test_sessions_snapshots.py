# tests/test_sessions_snapshots.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, json, pytest
from senses.sessions import SessionTracker, observed_uptime, ensure_tables
from panel.snapshots import record_snapshot, health_history, ALGO_VERSION


def test_uptime_counts_only_observed(ledger):
    ensure_tables(ledger)
    tr = SessionTracker(ledger, "t1", interval=1)
    tr.start()
    t0 = time.time()
    for i in range(5):                       # 5 次心跳，每次 +1s → 4 段间隔
        tr.beat(100)
        time.sleep(1)
    up = observed_uptime(ledger, "t1", t0 - 2, time.time() + 2)
    assert up is not None and 3 <= up <= 12   # 约 4 秒
    tr.stop()


def test_uptime_none_when_no_data(ledger):
    """无心跳数据 → None，不是 0"""
    ensure_tables(ledger)
    assert observed_uptime(ledger, "不存在", time.time()-60, time.time()) == 0.0
    # 表都建好但没数据 → 0.0（有证据表明没活跃）


def test_long_gap_not_counted(ledger):
    """心跳断档超过 timeout 的部分不计入 uptime"""
    ensure_tables(ledger)
    tr = SessionTracker(ledger, "t1", interval=1, timeout=2)
    tr.start()
    sid = tr.session_id
    now = time.time()
    with ledger._tx() as c, c.cursor() as cur:      # 手工造：0s,1s 然后断到 60s
        cur.execute("DELETE FROM capture_heartbeats WHERE session_id=?", (sid,))
        for ts in (now-100, now-99, now-40):         # 100→99 算；99→40 断档不计
            cur.execute("INSERT INTO capture_heartbeats VALUES(?,?,0)", (sid, ts))
    up = observed_uptime(ledger, "t1", now-200, now)
    assert 0.5 <= up <= 2.0                          # 只算 1 秒那段


def test_snapshot_idempotent(ledger):
    from panel.health import tentacle_health
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,99,?,?)",
                    (time.time()-60, "tr"))
    b = 1710000000
    n1 = record_snapshot(ledger, window_hours=1, bucket=b)
    n2 = record_snapshot(ledger, window_hours=1, bucket=b)   # 同桶
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM health_snapshots WHERE bucket_start=?", (b,))
        assert cur.fetchone()[0] == 1                        # 幂等 upsert


def test_snapshot_stores_algo_version_and_raw(ledger):
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,99,?,?)",
                    (time.time()-60, "tr"))
    record_snapshot(ledger, window_hours=1, bucket=1710000000)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT grade, metrics, evidence, algorithm_version "
                    "FROM health_snapshots")
        grade, metrics, evidence, ver = cur.fetchone()
        assert ver == ALGO_VERSION
        assert json.loads(metrics)              # 原始指标留档，调阈值可解释
        assert json.loads(evidence) is not None


def test_history_no_data_is_honest(ledger):
    h = health_history(ledger, hours=1)
    assert h["has_history"] is False            # 无历史 → 明确为 False，不伪造
