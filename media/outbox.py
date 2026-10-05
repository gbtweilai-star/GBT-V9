# media/outbox.py —— 至少一次投递：租约领取 + 退避 + 过期回收
from senses.sqldialect import txn
class OutboxSender:
    def __init__(self, ledger, deliver, *, worker_id="outbox", lease_sec=60,
                 backoff=5.0, cap=300.0):
        self.led, self.deliver = ledger, deliver
        self.worker_id = worker_id
        self.lease_sec, self.backoff, self.cap = lease_sec, backoff, cap

    def drain_once(self):
        import time, uuid, json
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        now = time.time(); owner = f"{self.worker_id}:{uuid.uuid4().hex[:8]}"
        with txn(self.led) as cur:
            cur.execute(f"""UPDATE media_outbox SET
                    state='sending', lease_owner={ph}, lease_expires={ph},
                    attempts=attempts+1, updated_at={ph}
                WHERE outbox_id=(
                  SELECT outbox_id FROM media_outbox
                  WHERE state='pending' AND next_run_at<={ph}
                  ORDER BY created_at LIMIT 1)
                RETURNING *""", (owner, now + self.lease_sec, now, now))
            row = cur.fetchone()
            if not row:
                return False
            cols = [d[0] for d in cur.description]
            job = dict(zip(cols, row))
        try:
            self.deliver(job["topic"], json.loads(job["payload"]),
                         job["idempotency_key"])       # 大脑按 key 去重
            with txn(self.led) as cur:
                cur.execute(f"""UPDATE media_outbox SET state='delivered',
                        lease_owner=NULL, lease_expires=NULL, updated_at={ph}
                    WHERE outbox_id={ph} AND lease_owner={ph}""",
                    (time.time(), job["outbox_id"], owner))
        except Exception as e:
            delay = min(self.backoff * (2 ** (job["attempts"] - 1)), self.cap)
            with txn(self.led) as cur:
                cur.execute(f"""UPDATE media_outbox SET state='pending',
                        lease_owner=NULL, lease_expires=NULL,
                        next_run_at={ph}, updated_at={ph}
                    WHERE outbox_id={ph}""",
                    (time.time() + delay, time.time(), job["outbox_id"]))
        return True
