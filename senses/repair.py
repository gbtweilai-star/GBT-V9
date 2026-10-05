# senses/repair.py —— 丢帧补偿：分类 → 有界重试 → 回写同一 trace
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 原则:
#   只把"原始缺帧确实恢复"计为补齐(restored); 重采记 resampled, 不冒充补齐;
#   不可补记 permanent_loss, 绝不伪造; 原因不明记 unknown, 不推断。
#   数据库事务里不执行磁盘/网络/扫描; 用租约 + 有界重试 + 指数退避。
import os, time, json, threading
from dataclasses import dataclass, field
from senses.sqldialect import txn


@dataclass
class GapContext:
    """补偿所需上下文（由吞噬能/告警桥提供）"""
    gap_id: str
    tentacle_id: str
    trace_id: str | None = None
    frame_start: int = 0
    frame_end: int = 0
    missing: int = 0
    stream_id: str | None = None
    ts: float = 0.0
    cause_hint: str | None = None      # 已知成因；None → 探测


@dataclass
class RepairResult:
    status: str = "failed"             # restored|resampled|permanent|failed
    restored: int = 0
    resampled: int = 0
    permanent: int = 0
    artifact_sha: str = ""
    artifact: str = ""
    error: str = ""
    detail: dict = field(default_factory=dict)


# ══ 表 ══
def _ddl(d):
    from audit.ddl import pk, epoch, bool_t
    return f"""
    CREATE TABLE IF NOT EXISTS repair_jobs(
        gap_id TEXT PRIMARY KEY, trace_id TEXT, tentacle_id TEXT, stream_id TEXT,
        frame_start INTEGER, frame_end INTEGER, missing INTEGER,
        cause TEXT, strategy TEXT, state TEXT NOT NULL,
        attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 3,
        next_retry_at {epoch(d)} DEFAULT 0, lease_until {epoch(d)} DEFAULT 0,
        restored_frames INTEGER DEFAULT 0, resampled_frames INTEGER DEFAULT 0,
        permanent_frames INTEGER DEFAULT 0,
        error TEXT, created_at {epoch(d)}, updated_at {epoch(d)});
    CREATE INDEX IF NOT EXISTS idx_repair_ready
        ON repair_jobs(state, next_retry_at);

    CREATE TABLE IF NOT EXISTS repair_attempts(
        id {pk(d)}, gap_id TEXT NOT NULL, trace_id TEXT, attempt_no INTEGER NOT NULL,
        strategy TEXT, cause TEXT, status TEXT,
        restored INTEGER DEFAULT 0, resampled INTEGER DEFAULT 0,
        permanent INTEGER DEFAULT 0, artifact_sha TEXT, artifact TEXT,
        error TEXT, detail TEXT, ts {epoch(d)} NOT NULL,
        UNIQUE(gap_id, attempt_no));
    CREATE INDEX IF NOT EXISTS idx_attempt_trace ON repair_attempts(trace_id, ts);
    """


class RepairPlanner:
    """分类器：判定可补性。prover 由外部注入，便于测试与替换。"""
    def __init__(self, has_buffered=None, can_resample=None,
                 is_live_passed=None):
        self.has_buffered = has_buffered or (lambda g: False)
        self.can_resample = can_resample or (lambda g: False)
        self.is_live_passed = is_live_passed or (lambda g: False)

    def classify(self, g: GapContext):
        if g.cause_hint:
            cause = g.cause_hint
        else:
            try:
                if self.has_buffered(g):
                    cause = "writeback_failed"
                elif self.is_live_passed(g):
                    cause = "live_passed"
                elif self.can_resample(g):
                    cause = "not_captured"
                else:
                    cause = "unknown"
            except Exception:
                cause = "unknown"
        strategy = {"writeback_failed": "writeback",
                    "not_captured": "resample",
                    "live_passed": "none"}.get(cause, "none")
        return cause, strategy


class RepairWorker:
    """补偿执行器：领取任务 → 执行策略 → 记尝试 → 回写同一 trace"""
    def __init__(self, ledger, planner=None, gap_bridge=None,
                 writeback=None, resample=None, verify=None,
                 concurrency=2, lease=120, base_backoff=30,
                 max_attempts=3, interval=5.0, brain=None):
        self.led, self.brain = ledger, brain
        self.planner = planner or RepairPlanner()
        self.gap_bridge = gap_bridge
        self.writeback = writeback          # callable(GapContext)->RepairResult
        self.resample = resample            # callable(GapContext)->RepairResult
        self.verify = verify               # callable(gap_id, result)->bool
        self.concurrency, self.lease = concurrency, lease
        self.base_backoff, self.max_attempts = base_backoff, max_attempts
        self.interval = interval
        self._stop = threading.Event()
        self._t = None
        self._init()

    def _init(self):
        with txn(self.led) as cur:
            from audit.ddl import run_script
            run_script(cur, _ddl(self.led.dialect), self.led.dialect)

    # ── 入队（由 GapAlertBridge 调用；与 gap 同事务可选）──
    def enqueue(self, g: GapContext, cur=None):
        cause, strategy = self.planner.classify(g)
        state = "permanent" if strategy == "none" else "queued"
        permanent = g.missing if state == "permanent" else 0
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        sql = f"""INSERT INTO repair_jobs
            (gap_id,trace_id,tentacle_id,stream_id,frame_start,frame_end,missing,
             cause,strategy,state,attempts,max_attempts,next_retry_at,lease_until,
             permanent_frames,created_at,updated_at)
            VALUES({','.join([ph]*17)})
            ON CONFLICT (gap_id) DO NOTHING"""
        args = (g.gap_id, g.trace_id, g.tentacle_id, g.stream_id,
                g.frame_start, g.frame_end, g.missing, cause, strategy, state,
                0, self.max_attempts, time.time(), 0.0, permanent,
                time.time(), time.time())
        if cur is not None:
            cur.execute(sql, args); return cause, strategy
        with txn(self.led) as cur2:
            cur2.execute(sql, args)
        return cause, strategy

    # ── 领取（PG: SKIP LOCKED；SQLite: BEGIN IMMEDIATE 已独占写）──
    def _claim(self):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        now = time.time()
        out = []
        with txn(self.led) as cur:
            if self.led.dialect == "sqlite":
                cur.execute(f"""SELECT gap_id FROM repair_jobs
                    WHERE state='queued' AND next_retry_at<={ph} AND lease_until<={ph}
                    ORDER BY next_retry_at LIMIT {self.concurrency}""", (now, now))
            else:
                cur.execute(f"""SELECT gap_id FROM repair_jobs
                    WHERE state='queued' AND next_retry_at<=%s AND lease_until<=%s
                    ORDER BY next_retry_at LIMIT {self.concurrency}
                    FOR UPDATE SKIP LOCKED""", (now, now))
            ids = [r[0] for r in cur.fetchall()]
            for gid in ids:
                cur.execute(f"""UPDATE repair_jobs SET state='running',
                    attempts=attempts+1, lease_until={ph}, updated_at={ph}
                    WHERE gap_id={ph}""", (now + self.lease, now, gid))
                out.append(gid)
        return out

    def _job(self, gap_id):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        with txn(self.led) as cur:
            cur.execute(f"SELECT * FROM repair_jobs WHERE gap_id={ph}", (gap_id,))
            cols = [d[0] for d in cur.description]
            row = cur.fetchone()
            return dict(zip(cols, row)) if row else None

    def _ctx(self, job):
        return GapContext(gap_id=job["gap_id"], tentacle_id=job["tentacle_id"],
                          trace_id=job["trace_id"], frame_start=job["frame_start"],
                          frame_end=job["frame_end"], missing=job["missing"],
                          stream_id=job["stream_id"],
                          cause_hint=job["cause"])

    # ── 执行策略（事务外！）──
    def _execute(self, job):
        ctx = self._ctx(job)
        strat = job["strategy"]
        if strat == "writeback":
            if not self.writeback:
                return RepairResult(status="permanent", permanent=ctx.missing,
                                    error="无写回通道（缓冲区不可用）")
            return self.writeback(ctx)
        if strat == "resample":
            if not self.resample:
                return RepairResult(status="permanent", permanent=ctx.missing,
                                    error="无重采通道")
            return self.resample(ctx)
        return RepairResult(status="permanent", permanent=ctx.missing,
                            error=f"策略 {strat} 不可补")

    # ── 落账 + 状态机推进 ──
    def _finish(self, job, res: RepairResult):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        attempt_no = (job["attempts"] or 0) + 1
        now = time.time()
        if res.status in ("restored", "resampled"):
            state, backoff = "done", 0
        elif res.status == "permanent":
            state, backoff = "permanent", 0
        else:                                       # failed
            if attempt_no >= (job["max_attempts"] or self.max_attempts):
                state, backoff = "dead_letter", 0
            else:
                state = "queued"
                backoff = self.base_backoff * (2 ** (attempt_no - 1))
        with txn(self.led) as cur:
            cur.execute(f"""INSERT INTO repair_attempts
                (gap_id,trace_id,attempt_no,strategy,cause,status,restored,resampled,
                 permanent,artifact_sha,artifact,error,detail,ts)
                VALUES({','.join([ph]*14)})""",
                (job["gap_id"], job["trace_id"], attempt_no, job["strategy"],
                 job["cause"], res.status, res.restored, res.resampled, res.permanent,
                 res.artifact_sha, res.artifact, res.error[:500],
                 json.dumps(res.detail, ensure_ascii=False)[:2000], now))
            cur.execute(f"""UPDATE repair_jobs SET state={ph}, next_retry_at={ph},
                lease_until=0, restored_frames=restored_frames+{ph},
                resampled_frames=resampled_frames+{ph},
                permanent_frames={ph}, error={ph}, updated_at={ph}
                WHERE gap_id={ph}""",
                (state, now + backoff, res.restored, res.resampled,
                 res.permanent or job["permanent_frames"], res.error[:500],
                 now, job["gap_id"]))
        # 只有"真恢复且校验通过"才驱动告警恢复（重采不恢复历史缺帧）
        if res.status == "restored" and self.gap_bridge:
            ok = True
            if self.verify:
                try: ok = bool(self.verify(job["gap_id"], res))
                except Exception: ok = False
            if ok:
                self.gap_bridge.on_verified_clean_segment(
                    job["tentacle_id"], trace_id=job["trace_id"],
                    segment_id=res.artifact, stream_id=job["stream_id"])
        return state

    def run_once(self):
        n = 0
        for gid in self._claim():
            job = self._job(gid)
            if not job: continue
            try:
                res = self._execute(job)
            except Exception as e:
                res = RepairResult(status="failed", error=f"{type(e).__name__}: {e}")
            try:
                self._finish(job, res); n += 1
            except Exception as e:
                print("[repair] 落账失败:", e)
        return n

    def _loop(self):
        while not self._stop.is_set():
            try: self.run_once()
            except Exception as e: print("[repair] worker 异常:", e)
            self._stop.wait(self.interval)

    def start(self):
        if not (self._t and self._t.is_alive()):
            self._stop.clear()
            self._t = threading.Thread(target=self._loop, daemon=True, name="repair")
            self._t.start()
        return self

    def stop(self): self._stop.set()

    # ── 统计（面板用）──
    def stats(self):
        with txn(self.led) as cur:
            cur.execute("""SELECT state, COUNT(*) FROM repair_jobs GROUP BY state""")
            jobs = {r[0]: r[1] for r in cur.fetchall()}
            cur.execute("""SELECT COALESCE(SUM(restored_frames),0),
                                  COALESCE(SUM(resampled_frames),0),
                                  COALESCE(SUM(permanent_frames),0)
                           FROM repair_jobs""")
            r = cur.fetchone()
        return {"by_state": jobs,
                "restored_frames": r[0], "resampled_frames": r[1],
                "permanent_frames": r[2],
                "pending": jobs.get("queued", 0),
                "running": jobs.get("running", 0),
                "dead_letter": jobs.get("dead_letter", 0),
                "worker_alive": bool(self._t and self._t.is_alive())}
