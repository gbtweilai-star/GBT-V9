# scan/cross_scan.py —— 多对交叉互扫 · 任意两触手互相复查
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计要点（经复核）:
#   1. 独立复核任务表 cross_tasks，UNIQUE(run_id,target,reviewer) 防重复分配
#   2. 状态机 pending → claimed → done；异常转 retry / blocked
#   3. 短事务 BEGIN IMMEDIATE 认领 + 租约(lease)，认领后立刻提交，复核期间不持锁
#   4. 结论按规范化字段比较（规则ID/路径/行号/严重度），不比较原始JSON串
#   5. 不一致 → 幂等仲裁队列(UNIQUE run_id,target) → 上报大脑，保留双方原证
#   6. k 必须满足 0 <= k <= 触手数-1，否则明确报"未完成"
import json, time, uuid, random
from dataclasses import dataclass, asdict
from enum import Enum
from core.swallow import swallow as _swallow

class CrossState(str, Enum):
    PENDING = "pending"; CLAIMED = "claimed"; DONE = "done"
    RETRY   = "retry";   BLOCKED = "blocked"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cross_tasks(
    run_id TEXT, target TEXT, original_scanner TEXT, reviewer TEXT,
    state TEXT, attempts INTEGER DEFAULT 0, lease_until REAL,
    result_json TEXT, updated_at REAL,
    UNIQUE(run_id, target, reviewer));
CREATE INDEX IF NOT EXISTS idx_cross_state ON cross_tasks(run_id, state);
CREATE TABLE IF NOT EXISTS cross_arbitration(
    run_id TEXT, target TEXT, original_result TEXT, review_result TEXT,
    verdict TEXT, hint TEXT, created_at REAL, resolved_at REAL,
    UNIQUE(run_id, target));
"""

# ─────────────────────────────────────────────────────────────
@dataclass
class Review:
    target: str
    original_scanner: str
    reviewer: str
    original: list
    reviewed: list
    agree: bool

def normalize(findings) -> set:
    """把扫描结果规范成可比较的指纹集合：以 finding 的本质（detail 文本）为准。

    原扫描落账只存 detail 文本，复核者返回的是完整规则对象（rule/level 命名
    因规则包而异）——按 rule/level 逐字比对会把命名差异误判成分歧。
    因此指纹 = 归一化后的 detail（去空白、小写、截断）。"""
    out = set()
    if not findings: return out
    if isinstance(findings, str):
        try: findings = json.loads(findings)
        except Exception: return {(findings[:160],)}
    for f in findings:
        if isinstance(f, dict):
            d = str(f.get("detail") or f.get("rule") or f)
        else:
            d = str(f)
        out.add((" ".join(d.split()).lower()[:160],))
    return out

class CrossBoard:
    """交叉复核任务板：分配 / 认领 / 交卷 / 仲裁，全部落 DB 可追踪"""
    def __init__(self, ledger, brain=None, run_id=None,
                 lease_sec=300, max_attempts=3):
        self.ledger, self.brain = ledger, brain
        self.run_id = run_id or uuid.uuid4().hex[:10]
        self.lease_sec, self.max_attempts = lease_sec, max_attempts
        self.conn = ledger.conn                     # 复用线程本地连接
        self.conn.executescript(SCHEMA)

    # ── 分配：为每个目标选 k 个「不同于原扫描者」的复核者 ──
    def plan(self, targets, scanners, k=1):
        if not scanners:
            raise ValueError("无可用触手")
        if k < 0 or k > len(scanners) - 1:
            raise ValueError(f"k={k} 越界，必须满足 0 <= k <= {len(scanners)-1}")
        if k == 0:
            return 0
        # 取每个目标原扫描者（账本真读数）
        owners = {}
        for t in targets:
            who = self.ledger.scanned_by(t)
            owners[t] = who[0] if who else None
        # 负载计数用于轮转打破平局，避免饥饿
        load = {s: 0 for s in scanners}
        rows, n = [], 0
        for t in sorted(targets):
            owner = owners[t]
            if owner is None:
                continue                            # 没人扫过，无需复核
            pool = [s for s in scanners if s != owner]
            if not pool: continue
            pool.sort(key=lambda s: (load[s], random.random()))   # 最少负载优先
            for rv in pool[:k]:
                rows.append((self.run_id, t, owner, rv,
                             CrossState.PENDING.value, 0, 0.0, "", time.time()))
                load[rv] += 1; n += 1
        if rows:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO cross_tasks VALUES(?,?,?,?,?,?,?,?,?)", rows)
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK"); raise
        return n

    # ── 认领：短事务原子取一项，设租约后立刻提交（不持锁做复核）──
    def claim(self, reviewer, scan_fn, scan_rule):
        now = time.time()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            # 回收过期租约
            self.conn.execute(
                "UPDATE cross_tasks SET state=?, attempts=attempts+1, lease_until=0 "
                "WHERE run_id=? AND state=? AND lease_until<? AND attempts<?",
                (CrossState.RETRY.value, self.run_id, CrossState.CLAIMED.value,
                 now, self.max_attempts))
            # 超限的标 blocked，不伪装成完成
            self.conn.execute(
                "UPDATE cross_tasks SET state=? WHERE run_id=? AND state=? AND attempts>=?",
                (CrossState.BLOCKED.value, self.run_id, CrossState.RETRY.value,
                 self.max_attempts))
            row = self.conn.execute(
                "SELECT target, original_scanner FROM cross_tasks "
                "WHERE run_id=? AND reviewer=? AND state IN (?,?) "
                "ORDER BY updated_at LIMIT 1",
                (self.run_id, reviewer, CrossState.PENDING.value,
                 CrossState.RETRY.value)).fetchone()
            if not row:
                self.conn.execute("COMMIT"); return None
            target, owner = row
            self.conn.execute(
                "UPDATE cross_tasks SET state=?, lease_until=?, updated_at=? "
                "WHERE run_id=? AND target=? AND reviewer=?",
                (CrossState.CLAIMED.value, now + self.lease_sec, now,
                 self.run_id, target, reviewer))
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK"); raise

        # ── 事务外做真正的复核（不持锁）──
        try:
            reviewed = scan_fn(target)
        except Exception as e:
            self._finish(target, reviewer, None, f"复核异常: {e}", CrossState.RETRY)
            return {"target": target, "reviewer": reviewer, "state": "retry", "err": str(e)}

        original = self._original_findings(target, owner)
        agree = normalize(original) == normalize(reviewed)
        self._finish(target, reviewer, reviewed, "", CrossState.DONE)

        if not agree:
            self._arbitrate(target, owner, original, reviewed)   # 差异 → 幂等仲裁
        return Review(target, owner, reviewer, original, reviewed, agree)

    def _finish(self, target, reviewer, result, note, state):
        self.conn.execute(
            "UPDATE cross_tasks SET state=?, result_json=?, updated_at=? "
            "WHERE run_id=? AND target=? AND reviewer=?",
            (state.value, json.dumps(result, ensure_ascii=False) if result is not None else note,
             time.time(), self.run_id, target, reviewer))

    def _original_findings(self, target, owner):
        rows = self.conn.execute(
            "SELECT detail FROM ledger WHERE target=? AND scanner=? "
            "AND status='vuln' LIMIT 1", (target, owner)).fetchall()
        return [{"detail": r[0]} for r in rows] if rows else []

    # ── 仲裁：幂等入队 + 上报大脑，保留双方原证 ──
    def _arbitrate(self, target, owner, original, reviewed):
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO cross_arbitration VALUES(?,?,?,?,?,?,?,?)",
                (self.run_id, target, json.dumps(original, ensure_ascii=False),
                 json.dumps(reviewed, ensure_ascii=False), "", "", time.time(), 0))
        except Exception as e:
            _swallow(__file__, e)

        verdict = {"verdict": "disagree", "cmd": "review", "hint": "原扫描与复核不一致"}
        if self.brain:
            verdict = self.brain.ask(
                owner, target,
                f"交叉复核不一致: 原={original} 复核={reviewed} reviewer={owner}")
        self.conn.execute(
            "UPDATE cross_arbitration SET verdict=?, hint=?, resolved_at=? "
            "WHERE run_id=? AND target=?",
            (verdict.get("verdict", ""), verdict.get("hint", ""), time.time(),
             self.run_id, target))
        self.ledger.set_verdict(target, verdict.get("verdict", ""), owner)

    # ── 进度：分配/完成/卡住/待仲裁 分开统计 ──
    def progress(self) -> dict:
        rows = self.conn.execute(
            "SELECT state, COUNT(*) FROM cross_tasks WHERE run_id=? GROUP BY state",
            (self.run_id,)).fetchall()
        by_state = {s: n for s, n in rows}
        arb = self.conn.execute(
            "SELECT COUNT(*), SUM(CASE WHEN resolved_at>0 THEN 1 ELSE 0 END) "
            "FROM cross_arbitration WHERE run_id=?", (self.run_id,)).fetchone()
        return {"run_id": self.run_id, "by_state": by_state,
                "assigned": sum(by_state.values()),
                "arbitration_total": arb[0] or 0,
                "arbitration_resolved": arb[1] or 0}


# ─────────────────────────────────────────────────────────────
def cross_sweep(tentacles, ledger, targets, brain, k=1, workers=None):
    """
    多对交叉互扫 · 任意两根触手互相复查
    tentacles: [{"id":..., "scan": fn}, ...] 每根触手带自己的扫描函数
    k: 每个目标的复核者数量（0 <= k <= 触手数-1）
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from scan.scan_rules import scan as default_scan

    ids = [t["id"] for t in tentacles]
    fn_of = {t["id"]: t.get("scan", default_scan) for t in tentacles}
    board = CrossBoard(ledger, brain)
    assigned = board.plan(targets, ids, k=k)
    if assigned == 0:
        return {"assigned": 0, "progress": board.progress(),
                "note": "无目标需复核或 k=0"}

    def _worker(tid):
        out = []
        while True:
            r = board.claim(tid, fn_of[tid], None)
            if r is None: break
            out.append(r)
        return out

    results = []
    with ThreadPoolExecutor(max_workers=workers or len(ids),
                            thread_name_prefix="cross") as ex:
        for f in as_completed([ex.submit(_worker, tid) for tid in ids]):
            results.extend(f.result())

    disagreements = [r for r in results if isinstance(r, Review) and not r.agree]
    return {"assigned": assigned, "reviewed": len(results),
            "disagreements": len(disagreements),
            "progress": board.progress(),
            "detail": [asdict(r) for r in disagreements[:50]]}
