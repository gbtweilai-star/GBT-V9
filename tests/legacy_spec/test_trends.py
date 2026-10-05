# tests/test_trends.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.trends import repair_trends, _auto_bucket


def test_auto_bucket():
    assert _auto_bucket(24*3600) == 300
    assert _auto_bucket(7*86400) == 3600
    assert _auto_bucket(365*86400) >= 86400
    assert 365*86400 / _auto_bucket(365*86400) <= 500     # 桶数上限


def test_zero_vs_no_data_distinct(ledger):
    """有覆盖无事件 → 0；无覆盖 → null"""
    # 造一个帧段（证明当时在采集）
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,100,?,?)",
                    (time.time()-60, "tr1"))
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_gaps(
            gap_id TEXT PRIMARY KEY, tentacle_id TEXT, frame_no INT,
            missing INT, ts REAL, trace_id TEXT)""")
    r = repair_trends(ledger, hours=1, bucket=300)
    bks = r["buckets"]
    # 最近一桶：有帧段覆盖 + 无 gap → 0（不是 null）
    cov = [b for b in bks if b["coverage"] == "observed"]
    assert cov, "应有观察到覆盖的桶"
    assert all(b["missing"] == 0 for b in cov)
    # 无覆盖的桶 → null
    nod = [b for b in bks if b["coverage"] == "no_data"]
    assert all(b["missing"] is None for b in nod)


def test_gap_summed_into_bucket(ledger):
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_gaps(
            gap_id TEXT PRIMARY KEY, tentacle_id TEXT, frame_no INT,
            missing INT, ts REAL, trace_id TEXT)""")
        t = time.time()-60
        cur.execute("INSERT INTO frame_segments VALUES('s2','t1',0,100,?,?)", (t,"tr1"))
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,3,?,?)", (t,"tr1"))
        cur.execute("INSERT INTO frame_gaps VALUES('g2','t1',20,5,?,?)", (t,"tr1"))
    r = repair_trends(ledger, hours=1, bucket=300)
    assert r["summary"]["total_missing"] == 8


def test_unavailable_source_returns_null_not_zero(ledger):
    """帧段表都不存在 → 丢帧全 null + coverage=unavailable"""
    r = repair_trends(ledger, hours=1, bucket=300)
    assert r["series_coverage"]["missing"] in ("unavailable", "observed")
    if r["series_coverage"]["missing"] == "unavailable":
        assert all(b["missing"] is None for b in r["buckets"])
        assert r["summary"]["total_missing"] == 0
