# body/history.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 指标只认白名单 id, 不接受 JSON 路径; NULL 也要落行(与"漏写"区分);
#       历史与快照同事务; 比率类由分子/分母重算, 绝不平均
from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from typing import Callable

@dataclass(frozen=True)
class Metric:
    domain: str
    unit: str              # frames|ratio|count|seconds|bytes|mb
    direction: str         # higher | lower | neutral
    allow_null: bool
    extract: Callable[[dict], float | None]


def M(mid, unit, direction, allow_null, fn) -> Metric:
    return Metric(mid.split(".")[0], unit, direction, allow_null, fn)


METRICS: dict[str, Metric] = {
    "devour.frames":            M("devour.frames", "frames", "neutral", True,
                                  lambda p: p.get("frames")),
    "devour.gap_intervals":     M("devour.gap_intervals", "count", "lower", True,
                                  lambda p: p.get("gap_intervals")),
    "devour.recovered_frames":  M("devour.recovered_frames", "frames", "higher", True,
                                  lambda p: p.get("recovered_frames")),
    "devour.unresolved_gap_frames": M("devour.unresolved_gap_frames", "frames", "lower", True,
                                  lambda p: p.get("unresolved_gap_frames")),
    "devour.cache_ratio":       M("devour.cache_ratio", "ratio", "lower", True,
                                  lambda p: (p.get("cache") or {}).get("ratio")),
    "devour.pending_bytes":     M("devour.pending_bytes", "bytes", "lower", True,
                                  lambda p: (p.get("archive") or {}).get("pending_bytes")),
    "scan.coverage":            M("scan.coverage", "ratio", "higher", True,
                                  lambda p: p.get("coverage")),
    "scan.scanned":             M("scan.scanned", "count", "higher", True,
                                  lambda p: p.get("scanned")),
    "scan.expected":            M("scan.expected", "count", "neutral", True,
                                  lambda p: p.get("expected")),
    "scan.gaps":                M("scan.gaps", "count", "lower", True,
                                  lambda p: p.get("gaps")),
    "scan.findings":            M("scan.findings", "count", "lower", True,
                                  lambda p: p.get("findings")),
    "queue.depth":              M("queue.depth", "count", "lower", True,
                                  lambda p: p.get("depth")),
    "queue.wait_p50_s":         M("queue.wait_p50_s", "seconds", "lower", True,
                                  lambda p: p.get("wait_p50_s")),
    "queue.failed":             M("queue.failed", "count", "lower", True,
                                  lambda p: p.get("failed")),          # 分子
    "queue.event_count":        M("queue.event_count", "count", "neutral", True,
                                  lambda p: p.get("event_count")),     # 分母
    "queue.dead":               M("queue.dead", "count", "lower", True,
                                  lambda p: p.get("dead")),
    "queue.vram_used_mb":       M("queue.vram_used_mb", "mb", "lower", True,
                                  lambda p: (p.get("vram") or {}).get("used_mb")),
}
# 派生指标：由分子/分母在查询时重算，不单独落值
DERIVED = {"queue.failure_rate": ("queue.failed", "queue.event_count"),
           "scan.coverage_derived": ("scan.scanned", "scan.expected")}
NUMERIC = {"higher", "lower", "neutral"}


def collect_metrics(domain: str, payload: dict) -> dict[str, float | None]:
    out = {}
    for mid, m in METRICS.items():
        if m.domain != domain:
            continue
        v = m.extract(payload)
        if v is not None:
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = None
        out[mid] = v                       # ★None 也保留：写入时落 NULL 行
    return out


def metrics_digest(metrics: dict) -> str:
    canon = json.dumps({k: (None if v is None else round(float(v), 6))
                        for k, v in sorted(metrics.items())},
                       separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canon.encode()).hexdigest()[:16]


def history_status(payload: dict, *, unknown_reason: str | None) -> str:
    if payload.get("consistency", "ok") != "ok":
        return "violation"
    if unknown_reason:
        return "unknown"
    if payload.get("status") in ("partial",):
        return "partial"
    return "healthy"


async def append_history(tx, *, domain, revision, observed_at, observed_epoch,
                         sample_period_s, payload, evidence_json, unknown_reason=None):
    """在发布快照的【同一事务】内调用：写入历史主行 + 全部指标行。"""
    metrics = collect_metrics(domain, payload)
    status = history_status(payload, unknown_reason=unknown_reason)
    await tx.execute(
        """INSERT INTO body_snapshot_history (domain, revision, observed_at,
               observed_epoch, sample_period_s, status, unknown_reason,
               metrics_digest, evidence_json) VALUES (?,?,?,?,?,?,?,?,?)
           ON CONFLICT (domain, revision) DO NOTHING""",
        (domain, revision, observed_at, observed_epoch, sample_period_s, status,
         unknown_reason, metrics_digest(metrics), json.dumps(evidence_json, sort_keys=True)))
    for mid, val in metrics.items():
        await tx.execute(
            """INSERT INTO body_snapshot_metrics (domain, revision, metric_id, value)
               VALUES (?,?,?,?) ON CONFLICT DO NOTHING""",
            (domain, revision, mid, val))
    return {"status": status, "metrics": len(metrics)}
