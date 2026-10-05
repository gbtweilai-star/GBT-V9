# panel/health.py —— 触手健康画像：指标 + 证据 + 可解释推荐
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 不做加权总分(避免虚假精确感); 用五级 + 每条附带证据
#   - 样本不达标 → 显示"样本不足", 不出百分比、不下结论
#   - 每条推荐标注 evidence + confidence(高/中/低) + auto/manual
#   - 缺表/缺列/查询失败 → 标 unavailable, 绝不伪装成零或健康
import os, time
from panel.trends import source_status, _cols, _pick, _q


def _agg_by_tent(led, table, tcol, agg, start, end):
    """按触手聚合；失败返回 None（=数据源不可用，不是零）"""
    try:
        rows = _q(led, f"""SELECT tentacle_id, {agg} FROM {table}
                           WHERE {tcol}>={{}} AND {tcol}<{{}}
                           GROUP BY tentacle_id""", (start, end))
        return {r["tentacle_id"]: r for r in rows}
    except Exception:
        return None


def _captured_expr(led, seg_tbl):
    cols = _cols(led, seg_tbl)
    if "frame_count" in cols:
        return "SUM(frame_count)"
    if "frames" in cols:
        return "SUM(frames)"
    s = _pick(cols, ("start_frame",))
    e = _pick(cols, ("end_frame",))
    if s and e:
        return f"SUM({e} - {s} + 1)"
    return None


def tentacle_health(led, hours=24):
    end = int(time.time())
    start = end - int(hours * 3600)
    prev_end, prev_start = start, start - int(hours * 3600)
    st = source_status(led)

    # ── 丢帧（本窗 + 前窗）──
    gap_tbl = (st.get("gap", {}).get("table")
               if st.get("gap", {}).get("available") else None)
    cur_gap = prev_gap = None
    if gap_tbl:
        cols = _cols(led, gap_tbl)
        mcol = _pick(cols, ("missing", "count")) or "1"
        tcol = _pick(cols, ("ts", "created_at")) or "ts"
        agg = f"SUM({mcol}) missing, COUNT(*) events, MIN({tcol}) first_ts, MAX({tcol}) last_ts"
        cur_gap = _agg_by_tent(led, gap_tbl, tcol, agg, start, end)
        prev_gap = _agg_by_tent(led, gap_tbl, tcol, agg, prev_start, prev_end)

    # ── 采集覆盖 + 有效帧数 ──
    seg_tbl = (st.get("devour", {}).get("table")
               if st.get("devour", {}).get("available") else None)
    cap = None
    if seg_tbl:
        tcol = _pick(_cols(led, seg_tbl), ("ts", "created_at")) or "ts"
        cexpr = _captured_expr(led, seg_tbl)
        agg = f"{cexpr} captured, COUNT(*) segs" if cexpr else "COUNT(*) segs"
        cap = _agg_by_tent(led, seg_tbl, tcol, agg, start, end, )

    # ── 补偿最终处置（repair_jobs，不累加重试）──
    comp = {}
    try:
        comp = {r["tentacle_id"]: r for r in _q(led, """
            SELECT tentacle_id, SUM(restored_frames) restored,
                   SUM(resampled_frames) resampled, SUM(permanent_frames) permanent,
                   SUM(CASE WHEN state='dead_letter' THEN 1 ELSE 0 END) dead
            FROM repair_jobs WHERE updated_at>={} AND updated_at<{}
            GROUP BY tentacle_id""", (start, end))}
    except Exception:
        comp = {}

    # ── 交叉复核 + 扫描受阻 + 能力失败率 ──
    cross = {}
    try:
        cross = {r["tentacle_id"]: r for r in _q(led, """
            SELECT owner tentacle_id, SUM(CASE WHEN consistent IN (0,'0',false)
                   THEN 1 ELSE 0 END) mismatch, COUNT(*) total
            FROM cross_arbitration WHERE ts>={} AND ts<{} GROUP BY owner""",
            (start, end))}
    except Exception:
        cross = {}
    blocked = {}
    try:
        blocked = {r["tentacle_id"]: r for r in _q(led, """
            SELECT tentacle_id, SUM(CASE WHEN status='blocked' THEN 1 ELSE 0 END) blk
            FROM ledger WHERE ts>={} AND ts<{} GROUP BY tentacle_id""",
            (start, end))}
    except Exception:
        blocked = {}

    tids = sorted(set(cur_gap or {}) | set(cap or {}) | set(comp or {}) |
                  set(prev_gap or {}))
    out = []
    for tid in tids:
        g = (cur_gap or {}).get(tid, {})
        pg = (prev_gap or {}).get(tid, {})
        c0 = comp.get(tid, {})
        missing = int(g.get("missing") or 0)
        captured = (int((cap or {}).get(tid, {}).get("captured") or 0)
                    if cap is not None else None)
        has_cover = cap is not None and tid in cap

        # 指标（样本不足 → None）
        loss_per_100k = (round(missing / (captured + missing) * 100000)
                         if (captured is not None and captured + missing > 0) else None)
        restored = int(c0.get("restored") or 0)
        resampled = int(c0.get("resampled") or 0)
        permanent = int(c0.get("permanent") or 0)
        restoration_rate = (round(restored / missing, 4) if missing else None)
        permanent_ratio = (round(permanent / missing, 4) if missing else None)

        # 趋势：两窗样本都要够
        trend = None
        prev_missing = int(pg.get("missing") or 0)
        if cur_gap is not None and prev_gap is not None and (has_cover or missing):
            if missing == prev_missing:
                trend = "持平"
            elif missing > prev_missing:
                trend = "恶化"
            else:
                trend = "改善"
        if trend is None:
            trend = "趋势样本不足"

        mismatch = int(cross.get(tid, {}).get("mismatch") or 0)
        blk = int(blocked.get(tid, {}).get("blk") or 0)
        dead = int(c0.get("dead") or 0)

        # ── 分级（规则阶梯，不搞总分）──
        if not has_cover and missing == 0:
            grade = "样本不足"
        elif permanent > 0 or mismatch > 0 or blk > 0:
            grade = "异常"
        elif missing > 0 and (restoration_rate is None or restoration_rate < 0.99):
            grade = "亚健康"
        elif missing > 0:
            grade = "关注"
        else:
            grade = "健康"

        # ── 主导指标（按规则优先级，不比数值大小）──
        if permanent > 0:              dom = "永久丢失"
        elif mismatch > 0:             dom = "交叉复核不一致"
        elif blk > 0:                  dom = "扫描受阻"
        elif grade == "样本不足":       dom = None
        elif loss_per_100k and loss_per_100k >= 100:  dom = "高丢帧率"
        elif missing > 0 and (restoration_rate or 0) < 0.99: dom = "补齐率低"
        elif missing > 0:              dom = "轻微丢帧"
        else:                          dom = None

        evidence, recs = [], []
        # ① 永久丢失
        if permanent > 0:
            evidence.append(f"永久丢失 {permanent} 帧（占丢帧 "
                            f"{(permanent_ratio or 0)*100:.0f}%）")
            recs.append({"text": "检查采集链路、缓冲区容量与当时资源指标",
                         "confidence": "高", "mode": "manual",
                         "reason": "永久丢失为已观测事实；具体瓶颈需看资源读数"})
        # ② 死信
        if dead > 0:
            evidence.append(f"{dead} 个补偿任务达重试上限（死信）")
            recs.append({"text": "复检死信任务的失败原因，必要时手动重排",
                         "confidence": "高", "mode": "manual",
                         "reason": "重试已耗尽，需人判断是否再试"})
        # ③ 重采多但未补齐
        if resampled > 0 and restored == 0:
            evidence.append(f"重采 {resampled} 帧但未恢复任何原帧")
            recs.append({"text": "检查采集源稳定性与重采配置",
                         "confidence": "中", "mode": "manual",
                         "reason": "重采不等于补齐，源可能持续不稳"})
        # ④ 趋势恶化
        if trend == "恶化":
            evidence.append(f"丢帧较前一窗口上升（{prev_missing} → {missing} 帧）")
            recs.append({"text": "检查该时段采集负载与资源竞争",
                         "confidence": "中", "mode": "manual",
                         "reason": "无 CPU/磁盘/网络读数时不指认具体瓶颈"})
        # ⑤ 交叉不一致
        if mismatch > 0:
            evidence.append(f"交叉复核不一致 {mismatch} 次")
            recs.append({"text": "复查相关目标与两根触手的证据",
                         "confidence": "高", "mode": "manual",
                         "reason": "不一致是事实，原因未知"})
        # ⑥ 扫描受阻
        if blk > 0:
            evidence.append(f"扫描受阻 {blk} 次")
            recs.append({"text": "查看卡点步骤与关联错误",
                         "confidence": "中", "mode": "manual",
                         "reason": "卡点原因需查看主脑裁决记录"})
        # ⑦ 无采集证据
        if not has_cover and missing == 0:
            evidence.append("本窗口无帧段覆盖记录")
            recs.append({"text": "先检查触手心跳与启动状态",
                         "confidence": "低", "mode": "manual",
                         "reason": "无覆盖可能是未启动，也可能是采集未写入"})

        out.append({
            "tentacle_id": tid, "grade": grade, "dominant_metric": dom,
            "metrics": {
                "missing_frames": missing, "loss_per_100k": loss_per_100k,
                "captured_frames": captured,
                "verified_restored": restored, "resampled": resampled,
                "restoration_rate": restoration_rate,
                "permanent_frames": permanent, "permanent_ratio": permanent_ratio,
                "trend": trend, "prev_missing": prev_missing,
                "gap_events": int(g.get("events") or 0),
                "last_gap_ts": g.get("last_ts"), "cross_mismatch": mismatch,
                "blocked": blk, "dead_letter": dead,
            },
            "evidence": evidence, "recommendations": recs,
            "coverage": ("observed" if has_cover else
                         ("no_data" if cap is not None else "unavailable")),
        })

    # 排序：异常优先，再按丢帧量
    rank = {"异常": 0, "亚健康": 1, "关注": 2, "健康": 3, "样本不足": 4}
    out.sort(key=lambda x: (rank.get(x["grade"], 9), -x["metrics"]["missing_frames"]))
    return {"hours": hours, "from": start, "to": end,
            "coverage_available": cap is not None, "tentacles": out,
            "grade_counts": _grade_counts(out)}


def _grade_counts(rows):
    c = {}
    for r in rows:
        c[r["grade"]] = c.get(r["grade"], 0) + 1
    return c
