# tests/test_trends_tentacle.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.trends import by_tentacle, _assign_colors


def _schema(led):
    with led._tx() as c, c.cursor() as cur:
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_segments(
            seg_id TEXT PRIMARY KEY, tentacle_id TEXT, start_frame INT,
            end_frame INT, ts REAL, trace_id TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS frame_gaps(
            gap_id TEXT PRIMARY KEY, tentacle_id TEXT, frame_no INT,
            missing INT, ts REAL, trace_id TEXT)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS repair_jobs(
            gap_id TEXT PRIMARY KEY, trace_id TEXT, tentacle_id TEXT, stream_id TEXT,
            frame_start INT, frame_end INT, missing INT, cause TEXT, strategy TEXT,
            state TEXT, attempts INT, max_attempts INT, next_retry_at REAL,
            lease_until REAL, restored_frames INT, resampled_frames INT,
            permanent_frames INT, error TEXT, created_at REAL, updated_at REAL)""")
        cur.execute("""CREATE TABLE IF NOT EXISTS repair_attempts(
            id INTEGER PRIMARY KEY AUTOINCREMENT, gap_id TEXT, trace_id TEXT,
            attempt_no INT, strategy TEXT, cause TEXT, status TEXT, restored INT,
            resampled INT, permanent INT, artifact_sha TEXT, artifact TEXT,
            error TEXT, detail TEXT, ts REAL)""")


def test_groups_and_ranks_by_tentacle(ledger):
    _schema(ledger); t = time.time()-60
    with ledger._tx() as c, c.cursor() as cur:
        for tid, n in (("t1", 3), ("t1", 5), ("t2", 2)):
            cur.execute("INSERT INTO frame_segments VALUES(?,?,?,?,?,?)",
                        (f"s-{tid}-{n}", tid, 0, 10, t, "tr"))
            cur.execute("INSERT INTO frame_gaps VALUES(?,?,?,?,?,?)",
                        (f"g-{tid}-{n}", tid, 10, n, t, "tr"))
    r = by_tentacle(ledger, hours=1, bucket=300)
    assert r["summary"]["tentacles"] == 2
    assert r["summary"]["dominant"] == "t1"          # t1 共 8 帧 > t2 2 帧
    top = r["series"][0]
    assert top["tentacle_id"] == "t1" and top["total_missing"] == 8
    assert r["summary"]["dominant_share"] == round(8/10, 3)


def test_per_tentacle_no_data_is_null(ledger):
    """t2 只在某一桶有采集，其余桶应 null 而非 0"""
    _schema(ledger); t = time.time()-60
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_segments VALUES('s1','t2',0,10,?,?)", (t,"tr"))
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t2',10,4,?,?)", (t,"tr"))
    r = by_tentacle(ledger, hours=2, bucket=300)
    pts = r["series"][0]["points"]
    assert pts[-1]["missing"] == 4                    # 有覆盖 + 有 gap
    assert any(p["missing"] is None for p in pts)     # 其余桶 null 不是 0


def test_other_is_partial_subtotal(ledger):
    _schema(ledger); t = time.time()-60
    with ledger._tx() as c, c.cursor() as cur:
        for i in range(7):                            # 7 根触手，top=5 → 2 根进 other
            cur.execute("INSERT INTO frame_segments VALUES(?,?,?,?,?,?)",
                        (f"s{i}", f"t{i}", 0, 10, t, "tr"))
            cur.execute("INSERT INTO frame_gaps VALUES(?,?,?,?,?,?)",
                        (f"g{i}", f"t{i}", 10, i+1, t, "tr"))
    r = by_tentacle(ledger, hours=1, bucket=300, top=5)
    assert len(r["series"]) == 5
    assert r["other"] and r["other"]["partial"] is True
    assert r["other"]["members"] == 2
    last = r["other"]["points"][-1]
    assert last["missing"] == 2+1                     # t5(6帧?) 具体值按插入序；仅验小计可算
    assert last["observed_count"] == 2 and last["total_count"] == 2


def test_colors_stable_across_calls(ledger):
    a = _assign_colors(["t1","t2","t3"])
    b = _assign_colors(["t3","t1","t2"])             # 传入顺序不同
    assert a == b                                     # 映射不变


def test_ranking_has_compensation(ledger):
    _schema(ledger); t = time.time()-60
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_segments VALUES('s1','t1',0,10,?,?)", (t,"tr"))
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,8,?,?)", (t,"tr"))
        cur.execute("""INSERT INTO repair_jobs VALUES('g1','tr','t1',NULL,10,17,8,
            'writeback_failed','writeback','done',1,3,0,0,6,0,0,NULL,?,?)""", (t,t))
        cur.execute("""INSERT INTO repair_attempts
            (gap_id,trace_id,attempt_no,strategy,cause,status,restored,resampled,
             permanent,artifact_sha,artifact,error,detail,ts)
            VALUES('g1','tr',1,'writeback','writeback_failed','restored',6,0,0,
                   'ab','buf://g1',NULL,NULL,?)""", (t,))
    r = by_tentacle(ledger, hours=1, bucket=300)
    row = r["ranking"][0]
    assert row["tentacle_id"] == "t1" and row["restored"] == 6
    assert row["recovery_rate"] == round(6/8, 3)
