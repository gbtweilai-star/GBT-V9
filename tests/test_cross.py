# tests/test_cross.py —— 交叉互扫：分配/认领/仲裁/边界
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from tests.fakes import FakeBrain


def _seed_vuln(ledger, target, scanner, detail="发现密钥"):
    ledger.log(scanner, target, "vuln", detail)


def test_cross_plan_excludes_owner(ledger):
    """分配必须排除原扫描者；每个目标拿 k 个不同复核者"""
    from scan.cross_scan import CrossBoard
    targets = ["a.py", "b.py", "c.py"]
    for t in targets: _seed_vuln(ledger, t, "t1")

    board = CrossBoard(ledger, FakeBrain())
    n = board.plan(targets, ["t1", "t2", "t3"], k=2)
    assert n == 6                                  # 3 目标 × 2 复核者

    rows = ledger.conn.execute(
        "SELECT target, original_scanner, reviewer FROM cross_tasks").fetchall()
    for target, owner, reviewer in rows:
        assert owner == "t1"
        assert reviewer != owner                   # 绝不自己查自己


def test_cross_k_out_of_range(ledger):
    """k 越界必须明确报错，不静默降级"""
    from scan.cross_scan import CrossBoard
    board = CrossBoard(ledger, FakeBrain())
    with pytest.raises(ValueError):
        board.plan(["a.py"], ["t1", "t2"], k=2)    # k 必须 ≤ N-1 = 1


def test_cross_no_duplicate_assignment(ledger):
    """同一 (target, reviewer) 不重复分配（UNIQUE 生效）"""
    from scan.cross_scan import CrossBoard
    _seed_vuln(ledger, "a.py", "t1")
    b1 = CrossBoard(ledger, FakeBrain(), run_id="run1")
    b1.plan(["a.py"], ["t1", "t2"], k=1)
    b1.plan(["a.py"], ["t1", "t2"], k=1)           # 再分配一次
    n = ledger.conn.execute("SELECT COUNT(*) FROM cross_tasks").fetchone()[0]
    assert n == 1                                   # 没变成 2


def test_cross_claim_and_arbitrate(ledger):
    """复核结论不一致 → 写仲裁行 + 上报大脑"""
    from scan.cross_scan import CrossBoard
    _seed_vuln(ledger, "a.py", "t1", detail="发现密钥")   # 原扫描者的结论
    brain = FakeBrain()
    board = CrossBoard(ledger, brain, run_id="run2")
    board.plan(["a.py"], ["t1", "t2"], k=1)

    # 复核者返回不同结论
    def different_scan(_target):
        return [{"rule": "无问题", "detail": "干净", "level": "low"}]

    r = board.claim("t2", different_scan, None)
    assert r is not None and r.agree is False

    arb = ledger.conn.execute(
        "SELECT target, verdict FROM cross_arbitration").fetchall()
    assert len(arb) == 1 and arb[0][0] == "a.py"    # 真写了仲裁行
    assert brain.asks                               # 真上报了大脑


def test_cross_agree_no_arbitration(ledger):
    """结论一致 → 不写仲裁，走完成"""
    from scan.cross_scan import CrossBoard
    _seed_vuln(ledger, "a.py", "t1", detail="发现密钥")
    board = CrossBoard(ledger, FakeBrain(), run_id="run3")
    board.plan(["a.py"], ["t1", "t2"], k=1)

    def same_scan(_t):
        return [{"rule": "密钥泄漏", "detail": "发现密钥", "level": "critical"}]

    r = board.claim("t2", same_scan, None)
    n = ledger.conn.execute("SELECT COUNT(*) FROM cross_arbitration").fetchone()[0]
    assert n == 0                                    # 一致就不仲裁
    prog = board.progress()
    assert prog["by_state"].get("done") == 1         # 状态推进到 done
