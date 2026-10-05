# tests/pg/test_pg_ledger.py —— PG 账本：并发写 / 幂等 / 覆盖率
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import uuid, threading, time
import pytest
from audit.ledger_pg import PGLedger


def test_pg_log_and_read(pg_ledger):
    pg_ledger.log("t1", "a.py", "scanned", "")
    pg_ledger.log("t1", "b.py", "vuln", "发现密钥")
    assert pg_ledger.counts().get("scanned") == 1
    assert pg_ledger.counts().get("vuln") == 1
    assert pg_ledger.scanned_by("a.py") == ["t1"]
    assert pg_ledger.coverage({"a.py", "b.py", "c.py"}) == pytest.approx(2 / 3)


def test_pg_idempotent_by_event_id(pg_ledger):
    """同一 event_id 重复写只落一行（ON CONFLICT DO NOTHING）"""
    eid = uuid.uuid4()
    pg_ledger.log("t1", "x.py", "scanned", "", event_id=eid)
    pg_ledger.log("t1", "x.py", "scanned", "", event_id=eid)
    pg_ledger.log("t1", "x.py", "vuln", "改了", event_id=eid)   # 同 ID 不同内容
    n = pg_ledger.counts().get("scanned", 0) + pg_ledger.counts().get("vuln", 0)
    assert n == 1                                   # 幂等键生效


def test_pg_concurrent_writes(pg_ledger):
    """16 真并发线程写 320 行，一行不丢（SQLite 做不到这点）"""
    N_THREADS, PER = 16, 20
    errors = []

    def worker(tid):
        try:
            for i in range(PER):
                pg_ledger.log(f"t{tid}", f"file_{tid}_{i}.py", "scanned", "")
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(N_THREADS)]
    t0 = time.time()
    for t in threads: t.start()
    for t in threads: t.join()
    elapsed = time.time() - t0

    assert errors == []                             # 无 locked / 无异常
    total = sum(pg_ledger.counts().values())
    assert total == N_THREADS * PER                 # 320 行全落
    assert elapsed < 30                             # 真并发，不是串行


def test_pg_batch_write(pg_ledger):
    rows = [(f"t{i%3}", f"f{i}.py", "scanned", "") for i in range(50)]
    pg_ledger.log_many(rows)
    assert sum(pg_ledger.counts().values()) == 50


def test_pg_set_verdict(pg_ledger):
    pg_ledger.log("t1", "a.py", "vuln", "x")
    pg_ledger.set_verdict("a.py", "critical", "t1")
    with pg_ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT brain_verdict FROM ledger WHERE target='a.py'")
        assert cur.fetchone()[0] == "critical"


def test_pg_pool_metrics_capture(pg_ledger):
    """连接池监控真读到借还次数"""
    for i in range(10):
        pg_ledger.log("t1", f"f{i}.py", "scanned", "")
    snap = pg_ledger.metrics.snapshot()
    assert snap["total_borrowed"] >= 10
    assert snap["query_count"] >= 10
    assert snap["maxconn"] == 6
    assert snap["in_use"] == 0                      # 借的都还了


def test_pg_slow_query_recorded(pg_ledger):
    """慢查询阈值能真抓到慢 SQL"""
    pg_ledger.metrics.slow_ms = 0                   # 阈值归零，任何查询都算慢
    pg_ledger.log("t1", "a.py", "scanned", "")
    slow = pg_ledger.metrics.slow_queries(5)
    assert slow and "INSERT" in slow[0]["sql"].upper()
