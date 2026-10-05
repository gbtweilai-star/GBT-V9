# media/requeue.py —— 死信重入队 + transactional outbox
from senses.sqldialect import txn
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 建新任务 + 记 requeued 事件 + 写 outbox —— 三者同一账本事务;
#       旧死信不动; 同 request_id 幂等; 复用 ID 指向不同死信 → 拒绝

OUTBOX_DDL = """
CREATE TABLE IF NOT EXISTS media_outbox(
  outbox_id       TEXT PRIMARY KEY,
  topic           TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  payload         TEXT NOT NULL,
  state           TEXT NOT NULL DEFAULT 'pending',
  attempts        INTEGER NOT NULL DEFAULT 0,
  lease_owner     TEXT,
  lease_expires   REAL,
  next_run_at     REAL NOT NULL DEFAULT 0,
  created_at      REAL NOT NULL,
  updated_at      REAL NOT NULL);
CREATE INDEX IF NOT EXISTS idx_outbox_claim ON media_outbox(state, next_run_at);

CREATE TABLE IF NOT EXISTS media_requeue_requests(
  request_id TEXT PRIMARY KEY,
  job_id     TEXT NOT NULL,
  new_job_id TEXT NOT NULL,
  created_at REAL NOT NULL);
"""


def ensure(led):
    with txn(led) as cur:
        cur.executescript(OUTBOX_DDL) if led.dialect == "sqlite" \
            else cur.execute(OUTBOX_DDL)


class RequeueError(Exception):
    pass


def requeue_dead(led, job_id, request_id, *, priority=None):
    """幂等重入队。返回 {new_job_id, deduped:bool}"""
    import uuid, json, time
    ph = "?" if led.dialect == "sqlite" else "%s"
    now = time.time()
    with txn(led) as cur:
        # 幂等：request_id 已存在
        cur.execute(f"SELECT job_id,new_job_id FROM media_requeue_requests "
                    f"WHERE request_id={ph}", (request_id,))
        r = cur.fetchone()
        if r:
            if r[0] != job_id:
                raise RequeueError("request_id 已指向另一个死信，拒绝复用")
            return {"new_job_id": r[1], "deduped": True}

        # 取旧死信
        cur.execute(f"SELECT * FROM media_jobs WHERE job_id={ph} AND state='dead'",
                    (job_id,))
        row = cur.fetchone()
        if not row:
            raise RequeueError("目标不是可重入队的死信")
        cols = [d[0] for d in cur.description]
        old = dict(zip(cols, row))

        new_id = f"{old['stage']}-{uuid.uuid4().hex[:12]}"
        cur.execute(f"""INSERT INTO media_jobs(
            job_id,project_id,stage,skill,params_hash,params,priority,
            state,attempts,max_attempts,next_run_at,created_at,updated_at)
            VALUES({ph},{ph},{ph},{ph},{ph},{ph},{ph},'queued',0,{ph},{ph},{ph},{ph})""",
            (new_id, old["project_id"], old["stage"], old["skill"],
             uuid.uuid4().hex,        # 新去重键，避免撞旧死信
             old["params"], priority if priority is not None else old["priority"],
             old["max_attempts"], now, now, now))

        # 事件 + outbox + request 全在同一事务
        cur.execute(f"""INSERT INTO media_job_events(
            event_id,job_id,attempt_no,event,ts,detail)
            VALUES({ph},{ph},0,'requeued',{ph},{ph})""",
            (uuid.uuid4().hex, new_id, now, json.dumps({"requeued_from": job_id})))
        cur.execute(f"""INSERT INTO media_outbox(
            outbox_id,topic,idempotency_key,payload,state,next_run_at,created_at,updated_at)
            VALUES({ph},'brain.media.dead_requeued',{ph},{ph},'pending',{ph},{ph},{ph})""",
            (uuid.uuid4().hex, f"requeue:{request_id}",
             json.dumps({"job_id": job_id, "new_job_id": new_id}),
             now, now, now))
        cur.execute(f"""INSERT INTO media_requeue_requests(
            request_id,job_id,new_job_id,created_at) VALUES({ph},{ph},{ph},{ph})""",
            (request_id, job_id, new_id, now))
        return {"new_job_id": new_id, "deduped": False}
