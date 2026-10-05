# tests/test_throughput.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from senses.sessions import ensure_tables, SessionTracker
from panel.throughput import tentacle_throughput


def _base_schema(led):
    with led._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")


def test_captured_vs_archived_delta_warning(ledger):
    _base_schema(ledger); ensure_tables(ledger)
    now = time.time()
    tr = SessionTracker(ledger, "t1", interval=1); tr.start()
    tr.beat(1000)
    with ledger._tx() as c, c.cursor() as cur:       # 归档只 900 帧
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,899,?,?)",
                    (now-30, "tr"))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    assert t["captured_frames"] == 1000
    assert t["archived_frames"] == 900
    assert t["frame_delta"] == 100
    assert any("不一致" in w for w in t["warnings"])   # 必须报"需核对"


def test_frames_per_hour_needs_uptime(ledger):
    _base_schema(ledger)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,3599,?,?)",
                    (time.time()-60, "tr"))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    # 没有心跳 → 无法算速率，必须是 None 而不是 0 或硬算
    assert t["frames_per_active_hour"] is None
    assert t["coverage"] in ("no_data", "unavailable")


def test_scan_rate_is_null_without_scan_session(ledger):
    """扫描没有独立活动时长 → 不给速率（不拿采集 uptime 冒充）"""
    _base_schema(ledger)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS ledger(
            ts REAL, tentacle TEXT, target TEXT, status TEXT, note TEXT)""")
        for i in range(10):
            cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                        (time.time()-60, "t1", f"f{i}.py", "scanned", ""))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    assert t["scan_events"] == 10
    assert t["scan_events_per_hour"] is None


def test_blocked_rate_not_vuln_based(ledger):
    _base_schema(ledger)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS ledger(
            ts REAL, tentacle TEXT, target TEXT, status TEXT, note TEXT)""")
        for st in ("scanned","scanned","blocked","vuln"):
            cur.execute("INSERT INTO ledger VALUES(?,?,?,?,?)",
                        (time.time()-60, "t1", "x", st, ""))
    t = tentacle_throughput(ledger, hours=1)["tentacles"][0]
    assert t["scan_events"] == 4 and t["blocked_events"] == 1
    assert t["blocked_rate"] == round(1/4, 4)          # 阻塞率，不看漏洞数


def test_source_unavailable_not_zero(ledger):
    """无任何明细表 → 全部 null + sources 标 unavailable，不伪装 0"""
    r = tentacle_throughput(ledger, hours=1)
    assert all(v == "unavailable" for v in r["sources"].values()) or r["tentacles"] == []


def test_no_load_score_invented(ledger):
    """返回结构里不得出现编造的负载分字段"""
    _base_schema(ledger)
    r = tentacle_throughput(ledger, hours=1)
    for t in r["tentacles"]:
        assert "load_score" not in t and "load" not in t
