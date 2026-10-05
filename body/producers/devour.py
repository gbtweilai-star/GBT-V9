# body/producers/devour.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径: 帧/gap/补齐 = 窗口增量 counter; R2水位/缓存占用 = gauge 只取最新;
#       三个数字必须自洽: 补齐帧 ⊆ 已登记 gap, 且 recovered <= gap_frames
import json, os
from body.snapshots import db_now, window, evidence, source
from body.snapshots import publish_snapshot

PERIOD = int(os.getenv("DEVOUR_SAMPLE_PERIOD", 30))
WIN_MIN = int(os.getenv("DEVOUR_WINDOW_MINUTES", 60))

# ← 对齐点：换成你实际的表名与列名
SQL_FRAMES = """SELECT COUNT(*) AS n, MIN(id) AS min_id, MAX(id) AS max_id
                FROM devour_frames WHERE captured_at > ? AND captured_at <= ?"""
SQL_GAPS   = """SELECT COUNT(*) AS n, COALESCE(SUM(missing_frames),0) AS missing,
                       MIN(id) AS min_id, MAX(id) AS max_id
                FROM devour_gaps WHERE created_at > ? AND created_at <= ?"""
SQL_RECOVER= """SELECT COALESCE(SUM(frames_recovered),0) AS n, MIN(id) AS min_id,
                       MAX(id) AS max_id
                FROM devour_compensations WHERE created_at > ? AND created_at <= ?
                AND verified = 1"""                     # 只算校验成功的补齐
SQL_WATERMARK = """SELECT pending_bytes, at FROM r2_watermark ORDER BY at DESC LIMIT 1"""
SQL_CACHE     = """SELECT used_bytes, limit_bytes, at FROM cache_stats ORDER BY at DESC LIMIT 1"""


async def collect(ledger, *, state, now_fn=None):
    end = await db_now(ledger)
    start, _ = window(end, WIN_MIN)
    w = (start, end)

    fr = await ledger.fetch_one(SQL_FRAMES, w)
    gp = await ledger.fetch_one(SQL_GAPS, w)
    rc = await ledger.fetch_one(SQL_RECOVER, w)

    frames, gaps = fr["n"], gp["n"]
    gap_frames, recovered = gp["missing"], rc["n"]

    # ★自洽校验：不满足就把派生值置 null，并显式标出不一致
    consistency, unresolved = "ok", None
    if recovered is not None and gap_frames is not None:
        if recovered > gap_frames:
            consistency, unresolved = "violation:recovered_gt_gap_frames", None
        else:
            unresolved = gap_frames - recovered
    if consistency != "ok":
        await ledger.record_blocked("devour_snapshot_inconsistent",
                                    {"recovered": recovered, "gap_frames": gap_frames})

    # gauge：只取最新，不求和
    wm = await ledger.fetch_one(SQL_WATERMARK)
    cs = await ledger.fetch_one(SQL_CACHE)
    archive = {"status": "unknown", "pending_bytes": None, "watermark_age_s": None}
    if wm and wm["at"]:
        age = _age_sec(wm["at"], end)
        pending = wm["pending_bytes"]
        archive = {"status": ("ok" if age <= PERIOD * 2 and (pending or 0) == 0
                              else "lagging"),
                   "pending_bytes": pending, "watermark_age_s": int(age)}
    cache = {"used_bytes": cs["used_bytes"] if cs else None,
             "limit_bytes": cs["limit_bytes"] if cs else None,
             "ratio": (round(cs["used_bytes"] / cs["limit_bytes"], 4)
                       if cs and cs["limit_bytes"] else None)}

    payload = {"frames": frames, "gap_intervals": gaps, "gap_frames": gap_frames,
               "recovered_frames": recovered, "unresolved_gap_frames": unresolved,
               "consistency": consistency, "archive": archive, "cache": cache,
               "window_minutes": WIN_MIN}
    ev = evidence(window=w, query_id="devour.window.v1",
                  params={"states": ["captured", "verified_compensation"]},
                  result_digest=_digest(payload),
                  sources=[source("devour_frames", time_column="captured_at", w=w,
                                  min_id=fr["min_id"], max_id=fr["max_id"],
                                  row_count=frames),
                           source("devour_gaps", time_column="created_at", w=w,
                                  min_id=gp["min_id"], max_id=gp["max_id"], row_count=gaps),
                           source("devour_compensations", time_column="created_at", w=w,
                                  min_id=rc["min_id"], max_id=rc["max_id"], row_count=recovered),
                           {"table": "r2_watermark", "kind": "gauge",
                            "available": wm is not None},
                           {"table": "cache_stats", "kind": "gauge",
                            "available": cs is not None}])
    return await publish_snapshot(ledger, "devour", payload=payload, evidence=ev,
                                  sample_period_s=PERIOD, producer="producers.devour")
