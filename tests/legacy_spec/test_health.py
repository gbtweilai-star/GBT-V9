# tests/test_health.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.health import tentacle_health


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


def _seg(led, tid, t, frames=1000):
    with led._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_segments VALUES(?,?,?,?,?,?)",
                    (f"s-{tid}-{t}", tid, 0, frames-1, t, "tr"))


def test_healthy_when_no_gaps(ledger):
    _schema(ledger); t = time.time()-60
    _seg(ledger, "t1", t)
    r = tentacle_health(ledger, hours=1)
    c = r["tentacles"][0]
    assert c["tentacle_id"] == "t1" and c["grade"] == "健康"
    assert c["metrics"]["missing_frames"] == 0
    assert c["metrics"]["restoration_rate"] is None      # 无丢帧 → None 不是 0
    assert c["dominant_metric"] is None


def test_permanent_is_abnormal(ledger):
    _schema(ledger); t = time.time()-60
    _seg(ledger, "t1", t)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,10,?,?)", (t,"tr"))
        cur.execute("""INSERT INTO repair_jobs VALUES('g1','tr','t1',NULL,10,19,10,
            'live_passed','none','permanent',0,3,0,0,0,0,10,NULL,?,?)""", (t,t))
    c = tentacle_health(ledger, hours=1)["tentacles"][0]
    assert c["grade"] == "异常"
    assert c["dominant_metric"] == "永久丢失"
    assert any("永久丢失" in e for e in c["evidence"])
    rec = c["recommendations"][0]
    assert rec["confidence"] == "高" and rec["mode"] == "manual"


def test_restored_fully_is_watch(ledger):
    _schema(ledger); t = time.time()-60
    _seg(ledger, "t1", t)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,5,?,?)", (t,"tr"))
        cur.execute("""INSERT INTO repair_jobs VALUES('g1','tr','t1',NULL,10,14,5,
            'writeback_failed','writeback','done',1,3,0,0,5,0,0,NULL,?,?)""", (t,t))
    c = tentacle_health(ledger, hours=1)["tentacles"][0]
    assert c["grade"] == "关注"               # 补齐率 100% → 关注
    assert c["metrics"]["restoration_rate"] == 1.0


def test_partial_restore_is_subhealth(ledger):
    _schema(ledger); t = time.time()-60
    _seg(ledger, "t1", t)
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,10,?,?)", (t,"tr"))
        cur.execute("""INSERT INTO repair_jobs VALUES('g1','tr','t1',NULL,10,19,10,
            'writeback_failed','writeback','done',1,3,0,0,4,0,0,NULL,?,?)""", (t,t))
    c = tentacle_health(ledger, hours=1)["tentacles"][0]
    assert c["grade"] == "亚健康"             # 补齐率 40% < 99%


def test_insufficient_sample_no_percentage(ledger):
    """只有 gap 没有帧段覆盖 → 样本不足，不出丢帧率"""
    _schema(ledger); t = time.time()-60
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("INSERT INTO frame_gaps VALUES('g1','t1',10,3,?,?)", (t,"tr"))
    c = tentacle_health(ledger, hours=1)["tentacles"][0]
    assert c["metrics"]["loss_per_100k"] is None      # 无有效帧数 → 不出百分比
    assert c["grade"] in ("亚健康", "异常")            # 有丢帧仍是问题


def test_no_data_is_insufficient_not_healthy(ledger):
    _schema(ledger)
    r = tentacle_health(ledger, hours=1)
    assert r["tentacles"] == []                        # 无数据不编造健康卡
