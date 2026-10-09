# media/queue.py —— 持久化生成任务队列（SQLite / PG 双后端）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 领取原子 + fencing token(lease_owner 不匹配者不得写回)
#   - 等 GPU 期间仍可 heartbeat 续租; 租约过期被回收重抢
#   - 去重键 = project_id + stage + params_hash (含 stage, 防跨阶段误合并)
#   - 重试有上限的指数退避 + 抖动; 耗尽转死信
from core.swallow import swallow as _swallow
import os, time, json, uuid, hashlib, threading
from senses.sqldialect import txn
from audit.ddl import run_script

TERMINAL = ("completed", "dead")
ACTIVE = ("queued", "running", "completed")


def _ph(led):
    return "?" if led.dialect == "sqlite" else "%s"


def _ddl(d):
    from audit.ddl import epoch
    return f"""
    CREATE TABLE IF NOT EXISTS media_jobs(
        job_id        TEXT PRIMARY KEY,
        project_id    TEXT NOT NULL,
        stage         TEXT NOT NULL,
        skill         TEXT NOT NULL,
        params_hash   TEXT NOT NULL,
        params        TEXT NOT NULL,
        priority      INTEGER NOT NULL DEFAULT 0,
        state         TEXT NOT NULL DEFAULT 'queued',
        attempts      INTEGER NOT NULL DEFAULT 0,
        max_attempts  INTEGER NOT NULL DEFAULT 3,
        lease_owner   TEXT,
        lease_expires {epoch(d)},
        checkpoint    TEXT,
        artifact_sha  TEXT,
        error         TEXT,
        next_run_at   {epoch(d)} NOT NULL DEFAULT 0,
        created_at    {epoch(d)} NOT NULL,
        updated_at    {epoch(d)} NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_jobs_claim
        ON media_jobs(state, next_run_at, priority);
    CREATE INDEX IF NOT EXISTS idx_jobs_lease
        ON media_jobs(state, lease_expires);
    CREATE INDEX IF NOT EXISTS idx_jobs_proj ON media_jobs(project_id);
    -- 去重: 同一 project+stage+输入 只保留一份 (dead 可重入)
    CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_dedup
        ON media_jobs(project_id, stage, params_hash) WHERE state <> 'dead';

    CREATE TABLE IF NOT EXISTS media_job_events(
        event_id   TEXT PRIMARY KEY,
        job_id     TEXT NOT NULL,
        attempt_no INTEGER NOT NULL DEFAULT 0,
        event      TEXT NOT NULL,
        ts         {epoch(d)} NOT NULL,
        detail     TEXT);
    CREATE INDEX IF NOT EXISTS idx_media_events_ts
        ON media_job_events(ts);
    CREATE INDEX IF NOT EXISTS idx_media_events_job_ts
        ON media_job_events(job_id, ts);
    """


def ensure_tables(led):
    with txn(led) as cur:
        ddl = _ddl(led.dialect)
        run_script(cur, ddl, led.dialect)


def params_hash(skill, params) -> str:
    blob = json.dumps({"skill": skill, "p": params}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class JobQueue:
    """持久化队列。所有写回必须带 lease_owner (fencing token)。"""

    def __init__(self, ledger, lease_sec=300, backoff_base=5.0, backoff_cap=600.0):
        self.led = ledger
        self.lease_sec = float(lease_sec)
        self.backoff_base = float(backoff_base)
        self.backoff_cap = float(backoff_cap)
        self._lock = threading.Lock()

    # ── 入队（去重：已存在则返回原任务） ──
    def enqueue(self, project_id, stage, skill, params, *, priority=0,
                max_attempts=3, force=False):
        now = time.time()
        ph = _ph(self.led)
        h = params_hash(skill, params)
        jid = f"{stage}-{uuid.uuid4().hex[:12]}"
        with txn(self.led) as cur:
            if not force:
                cur.execute(f"""SELECT * FROM media_jobs
                    WHERE project_id={ph} AND stage={ph} AND params_hash={ph}
                      AND state <> 'dead'""", (project_id, stage, h))
                row = cur.fetchone()
                if row:
                    return self._row(cur, row)          # 已存在 → 返回原任务
            cur.execute(f"""INSERT INTO media_jobs(
                job_id,project_id,stage,skill,params_hash,params,priority,
                state,attempts,max_attempts,next_run_at,created_at,updated_at)
                VALUES({ph},{ph},{ph},{ph},{ph},{ph},{ph},
                       'queued',0,{ph},{ph},{ph},{ph})""",
                (jid, project_id, stage, skill, h,
                 json.dumps(params, ensure_ascii=False), priority,
                 max_attempts, now, now, now))
            self._event(cur, jid, "enqueued", 0, stage)
            cur.execute(f"SELECT * FROM media_jobs WHERE job_id={ph}", (jid,))
            return self._row(cur, cur.fetchone())

    # ── 原子领取（带 fencing token） ──
    def claim(self, worker_id, now=None):
        """原子领取一个可运行任务；无则返回 None。
        PG: UPDATE ... WHERE job_id=(SELECT ... SKIP LOCKED)
        SQLite: 单条 UPDATE，天然串行化（写锁）。"""
        now = now if now is not None else time.time()
        ph = _ph(self.led)
        owner = f"{worker_id}:{uuid.uuid4().hex[:8]}"
        lease_until = now + self.lease_sec
        skip = " FOR UPDATE SKIP LOCKED" if self.led.dialect != "sqlite" else ""
        sel = f"""SELECT job_id FROM media_jobs
                  WHERE state='queued' AND next_run_at<={ph}
                    AND attempts < max_attempts
                  ORDER BY priority DESC, created_at ASC LIMIT 1{skip}"""
        with self._lock, txn(self.led) as cur:
            cur.execute(f"""UPDATE media_jobs SET
                    state='running', lease_owner={ph}, lease_expires={ph},
                    attempts=attempts+1, updated_at={ph}
                WHERE job_id = ({sel})
                RETURNING *""", (owner, lease_until, now, now))
            row = cur.fetchone()
            if row:
                job = self._row(cur, row)      # 先取行（description 属于 RETURNING）
                self._event(cur, row[0], "claimed", 1)
                return job
            return None

    # ── 续租（等 GPU 时也要调，防租约过期被抢） ──
    def heartbeat(self, job_id, owner, now=None):
        now = now if now is not None else time.time()
        ph = _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""UPDATE media_jobs
                SET lease_expires={ph}, updated_at={ph}
                WHERE job_id={ph} AND lease_owner={ph} AND state='running'""",
                (now + self.lease_sec, now, job_id, owner))
            return cur.rowcount == 1

    # ── 成功 ──
    def complete(self, job_id, owner, artifact_sha=None, checkpoint=None):
        now = time.time(); ph = _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""UPDATE media_jobs SET
                    state='completed', lease_owner=NULL, lease_expires=NULL,
                    artifact_sha={ph}, checkpoint={ph}, error=NULL, updated_at={ph}
                WHERE job_id={ph} AND lease_owner={ph}""",
                (artifact_sha, checkpoint, now, job_id, owner))
            if cur.rowcount == 1:
                self._event(cur, job_id, "completed")
                return True
            return False        # False = 已被回收, 说明本 worker 是过期者

    # ── 失败 / 退避重试 / 死信 ──
    def fail(self, job_id, owner, error, now=None):
        now = now if now is not None else time.time()
        ph = _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"SELECT attempts,max_attempts FROM media_jobs "
                        f"WHERE job_id={ph} AND lease_owner={ph}", (job_id, owner))
            r = cur.fetchone()
            if not r:
                return "stale"             # 不是当前持有者 → 忽略
            attempts, max_attempts = int(r[0]), int(r[1])
            if attempts >= max_attempts:
                cur.execute(f"""UPDATE media_jobs SET state='dead', lease_owner=NULL,
                        lease_expires=NULL, error={ph}, updated_at={ph}
                    WHERE job_id={ph}""", (str(error)[:500], now, job_id))
                self._event(cur, job_id, "failed", attempts, error)   # 末次尝试算失败
                self._event(cur, job_id, "dead", attempts, error)
                return "dead"
            import random
            delay = min(self.backoff_base * (2 ** (attempts - 1)), self.backoff_cap)
            delay *= (0.5 + random.random())                # 抖动
            cur.execute(f"""UPDATE media_jobs SET state='queued', lease_owner=NULL,
                    lease_expires=NULL, next_run_at={ph}, error={ph}, updated_at={ph}
                WHERE job_id={ph}""", (now + delay, str(error)[:500], now, job_id))
            self._event(cur, job_id, "failed", attempts, error)
            return "retry"

    # ── 租约过期回收（崩溃 worker 的任务重抢） ──
    def sweep_expired(self, now=None):
        now = now if now is not None else time.time()
        ph = _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""UPDATE media_jobs
                SET state='queued', lease_owner=NULL, lease_expires=NULL,
                    next_run_at={ph}, updated_at={ph}
                WHERE state='running' AND lease_expires IS NOT NULL
                  AND lease_expires<{ph}""", (now, now, now))
            return cur.rowcount

    # ── 统计（面板/告警） ──
    def stats(self):
        with txn(self.led) as cur:
            cur.execute("""SELECT state, COUNT(*) FROM media_jobs GROUP BY state""")
            return {s: n for s, n in cur.fetchall()}

    def dead_letters(self, limit=50):
        ph = _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""SELECT job_id,project_id,stage,skill,error,attempts
                FROM media_jobs WHERE state='dead'
                ORDER BY updated_at DESC LIMIT {int(limit)}""")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def _event(self, cur, job_id, event, attempt_no=0, detail=None):
        """状态转换事件：与转换同一事务写入（失败率与审计的事实源）"""
        ph = _ph(self.led)
        cur.execute(f"""INSERT INTO media_job_events
            (event_id,job_id,attempt_no,event,ts,detail)
            VALUES({ph},{ph},{ph},{ph},{ph},{ph})""",
            (uuid.uuid4().hex[:16], job_id, int(attempt_no), event,
             time.time(), None if detail is None else str(detail)[:500]))

    @staticmethod
    def _row(cur, row):
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        try:
            d["params"] = json.loads(d.get("params") or "{}")
        except Exception as e:
            _swallow(__file__, e)
        return d
