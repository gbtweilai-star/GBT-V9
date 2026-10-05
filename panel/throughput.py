# panel/throughput.py —— 按触手的产能与负载对比（只用可观测真实字段）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 各明细表分开聚合再合并, 不 JOIN 明细表(会放大计数)
#   - 采集帧数(心跳计数) 与 已归档帧数(帧段) 分开, 差值标"需核对"
#   - 负载只用可观测代理量; 无观测就不给, 不编"负载分"
#   - 忙不忙看 阻塞率/失败率, 不看扫出的漏洞数
import os, time
from panel.trends import source_status, _cols, _pick, _q
from senses.sessions import observed_uptime
from senses.sqldialect import txn

QUAD_SCAN_HOUR = float(os.environ.get("QUAD_SCAN_HOUR", 0))    # 业务目标阈值, 默认不画象限
QUAD_FRAME_HOUR = float(os.environ.get("QUAD_FRAME_HOUR", 0))


def _ph(led):
    return "?" if led.dialect == "sqlite" else "%s"


def _capture_counts(led, start, end):
    """心跳计数器增量：需要窗口开始前的最近样本作为基线"""
    if not (source_status(led).get("devour", {}).get("available")):
        return None
    ph = _ph(led)
    try:
        with txn(led) as cur:
            cur.execute(f"""SELECT s.tentacle_id,
                (SELECT h.frames_captured FROM capture_heartbeats h
                 WHERE h.session_id=s.session_id AND h.ts<{ph}
                 ORDER BY h.ts DESC LIMIT 1) base,
                (SELECT h.frames_captured FROM capture_heartbeats h
                 WHERE h.session_id=s.session_id AND h.ts<{ph}
                 ORDER BY h.ts DESC LIMIT 1) fin
                FROM capture_sessions s
                WHERE s.started_at<{ph} AND COALESCE(s.ended_at, 9e18)>{ph}""",
                (start, end, end, start))
            rows = cur.fetchall()
    except Exception:
        return None
    agg = {}
    for tid, base, fin in rows:
        if fin is None:
            continue
        b = base if base is not None else 0
        e = agg.setdefault(tid, {"captured": 0, "partial": base is None})
        e["captured"] += max(0, int(fin) - int(b))
    return agg


def _archived(led, start, end):
    st = source_status(led)
    t = st.get("devour", {}).get("table") if st.get("devour", {}).get("available") else None
    if not t:
        return None
    cols = _cols(led, t)
    tcol = _pick(cols, ("ts", "created_at")) or "ts"
    if "frame_count" in cols:
        fexpr = "SUM(frame_count)"
    elif "frames" in cols:
        fexpr = "SUM(frames)"
    else:
        s, e = _pick(cols, ("start_frame",)), _pick(cols, ("end_frame",))
        fexpr = f"SUM({e} - {s} + 1)" if (s and e) else "0"
    try:
        rows = _q(led, f"""SELECT tentacle_id, {fexpr} frames, COUNT(*) segs
                           FROM {t} WHERE {tcol}>={{}} AND {tcol}<{{}}
                           GROUP BY tentacle_id""", (start, end))
        return {r["tentacle_id"]: r for r in rows}
    except Exception:
        return None


def _scan(led, start, end):
    try:
        rows = _q(led, """SELECT scanner AS tentacle_id,
                   COUNT(*) events,
                   SUM(CASE WHEN status='vuln' THEN 1 ELSE 0 END) vuln,
                   SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) blocked
                   FROM ledger WHERE ts>={} AND ts<{} GROUP BY scanner""",
                  (start, end))
        return {r["tentacle_id"]: r for r in rows}
    except Exception:
        return None


def _cross(led, start, end):
    try:
        rows = _q(led, """SELECT reviewer AS tentacle_id, COUNT(*) reviews
                          FROM cross_tasks
                          WHERE updated_at>={} AND updated_at<{}
                          GROUP BY reviewer""", (start, end))
        return {r["tentacle_id"]: r for r in rows}
    except Exception:
        return None


def _skills(led, start, end):
    try:
        rows = _q(led, """SELECT tentacle, COUNT(*) calls, AVG(ms) avg_ms,
                          SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) failed
                          FROM skill_calls WHERE ts>={} AND ts<{}
                          GROUP BY tentacle""", (start, end))
        return {r["tentacle"]: r for r in rows}
    except Exception:
        return None


def tentacle_throughput(led, hours=24):
    end = time.time() + 1        # 浮点右边界：刚写入的事件不能被截掉
    start = end - 1 - int(hours * 3600)
    cap = _capture_counts(led, start, end)
    arc = _archived(led, start, end)
    scn = _scan(led, start, end)
    crs = _cross(led, start, end)
    skl = _skills(led, start, end)

    tids = sorted(set(cap or {}) | set(arc or {}) | set(scn or {}) |
                  set(crs or {}) | set(skl or {}))
    out = []
    for tid in tids:
        c = (cap or {}).get(tid, {})
        a = (arc or {}).get(tid, {})
        s = (scn or {}).get(tid, {})
        x = (crs or {}).get(tid, {})
        k = (skl or {}).get(tid, {})

        captured = int(c.get("captured") or 0) if cap is not None else None
        archived = int(a.get("frames") or 0) if arc is not None else None
        delta = ((captured - archived)
                 if (captured is not None and archived is not None) else None)

        events = int(s.get("events") or 0) if scn is not None else None
        vuln = int(s.get("vuln") or 0) if scn is not None else None
        blocked = int(s.get("blocked") or 0) if scn is not None else None
        reviews = int(x.get("reviews") or 0) if crs is not None else None
        calls = int(k.get("calls") or 0) if skl is not None else None
        failed = int(k.get("failed") or 0) if skl is not None else None
        avg_ms = (round(k.get("avg_ms") or 0) if skl is not None else None)

        # 速率：只用有证据的活跃时长
        upt = None
        try:
            upt = observed_uptime(led, tid, start, end)
        except Exception:
            upt = None
        upt_h = (upt / 3600) if (upt and upt > 0) else None
        frames_per_hour = (round(archived / upt_h, 1)
                           if (archived is not None and upt_h) else None)
        # 扫描速率：用扫描会话心跳算活跃时长（样本不足时诚实给 null+原因）
        scan_note = None
        try:
            from senses.scan_sessions import scan_rate
            scan_per_hour, scan_note = scan_rate(
                led, tid, start, end, linked_events=events or 0)
        except Exception:
            scan_per_hour, scan_note = None, None

        warnings = []
        if delta is not None and delta != 0:
            warnings.append(f"采集计数与已归档帧数不一致（差 {delta}，需核对）")
        if cap is not None and any(v.get("partial") for kk, v in (cap or {}).items()
                                   if kk == tid):
            warnings.append("基线样本缺失，采集帧数为部分统计")

        # 派生质量指标（有意义的那几个）
        blocked_rate = (round(blocked / events, 4) if (events and blocked is not None)
                        else None)
        fail_rate = (round(failed / calls, 4) if (calls and failed is not None) else None)
        coverage = ("observed" if upt_h
                    else ("no_data" if cap is not None else "unavailable"))

        out.append({
            "id": tid,
            "captured_frames": captured, "archived_frames": archived,
            "frame_delta": delta, "segments": (int(a.get("segs") or 0)
                                               if arc is not None else None),
            "scan_events": events, "vuln_events": vuln, "blocked_events": blocked,
            "cross_reviews": reviews,
            "skill_calls": calls, "avg_ms": avg_ms, "failed_calls": failed,
            "uptime_hours": (round(upt_h, 3) if upt_h else None),
            "frames_per_active_hour": frames_per_hour,
            "scan_events_per_hour": scan_per_hour,
            "blocked_rate": blocked_rate, "skill_fail_rate": fail_rate,
            "scan_rate_note": scan_note,
            "active_sessions": None,
            "coverage": coverage, "warnings": warnings,
        })

    # 排序：已归档帧/小时降序（产能）
    out.sort(key=lambda r: -(r["archived_frames"] or 0))
    return {
        "window": {"from": start, "to": end, "hours": hours},
        "sources": {
            "capture": "available" if cap is not None else "unavailable",
            "segments": "available" if arc is not None else "unavailable",
            "scan": "available" if scn is not None else "unavailable",
            "cross": "available" if crs is not None else "unavailable",
            "skills": "available" if skl is not None else "unavailable",
        },
        "quadrant": {"scan_per_hour": QUAD_SCAN_HOUR or None,
                     "frame_per_hour": QUAD_FRAME_HOUR or None},
        "tentacles": out,
    }
