# tests/test_scan_sessions.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from senses.scan_sessions import (ScanSession, observed_scan_seconds, scan_rate,
                                  ensure_tables, ensure_ledger_column,
                                  MIN_ACTIVE_SEC, MIN_EVENTS)
from panel.throughput import tentacle_throughput


def _ledger_schema(led):
    ensure_tables(led)
    with led._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS ledger(
            ts REAL, tentacle TEXT, target TEXT, status TEXT, note TEXT,
            scan_session_id TEXT)""")
    ensure_ledger_column(led)


def test_scan_session_links_events(ledger):
    _ledger_schema(ledger)
    run = "run-1"
    with ScanSession(ledger, run, "t1") as s:
        for i in range(3):
            with ledger._tx() as c, c.cursor() as cur:
                cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                            (time.time(), "t1", f"f{i}.py", "scanned", "", s.session_id))
        assert s.session_id
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state, ended_at FROM scan_sessions")
        st, en = cur.fetchone()
        assert st == "closed" and en is not None       # finally 必关闭


def test_short_session_rate_is_null(ledger):
    """短会话 → 速率必须 null + 样本不足，不能算出天文数字"""
    _ledger_schema(ledger)
    with ScanSession(ledger, "run-1", "t1") as s:
        for i in range(10):
            with ledger._tx() as c, c.cursor() as cur:
                cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                            (time.time(), "t1", f"f{i}.py", "scanned", "", s.session_id))
        time.sleep(0.2)                                 # 远小于 MIN_ACTIVE_SEC
    sec = observed_scan_seconds(ledger, "t1", time.time()-60, time.time()+60)
    assert sec is not None and sec < MIN_ACTIVE_SEC
    rate, why = scan_rate(ledger, "t1", time.time()-60, time.time()+60, linked_events=10)
    assert rate is None and "活跃时长不足" in why


def test_rate_needs_min_events(ledger):
    """活跃够但事件太少 → 仍 null"""
    _ledger_schema(ledger)
    with ledger._tx() as c, c.cursor() as cur:          # 手工造 600 秒活跃
        sid = "scan-t1-x"
        now = time.time()
        cur.execute("""INSERT INTO scan_sessions VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, "run-1", "tr", "t1", now-600, now, now, 5.0, "closed", "closed"))
        for k in range(0, 600, 5):
            cur.execute("INSERT INTO scan_heartbeats VALUES(?,?,0)", (sid, now-600+k))
    rate, why = scan_rate(ledger, "t1", now-700, now+10, linked_events=MIN_EVENTS-1)
    assert rate is None and "关联事件不足" in why


def test_parallel_sessions_no_double_count(ledger):
    """同一触手两个重叠会话 → 时长取并集，不重复计时"""
    _ledger_schema(ledger)
    now = time.time()
    with ledger._tx() as c, c.cursor() as cur:
        for sid in ("a", "b"):
            cur.execute("""INSERT INTO scan_sessions VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (sid, "run", "tr", "t1", now-100, now, now, 5.0, "closed", "closed"))
            for k in range(0, 100, 5):
                cur.execute("INSERT INTO scan_heartbeats VALUES(?,?,0)", (sid, now-100+k))
    sec = observed_scan_seconds(ledger, "t1", now-200, now+10)
    assert sec is not None and sec <= 105            # ~100s，不是 200s


def test_heartbeat_gap_not_counted(ledger):
    """断档超过 timeout 的部分不计入活跃时长"""
    _ledger_schema(ledger)
    now = time.time()
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO scan_sessions VALUES(?,?,?,?,?,?,?,?,?,?)""",
            ("s", "run", "tr", "t1", now-300, now, now, 5.0, "closed", "closed"))
        for ts in (now-300, now-295, now-60):        # 295→60 断档 235s，不计
            cur.execute("INSERT INTO scan_heartbeats VALUES(?,?,0)", ("s", ts))
    sec = observed_scan_seconds(ledger, "t1", now-400, now)
    assert sec is not None and 2 <= sec <= 10        # 只算 5 秒那段


def test_throughput_uses_scan_rate(ledger):
    _ledger_schema(ledger)
    now = time.time()
    with ledger._tx() as c, c.cursor() as cur:       # 造 400s 活跃 + 20 事件
        sid = "scan-t1-live"
        cur.execute("""INSERT INTO scan_sessions VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (sid, "run", "tr", "t1", now-400, now, now, 5.0, "closed", "closed"))
        for k in range(0, 400, 5):
            cur.execute("INSERT INTO scan_heartbeats VALUES(?,?,0)", (sid, now-400+k))
        for i in range(20):
            cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                        (now-200+i, "t1", f"f{i}", "scanned", "", sid))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    assert t["scan_events"] == 20
    assert t["scan_events_linked"] == 20
    assert t["scan_events_per_hour"] is not None      # 400s>=300 & 20>=5
    assert t["scan_active_seconds"] and t["scan_active_seconds"] >= MIN_ACTIVE_SEC


def test_unlinked_events_excluded_from_rate(ledger):
    _ledger_schema(ledger)
    now = time.time()
    with ledger._tx() as c, c.cursor() as cur:
        for i in range(10):                          # 历史行，无 scan_session_id
            cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?,NULL)",
                        (now-60, "t1", f"f{i}", "scanned", ""))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    assert t["scan_events"] == 10 and t["scan_events_linked"] == 0
    assert t["scan_events_per_hour"] is None          # 不猜关联
    assert any("未关联会话" in w for w in t["warnings"])
