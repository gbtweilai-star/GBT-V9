# panel/media_api.py —— 采样分桶（SQLite/PG 双后端）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 缺失桶 → coverage=no_data 且值 null(绝不补零);
#       "源不可用" 与 "无样本" 分开; 分桶只查时间窗 + 桶数上限
import math, time, json
from fastapi import APIRouter, Query
from senses.sqldialect import txn
from core.swallow import swallow as _swallow

router = APIRouter()


def _env(data=None, error=None, coverage=None, updated=None, **kw):
    """统一信封：{data, updated_at, stale, error[, coverage]}；查询失败 != 空数据"""
    ts = updated or time.time()
    out = {"data": data, "updated_at": ts, "stale": bool(error), "error": error}
    if coverage is not None:
        out["coverage"] = coverage
    out.update(kw)
    return out


MAX_BUCKETS = 500
MAX_RANGE_SEC = 366 * 86400
FIELDS = ("queued", "running", "dead", "oldest_wait", "backoff_count",
          "next_retry_in", "failure_rate", "vram_reserved_mb", "vram_real_mb")
SRC_OF = {"vram_reserved_mb": "vram_reserved", "vram_real_mb": "vram_real"}


def _auto_bucket(span):
    if span <= 48 * 3600:  return 300
    if span <= 90 * 86400: return 3600
    return 86400


def _choose_bucket(arg, start, end):
    span = end - start
    if arg == "auto":
        b = _auto_bucket(span)
    else:
        try:
            b = int(arg)
        except (TypeError, ValueError):
            raise ValueError("bucket 必须是正整数秒或 auto")
        if b <= 0:
            raise ValueError("bucket 必须大于 0")
    first, last = math.floor(start / b) * b, math.ceil(end / b) * b
    while (last - first) // b > MAX_BUCKETS:      # 桶数上限：超了自动放大
        b *= 2
        first, last = math.floor(start / b) * b, math.ceil(end / b) * b
    return b, first, last


def _bexpr(dialect, b):
    # epoch 为正；b 已 int 化
    return (f"CAST(ts/{b} AS INTEGER)*{b}" if dialect == "sqlite"
            else f"floor(ts/{b}.0)*{b}")


def _metric(coverage, avg, mn, mx, count, note=None):
    if coverage in ("no_data", "unavailable"):
        avg = mn = mx = None
    d = {"avg": avg, "min": mn, "max": mx, "count": count, "coverage": coverage}
    if note:
        d["note"] = note
    return d


@router.get("/media/samples")
def media_samples(from_ts: float = Query(...),
                  to_ts: float | None = Query(None),
                  bucket: str = Query("300")):
    end = float(to_ts if to_ts is not None else time.time())
    start = float(from_ts)
    if not (math.isfinite(start) and math.isfinite(end)) or end <= start:
        return _env(error="invalid_window", coverage="unavailable")
    if end - start > MAX_RANGE_SEC:
        return _env(error="range_too_large", coverage="unavailable")
    try:
        b, first, last = _choose_bucket(bucket, start, end)
    except ValueError as e:
        return _env(error=str(e), coverage="unavailable")

    ph = "?" if router.ledger.dialect == "sqlite" else "%s"
    expr = _bexpr(router.ledger.dialect, b)
    sel = [f"{expr} AS bucket_start", "COUNT(*) AS n"]
    for f in FIELDS:
        sel += [f"COUNT({f}) AS {f}__n", f"AVG({f}) AS {f}__avg",
                f"MIN({f}) AS {f}__min", f"MAX({f}) AS {f}__max"]
    sel += [
        f"SUM(CASE WHEN COALESCE(sources,'') LIKE {ph} THEN 1 ELSE 0 END) AS q_bad",
        f"SUM(CASE WHEN COALESCE(sources,'') LIKE {ph} THEN 1 ELSE 0 END) AS vres_bad",
        f"SUM(CASE WHEN COALESCE(sources,'') LIKE {ph} THEN 1 ELSE 0 END) AS vreal_bad",
    ]
    sql = (f"SELECT {', '.join(sel)} FROM media_monitor_samples "
           f"WHERE ts>={ph} AND ts<{ph} GROUP BY {expr} ORDER BY bucket_start")
    args = [start, end, '%"queue":"unavailable"%',
            '%"vram_reserved":"unavailable"%', '%"vram_real":"unavailable"%']
    try:
        with txn(router.ledger) as cur:
            cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            rows = {int(float(r[0])): dict(zip(cols, r)) for r in cur.fetchall()}
    except Exception as e:
        return _env(error=str(e), coverage="unavailable", samples=[])

    buckets = []
    for ts in range(first, last, b):
        r = rows.get(ts)
        n = int(r["n"]) if r else 0
        metrics = {}
        for f in FIELDS:
            if r is None:
                metrics[f] = _metric("no_data", None, None, None, 0)
                continue
            cnt = int(r.get(f"{f}__n") or 0)
            bad_key = {"vram_reserved_mb": "vres_bad", "vram_real_mb": "vreal_bad"}.get(f, "q_bad")
            bad = int(r.get(bad_key) or 0)
            if bad and bad >= n:                 coverage = "unavailable"
            elif cnt == 0:                        coverage = "no_data"
            elif cnt < n:                         coverage = "partial"
            else:                                 coverage = "observed"
            note = None
            if f == "failure_rate":
                note = "采样率简单平均；精确失败率见 media_job_events"
            if f == "next_retry_in" and cnt == 0 and n:
                note = "当前没有已安排的重试"
            metrics[f] = _metric(coverage, r.get(f"{f}__avg"), r.get(f"{f}__min"),
                                 r.get(f"{f}__max"), cnt, note)
        states = {m["coverage"] for m in metrics.values()}
        oval = ("no_data" if n == 0 else
                "unavailable" if states == {"unavailable"} else
                "partial" if states & {"partial", "no_data", "unavailable"} else
                "observed")
        buckets.append({"start": ts, "end": ts + b, "sample_count": n,
                        "coverage": oval, "metrics": metrics})
    return _env({"bucket_seconds": b, "buckets": buckets, "coverage": "observed"})


# ═══ 生成队列监控：深度/等待/失败率 · 显存（预算 vs 真实并列）· 死信 ═══
def _led():
    led = getattr(router, "ledger", None)
    return led


def _coverage_unavailable(err):
    return _env(error=str(err), coverage="unavailable")


@router.get("/media/queue/stats")
def media_queue_stats():
    """0 ≠ 无数据：表可读无任务 → 0+observed；表不可读 → unavailable。"""
    from media.metrics import queue_stats
    led = _led()
    if led is None:
        return _env(error="ledger unavailable", coverage="unavailable")
    try:
        return _env(queue_stats(led))
    except Exception as e:
        return _coverage_unavailable(e)


@router.get("/media/vram")
def media_vram():
    """静态预留（进程内预算）与真实 GPU 读数并列，绝不合并成一个占用率。"""
    from media.metrics import read_vram
    try:
        snap = getattr(router, "budget", None)
        reserved = snap.snapshot() if snap else None
        real, src = read_vram()
        return _env({
            "reserved": ({"total_mb": reserved["total_mb"],
                          "used_mb": reserved["used_mb"],
                          "pct": (round(reserved["used_mb"] / reserved["total_mb"], 4)
                                  if reserved.get("total_mb") else None),
                          "exclusive": reserved.get("exclusive", False),
                          "kind": "static_budget"}
                         if reserved else None),
            "real": ({"total_mb": real["total_mb"], "used_mb": real["used_mb"],
                      "pct": round(real["used_mb"] / real["total_mb"], 4),
                      "kind": "hardware_reading"}
                     if real else None),
            "real_source": src,
            "coverage": "observed",
        })
    except Exception as e:
        return _coverage_unavailable(e)


@router.get("/media/dead")
def media_dead(limit: int = Query(50, ge=1, le=500)):
    """死信：计数 + 分页列表（error 由前端转义展示）"""
    q = getattr(router, "queue", None)
    led = _led()
    if q is None or led is None:
        return _env(error="queue unavailable", coverage="unavailable")
    try:
        st = q.stats()
        return _env({"total": int(st.get("dead", 0)),
                     "jobs": q.dead_letters(limit=limit),
                     "coverage": "observed"})
    except Exception as e:
        return _coverage_unavailable(e)


@router.post("/media/dead/{job_id}/requeue")
def media_dead_requeue(job_id: str, request_id: str = Query(...)):
    """死信重入队：不改旧死信，新建 job 记 requeued_from；request_id 幂等"""
    from media.requeue import requeue_dead, RequeueError
    led = _led()
    if led is None:
        return _env(error="ledger unavailable", coverage="unavailable")
    try:
        return _env(requeue_dead(led, job_id, request_id))
    except RequeueError as e:
        return _env(error=str(e))
    except Exception as e:
        return _coverage_unavailable(e)


# ═══ 终态事件（failed/completed）+ 游标分页（双后端兼容）═══

def _cursor_where(dialect, ts, event_id, event, has_event, order="desc"):
    """游标条件：时间戳相同用 event_id 决胜，保证不重不漏。
    游标方向必须与排序方向一致（desc 用 <，asc 用 >），否则会跳行。"""
    ph = "%s" if dialect != "sqlite" else "?"
    parts, args = [], []
    if event:
        parts.append("event=" + ph)
        args.append(event)
    if ts is not None:
        cmp_ts, cmp_id = ("<", "<") if order == "desc" else (">", ">")
        parts.append("(ts " + cmp_ts + " " + ph + " OR (ts = " + ph
                     + " AND event_id " + cmp_id + " " + ph + "))")
        args.extend([ts, ts, event_id or ""])
    return (" WHERE " + " AND ".join(parts)) if parts else "", args


@router.get("/media/terminal-events")
def media_terminal_events(limit: int = Query(50, ge=1, le=200),
                          event: str | None = Query(None,
                                                    pattern="^(failed|completed)$"),
                          from_ts: float | None = Query(None),
                          to_ts: float | None = Query(None),
                          cursor_ts: float | None = Query(None),
                          cursor_id: str | None = Query(None),
                          order: str = Query("desc", pattern="^(asc|desc)$")):
    """终态事件流：默认倒序；游标=(cursor_ts,cursor_id) 必须成对，防篡改跳行。"""
    led = _led()
    if led is None:
        return _env(error="ledger unavailable", coverage="unavailable")
    dialect = led.dialect
    if (cursor_ts is None) != (cursor_id is None):
        return _env(error="cursor_ts/cursor_id 必须成对", coverage="unavailable")
    try:
        ph = "%s" if dialect != "sqlite" else "?"
        where, args = _cursor_where(dialect, cursor_ts, cursor_id, event,
                                    bool(event), order)
        if from_ts is not None:
            extra = "ts >= " + ph
            where = (where + " AND " + extra) if where else (" WHERE " + extra)
            args.append(from_ts)
        if to_ts is not None:
            extra = "ts <= " + ph
            where = (where + " AND " + extra) if where else (" WHERE " + extra)
            args.append(to_ts)
        with txn(led) as cur:
            cur.execute("SELECT COUNT(*) FROM media_job_events" + where, tuple(args))
            total = cur.fetchone()[0]
            cur.execute("""SELECT event_id,job_id,attempt_no,event,ts,detail FROM
                media_job_events""" + where +
                " ORDER BY ts " + order.upper() + ", event_id " + order.upper()
                + " LIMIT " + ph,
                tuple(args) + (int(limit),))
            rows = cur.fetchall()
        items = [{"event_id": r[0], "job_id": r[1], "attempt_no": r[2],
                  "event": r[3], "ts": r[4], "detail": r[5]} for r in rows]
        nxt = ({"ts": items[-1]["ts"], "id": items[-1]["event_id"],
                "order": order} if len(items) == int(limit) else None)
        return _env({"items": items, "total": int(total), "next_cursor": nxt,
                     "event": event, "order": order, "coverage": "observed"})
    except Exception as e:
        return _env(error=str(e), coverage="unavailable")


@router.get("/media/queue/{job_id}")
def media_queue_job(job_id: str, events_limit: int = Query(50, ge=1, le=200)):
    """单个任务的完整下钻：任务行 + checkpoint + 它的全部事件（含终态）"""
    q = getattr(router, "queue", None)
    led = _led()
    if q is None or led is None:
        return _env(error="queue unavailable", coverage="unavailable")
    try:
        ph = "%s" if led.dialect != "sqlite" else "?"
        with txn(led) as cur:
            cur.execute("""SELECT job_id,project_id,stage,skill,params_hash,params,
                    priority,state,attempts,max_attempts,lease_owner,lease_expires,
                    checkpoint,artifact_sha,error,next_run_at,created_at,updated_at
                FROM media_jobs WHERE job_id=""" + ph, (job_id,))
            row = cur.fetchone()
            if not row:
                return _env(error="job 不存在", coverage="observed")
            cols = [d[0] for d in cur.description]
            job = dict(zip(cols, row))
            cur.execute("""SELECT event_id,attempt_no,event,ts,detail
                FROM media_job_events WHERE job_id=""" + ph +
                " ORDER BY ts ASC, event_id ASC LIMIT " + ph,
                (job_id, int(events_limit)))
            ev = [{"event_id": r[0], "attempt_no": r[1], "event": r[2],
                   "ts": r[3], "detail": r[4]} for r in cur.fetchall()]
        # checkpoint 是 JSON 文本 → 解析失败如实返回原文
        try:
            import json as _json
            job["checkpoint"] = _json.loads(job["checkpoint"] or "null")
        except Exception as e:
            _swallow(__file__, e)

        return _env({"job": job, "events": ev, "coverage": "observed"})
    except Exception as e:
        return _env(error=str(e), coverage="unavailable")
