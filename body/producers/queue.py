# body/producers/queue.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径: depth/显存/死信 = gauge 取最新; 等待时长按【窗口内进入终态】的任务算
#       (不能被还在排队的混进完成样本); 失败率分母 0 → null, 且 event_count=0
import os
from body.histogram import add, merge, quantile, ALGO
from body.snapshots import db_now, window, evidence, source, publish_snapshot

PERIOD = int(os.getenv("QUEUE_SAMPLE_PERIOD", 30))
WIN_MIN = int(os.getenv("QUEUE_WINDOW_MINUTES", 30))
MAX_SAMPLES = int(os.getenv("QUEUE_WAIT_SAMPLES_MAX", 5000))

SQL_DEPTH = """SELECT COUNT(*) AS depth, MIN(enqueued_at) AS oldest
               FROM job_queue WHERE state='queued'"""
# ★每个 job 只取最后一次终态事件（重试不重复计）
SQL_TERMINAL = """
SELECT state, COUNT(*) AS n FROM (
  SELECT e.job_id, e.state FROM media_job_events e
   WHERE e.created_at > ? AND e.created_at <= ?
     AND NOT EXISTS (SELECT 1 FROM media_job_events e2
                      WHERE e2.job_id = e.job_id AND e2.created_at > e.created_at)
) g GROUP BY state"""
SQL_WAITS = """SELECT e.job_id, e.terminal_at, j.enqueued_at
               FROM media_job_events e JOIN job_queue j ON j.job_id = e.job_id
               WHERE e.terminal_at > ? AND e.terminal_at <= ? LIMIT ?"""
SQL_DEAD  = "SELECT COUNT(*) AS n FROM dead_letter_jobs"
SQL_VRAM  = "SELECT used_mb, total_mb, at FROM gpu_vram_samples ORDER BY at DESC LIMIT 1"


async def collect(ledger, *, state, now_fn=None):
    end = await db_now(ledger)
    start, _ = window(end, WIN_MIN)
    w = (start, end)

    dq = await ledger.fetch_one(SQL_DEPTH)
    terms = {r["state"]: r["n"] for r in await ledger.fetch_all(SQL_TERMINAL, w)}
    failed, completed = terms.get("failed", 0), terms.get("completed", 0)
    event_count = failed + completed
    # ★分母 0 → null；failed=0 且事件>0 才是真正的 0%
    failure_rate = (round(failed / event_count, 5) if event_count else None)

    waits = await ledger.fetch_all(SQL_WAITS, (*w, MAX_SAMPLES))
    hist: dict = {}
    for r in waits:
        ms = _diff_ms(r["enqueued_at"], r["terminal_at"])
        if ms is not None and ms >= 0:
            add(hist, ms)
    capped = len(waits) >= MAX_SAMPLES
    hist = merge(hist)                       # 多 worker 直方图合并，绝不平均各自 p50

    dead = (await ledger.fetch_one(SQL_DEAD))["n"]
    vr = await ledger.fetch_one(SQL_VRAM)
    vram = ({"used_mb": vr["used_mb"], "total_mb": vr["total_mb"],
             "age_s": int(_age_sec(vr["at"], end))} if vr else
            {"used_mb": None, "total_mb": None, "age_s": None})

    payload = {"depth": dq["depth"],
               "oldest_queued_age_s": (int(_age_sec(dq["oldest"], end)) if dq["oldest"] else None),
               "wait_p50_s": _ms_to_s(quantile(hist, 0.50)),
               "wait_p95_s": _ms_to_s(quantile(hist, 0.95)),
               "wait_samples": sum(hist.values()), "wait_capped": capped,
               "failed": failed, "completed": completed, "event_count": event_count,
               "failure_rate": failure_rate, "dead": dead, "vram": vram,
               "window_minutes": WIN_MIN}
    ev = evidence(window=w, query_id="queue.terminal.v1",
                  params={"states": ["failed", "completed"], "window_minutes": WIN_MIN},
                  algorithm=ALGO, result_digest=_digest(payload),
                  sources=[source("media_job_events", time_column="created_at", w=w,
                                  row_count=event_count),
                           {"table": "job_queue", "kind": "gauge",
                            "filter": "state='queued'", "row_count": dq["depth"]},
                           {"table": "dead_letter_jobs", "kind": "gauge", "row_count": dead},
                           {"table": "gpu_vram_samples", "kind": "gauge",
                            "available": vr is not None},
                           {"table": "wait_histogram", "algorithm": ALGO,
                            "buckets": hist, "capped": capped}])
    return await publish_snapshot(ledger, "queue", payload=payload, evidence=ev,
                                  sample_period_s=PERIOD, producer="producers.queue")
