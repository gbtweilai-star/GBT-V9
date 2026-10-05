# body/history_query.py
# 分桶表达式：两端都是整数除法，只有占位符交给适配层
BUCKET_EXPR = "CAST((observed_epoch - ?) / ? AS BIGINT)"        # SQLite/PG 都吃

SQL_TREND = f"""
WITH ranked AS (
  SELECT {BUCKET_EXPR} AS bucket_id, value, observed_epoch, revision,
         ROW_NUMBER() OVER (PARTITION BY {BUCKET_EXPR}
                            ORDER BY observed_epoch DESC, revision DESC) AS rn
  FROM body_snapshot_metrics
  WHERE domain=? AND metric_id=? AND observed_epoch>=? AND observed_epoch<?
)
SELECT bucket_id, value, observed_epoch, revision, rn FROM ranked ORDER BY bucket_id"""

SQL_TREND_DERIVED = f"""
WITH ranked AS (
  SELECT {BUCKET_EXPR} AS bucket_id, metric_id, value, observed_epoch, revision,
         ROW_NUMBER() OVER (PARTITION BY {BUCKET_EXPR}, metric_id
                            ORDER BY observed_epoch DESC, revision DESC) AS rn
  FROM body_snapshot_metrics
  WHERE domain=? AND metric_id IN (?,?) AND observed_epoch>=? AND observed_epoch<?
)
SELECT bucket_id, metric_id, value FROM ranked WHERE rn=1 ORDER BY bucket_id"""


async def trend(ledger, *, domain, metric_id, frm=None, to=None, bucket_s=None,
                points_limit=1000, max_days=90, now_fn=time.time):
    from body.history import METRICS, DERIVED
    if metric_id not in METRICS and metric_id not in DERIVED:
        raise ValueError("metric_not_allowed")                   # ★白名单
    to = int(to or now_fn()); frm = int(frm or to - 86400)
    if to <= frm or (to - frm) > max_days * 86400:
        raise ValueError("range_invalid")
    span = to - frm
    bucket_s = int(bucket_s or max(1, span // points_limit))
    if span // bucket_s > points_limit:                          # 提高桶宽而不是截断
        bucket_s = max(1, -(-span // points_limit))              # ceil
    period = (await ledger.fetch_one(
        """SELECT sample_period_s FROM body_snapshot_history
           WHERE domain=? ORDER BY revision DESC LIMIT 1""", (domain,))) or {}
    period = period.get("sample_period_s") or bucket_s

    pts, method = [], "last_sample"
    if metric_id in DERIVED:
        num, den = DERIVED[metric_id]
        rows = await ledger.fetch_all(SQL_TREND_DERIVED,
                                      (frm, bucket_s, bucket_s, domain, num, den, frm, to))
        buckets: dict[int, dict] = {}
        for r in rows:
            buckets.setdefault(r["bucket_id"], {})[r["metric_id"]] = r["value"]
        for bid in sorted(buckets):
            n, d = buckets[bid].get(num), buckets[bid].get(den)
            pts.append({"t": frm + bid * bucket_s,
                        "value": (round(n / d, 6) if (n is not None and d) else None),
                        "method": "weighted_ratio"})             # ★按分子/分母重算
            method = "weighted_ratio"
    else:
        rows = await ledger.fetch_all(SQL_TREND,
                                      (frm, bucket_s, bucket_s, domain, metric_id, frm, to))
        for r in rows:
            pts.append({"t": frm + r["bucket_id"] * bucket_s,
                        "value": r["value"], "revision": r["revision"]})

    present = {p["t"] for p in pts if p["value"] is not None}
    all_buckets = {frm + i * bucket_s for i in range(span // bucket_s)}
    holes = sorted(all_buckets - present)
    regressed = any(pts[i]["t"] > pts[i + 1]["t"] for i in range(len(pts) - 1))
    return {"domain": domain, "metric_id": metric_id, "unit": METRICS.get(metric_id, Metric("", "", "", False, lambda p: None)).unit,
            "from": frm, "to": to, "bucket_s": bucket_s, "method": method,
            "points_limit": points_limit, "points": len(pts),
            "sample_period_s": period,
            "bucket_shorter_than_period": bucket_s < period,        # 别把采样稀疏当漏采
            "has_holes": bool(holes), "missing_bucket_count": len(holes),
            "clock_regressed": regressed,
            "series": pts}
