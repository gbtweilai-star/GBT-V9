# panel/trends.py —— 追加：按触手分组的丢帧趋势
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 原则:
#   - 默认只比较"丢帧"一个指标(避免 触手数×指标数 线爆掉)
#   - 每触手每桶独立判覆盖: 有覆盖无 gap → 0; 无覆盖 → null(不伪装零)
#   - "其他"是部分小计, 带 observed_count/total_count, 明确标注
#   - 颜色哈希稳定, 跨刷新不跳
import hashlib

PALETTE = ["#39d0ff", "#9b7bff", "#35d39a", "#ffbd59", "#ff8fab",
           "#5ee7df", "#f7b267", "#7dd3fc", "#c084fc", "#4ade80",
           "#f87171", "#facc15"]

_COLOR_CACHE = {}


def _assign_colors(tids_sorted):
    """按字典序稳定分配颜色，冲突向后探测；同进程内缓存不变"""
    used = set(_COLOR_CACHE.values())
    out = {}
    for tid in tids_sorted:
        if tid in _COLOR_CACHE:
            out[tid] = _COLOR_CACHE[tid]
            continue
        i = int(hashlib.sha1(tid.encode()).hexdigest()[:8], 16) % len(PALETTE)
        for k in range(len(PALETTE)):
            c = PALETTE[(i + k) % len(PALETTE)]
            if c not in used:
                break
        _COLOR_CACHE[tid] = c
        used.add(c)
        out[tid] = c
    return out


def by_tentacle(led, hours=24, bucket=None, top=5):
    end = int(time.time())
    start = end - int(hours * 3600)
    b = bucket or _auto_bucket(hours * 3600)
    st = source_status(led)

    # ── ① 丢帧：按 触手 × 桶 聚合 ──
    gap_tbl = (st.get("gap", {}).get("table")
               if st.get("gap", {}).get("available") else None)
    grows = []
    if gap_tbl:
        cols = _cols(led, gap_tbl)
        mcol = _pick(cols, ("missing", "count")) or "1"
        tcol = _pick(cols, ("ts", "created_at")) or "ts"
        expr = _bucket_expr(led.dialect, tcol, b)
        grows = _q(led, f"""SELECT tentacle_id, {expr} AS b,
                                   SUM({mcol}) missing, COUNT(*) n
                            FROM {gap_tbl}
                            WHERE {tcol}>={{}} AND {tcol}<{{}}
                            GROUP BY tentacle_id, b""", (start, end))

    # ── ② 覆盖证据：帧段表有记录 = 该触手当时在采集 ──
    seg_tbl = (st.get("devour", {}).get("table")
               if st.get("devour", {}).get("available") else None)
    cover, cov_ok = set(), False
    if seg_tbl:
        cols = _cols(led, seg_tbl)
        tcol = _pick(cols, ("ts", "created_at")) or "ts"
        expr = _bucket_expr(led.dialect, tcol, b)
        crows = _q(led, f"""SELECT DISTINCT tentacle_id, {expr} AS b
                            FROM {seg_tbl}
                            WHERE {tcol}>={{}} AND {tcol}<{{}}""", (start, end))
        cover = {(r["tentacle_id"], int(r["b"])) for r in crows}
        cov_ok = True

    gapmap = {(r["tentacle_id"], int(r["b"])): r for r in grows}
    tids = sorted({r["tentacle_id"] for r in grows} | {c[0] for c in cover})
    colors = _assign_colors(tids)

    first = (start // b) * b
    tickers = []
    t0 = first
    while t0 < end:
        tickers.append(t0); t0 += b

    # ── ③ 逐触手逐桶：0 / 值 / null ──
    series = []
    for tid in tids:
        pts = []
        for tk in tickers:
            has_gap = (tid, tk) in gapmap
            # 有覆盖表 → 用覆盖判定；没有 → 只认"有 gap"这个事实
            observed = ((tid, tk) in cover) if cov_ok else has_gap
            if observed:
                pts.append({"start": tk,
                            "missing": int(gapmap[(tid, tk)]["missing"]) if has_gap else 0,
                            "coverage": "observed"})
            else:
                pts.append({"start": tk, "missing": None, "coverage": "no_data"})
        series.append({"tentacle_id": tid, "color": colors[tid], "points": pts,
                       "total_missing": sum(p["missing"] or 0 for p in pts)})

    series.sort(key=lambda s: -s["total_missing"])
    head, tail = series[:top], series[top:]

    # ── ④ "其他"小计（部分覆盖，明确标注）──
    other_pts = []
    for i, tk in enumerate(tickers):
        vals, obs, tot = [], 0, len(tail)
        for s in tail:
            if s["points"][i]["missing"] is not None:
                vals.append(s["points"][i]["missing"]); obs += 1
        other_pts.append({"start": tk,
                          "missing": (sum(vals) if obs else None),
                          "observed_count": obs, "total_count": tot,
                          "coverage": "observed" if obs else "no_data"})
    other = ({"points": other_pts, "partial": True, "members": len(tail)}
             if tail else None)

    # ── ⑤ 排行表：丢帧 + 补偿（repair_attempts JOIN repair_jobs 取触手）──
    rep_tbl = (st.get("repair", {}).get("table")
               if st.get("repair", {}).get("available") else None)
    comp = {}
    if rep_tbl:
        try:
            crows = _q(led, f"""SELECT j.tentacle_id, SUM(a.restored) restored,
                                       SUM(a.resampled) resampled,
                                       SUM(a.permanent) permanent
                                FROM {rep_tbl} a
                                JOIN repair_jobs j ON j.gap_id = a.gap_id
                                WHERE a.ts>={{}} AND a.ts<{{}}
                                GROUP BY j.tentacle_id""", (start, end))
            comp = {r["tentacle_id"]: r for r in crows}
        except Exception:
            comp = {}

    ranking = []
    for s in series:
        tid = s["tentacle_id"]
        c = comp.get(tid, {})
        mis = s["total_missing"]
        rest = int(c.get("restored") or 0)
        covered = sum(1 for p in s["points"] if p["missing"] is not None)
        ranking.append({
            "tentacle_id": tid, "color": s["color"], "total_missing": mis,
            "restored": rest, "resampled": int(c.get("resampled") or 0),
            "permanent": int(c.get("permanent") or 0),
            "recovery_rate": round(rest / mis, 3) if mis else None,
            "buckets_covered": covered, "buckets_total": len(tickers),
        })

    return {
        "bucket_seconds": b, "from": start, "to": end, "hours": hours,
        "coverage_available": cov_ok,     # false → 无法判 0，只显示有 gap 的桶
        "series": head, "other": other,
        "ranking": ranking,
        "summary": {
            "total_missing": sum(s["total_missing"] for s in series),
            "tentacles": len(series),
            "dominant": series[0]["tentacle_id"] if series else None,
            "dominant_share": (round(series[0]["total_missing"] /
                                     max(1, sum(s["total_missing"] for s in series)), 3)
                               if series else None),
        },
    }


# ── 与流水线共用（对话片段交付：source_status/_series/repair_trends）──
from panel.pipelines import _cols, _pick, source_status  # noqa: F401
from senses.sqldialect import txn


def _auto_bucket(span_sec):
    if span_sec <= 48 * 3600:   b = 300
    elif span_sec <= 90 * 86400: b = 3600
    else:                        b = 86400
    while span_sec / b > 500:    b *= 2          # 上限 500 桶
    return b


def _series(led, table, tcol, agg, start, end, b):
    """聚合一组值；返回 {bucket_start: {...}}"""
    expr = _bucket_expr(led.dialect, tcol, b)
    rows = _q(led, f"SELECT {expr} AS b, {agg} FROM {table} "
                   f"WHERE {tcol}>={{}} AND {tcol}<{{}} GROUP BY b", (start, end))
    return {int(r["b"]): r for r in rows}


def repair_trends(led, hours=24, bucket=None, tentacle=None):
    end = int(time.time())
    start = end - int(hours * 3600)
    b = bucket or _auto_bucket(hours * 3600)
    st = source_status(led)


def _q(led, sql, args):
    ph = "?" if led.dialect == "sqlite" else "%s"
    with txn(led) as cur:
        cur.execute(sql.replace("{}", ph), args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
