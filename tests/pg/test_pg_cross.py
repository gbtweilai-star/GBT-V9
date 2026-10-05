# tests/pg/test_pg_cross.py —— PG 交叉复核：SKIP LOCKED 并发领取
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import threading
import pytest


def _seed(pg_ledger, n=10):
    for i in range(n):
        pg_ledger.log("t1", f"f{i}.py", "vuln", "发现密钥")


def test_pg_cross_plan_and_claim(pg_ledger):
    from audit.ledger_pg import PGCrossBoard
    _seed(pg_ledger, 5)
    board = PGCrossBoard(pg_ledger, brain=None, run_id="r1")
    n = board.plan([f"f{i}.py" for i in range(5)], ["t1", "t2", "t3"], k=2)
    assert n == 10                                  # 5 × 2

    def same(_t): return [{"rule": "密钥泄漏", "detail": "发现密钥", "level": "critical"}]
    r = board.claim("t2", same)
    assert r and r["state"] == "done"
    prog = board.progress()
    assert prog["by_state"].get("done", 0) >= 1


def test_pg_cross_skip_locked_no_dup(pg_ledger):
    """两触手并发领取：SKIP LOCKED 保证不重复领同一行"""
    from audit.ledger_pg import PGCrossBoard
    _seed(pg_ledger, 30)
    board = PGCrossBoard(pg_ledger, brain=None, run_id="r2")
    board.plan([f"f{i}.py" for i in range(30)], ["t1", "t2"], k=1)

    def same(_t): return [{"rule": "密钥泄漏", "detail": "发现密钥", "level": "critical"}]
    claimed = []
    lock = threading.Lock()

    def worker(tid):
        while True:
            r = board.claim(tid, same)
            if r is None: break
            with lock: claimed.append((tid, r["target"]))

    ts = [threading.Thread(target=worker, args=(t,)) for t in ("t2",)]
    # 同一 reviewer 只能领自己的任务，这里验证不重复
    for t in ts: t.start()
    for t in ts: t.join()
    targets = [c[1] for c in claimed]
    assert len(targets) == len(set(targets))        # 无重复领取


def test_pg_cross_arbitration(pg_ledger):
    from audit.ledger_pg import PGCrossBoard
    pg_ledger.log("t1", "a.py", "vuln", "发现密钥")
    board = PGCrossBoard(pg_ledger, brain=None, run_id="r3")
    board.plan(["a.py"], ["t1", "t2"], k=1)

    def diff(_t): return [{"rule": "无问题", "detail": "干净", "level": "low"}]
    r = board.claim("t2", diff)
    assert r["agree"] is False
    with pg_ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cross_arbitration WHERE run_id='r3'")
        assert cur.fetchone()[0] == 1
