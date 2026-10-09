# media/metrics.py —— 生成队列指标：深度/等待/失败率/显存（0 ≠ 无数据）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 诚实纪律:
#   - 表可读但无任务 → 0 + coverage=observed；表不可读 → coverage=unavailable
#   - 退避任务(next_run_at>now) ≠ 正在等 GPU → 分开报
#   - 失败率分母 = 窗口内 failed+completed 尝试；无完成尝试 → null/no_data
#   - 显存：调度器静态预留 与 真实 GPU 读数 两个数并列，绝不合并成一个占用率
#   - SQL 全部为字面量（双方言各一份），数据值一律参数绑定
import os
import subprocess
import time

from senses.sqldialect import is_pg, txn
from core.swallow import swallow as _swallow

WIN = int(os.environ.get("MEDIA_FAIL_WINDOW_SEC", "3600"))


def queue_stats(led, window_sec=WIN, now=None):
    """面板/告警的队列指标源。任何查询失败 → coverage=unavailable（不伪装 0）。"""
    now = now if now is not None else time.time()
    try:
        if is_pg(led):
            with txn(led) as cur:
                cur.execute("SELECT state, COUNT(*) FROM media_jobs GROUP BY state")
                counts = {s: n for s, n in cur.fetchall()}
                cur.execute("""SELECT
                    MIN(CASE WHEN next_run_at<=%s THEN created_at END),
                    SUM(CASE WHEN next_run_at>%s THEN 1 ELSE 0 END),
                    MIN(CASE WHEN next_run_at>%s THEN next_run_at END)
                  FROM media_jobs WHERE state='queued'""",
                    (now, now, now))
                oldest_run, backing_off, next_retry = cur.fetchone()
                cur.execute("""SELECT
                    SUM(CASE WHEN event='failed' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN event='completed' THEN 1 ELSE 0 END)
                  FROM media_job_events WHERE ts>=%s""", (now - window_sec,))
                f, ok = cur.fetchone()
        else:
            with txn(led) as cur:
                cur.execute("SELECT state, COUNT(*) FROM media_jobs GROUP BY state")
                counts = {s: n for s, n in cur.fetchall()}
                cur.execute("""SELECT
                    MIN(CASE WHEN next_run_at<=? THEN created_at END),
                    SUM(CASE WHEN next_run_at>? THEN 1 ELSE 0 END),
                    MIN(CASE WHEN next_run_at>? THEN next_run_at END)
                  FROM media_jobs WHERE state='queued'""", (now, now, now))
                oldest_run, backing_off, next_retry = cur.fetchone()
                cur.execute("""SELECT
                    SUM(CASE WHEN event='failed' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN event='completed' THEN 1 ELSE 0 END)
                  FROM media_job_events WHERE ts>=?""", (now - window_sec,))
                f, ok = cur.fetchone()
        f, ok = int(f or 0), int(ok or 0)
        total = f + ok
        oldest = float(oldest_run) if oldest_run is not None else None
        return {
            "depth": {"queued": int(counts.get("queued", 0)),
                      "running": int(counts.get("running", 0)),
                      "dead": int(counts.get("dead", 0))},
            "wait": {
                "oldest_runnable_age": round(now - oldest, 1) if oldest else 0,
                "backoff_count": int(backing_off or 0),
                "next_retry_in": (max(0.0, round(float(next_retry) - now, 1))
                                  if next_retry is not None else None)},
            "failure_rate": (round(f / total, 4) if total else None),
            "failure_rate_kind": "observed" if total else "no_data",
            "failure_attempts": total,
            "failed_count": f,          # 原始计数（面板展示用）
            "success_count": ok,
            "window_sec": window_sec,
            "coverage": "observed",
        }
    except Exception as e:
        return {"coverage": "unavailable", "error": type(e).__name__ + ": " + str(e)}


def read_vram():
    """真实显存：pynvml → nvidia-smi；都拿不到 → (None, 'unavailable')。"""
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        return {"used_mb": int(m.used) // 1048576,
                "total_mb": int(m.total) // 1048576}, "nvml"
    except Exception as e:
        _swallow(__file__, e)

    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total",
             "--format=csv,noheader,nounits"], text=True, timeout=5)
        u, t = [int(x.strip()) for x in out.strip().splitlines()[0].split(",")]
        return {"used_mb": u, "total_mb": t}, "nvidia-smi"
    except Exception:
        return None, "unavailable"


def vram_snapshot(budget=None):
    """静态预留（进程内预算，不是真实 GPU 读数）"""
    if budget is None:
        return None
    try:
        return budget.snapshot()
    except Exception:
        return None
