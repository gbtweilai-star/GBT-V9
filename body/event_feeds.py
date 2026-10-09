"""真实事件源 → 情绪事件：吞噬/扫描/队列读数按规则做边沿触发。

设计：把三域读到的最新读数映射成 ok/warn/crit 三档，只在“档位变化
且过了冷却”时放行一条事件，避免每轮轮询都刷屏。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Iterable


@dataclass(frozen=True)
class FeedRule:
    feed: str                       # devour / scan / queue
    domain: str                     # body_read_snapshots.domain
    metric: str                     # body_read_snapshots.metric
    kind: str                       # 触发时喂给导演的事件类型
    direction: str = "high"         # high: 越大越坏；low: 越小越坏
    warn_at: float = 0.0
    crit_at: float = 0.0
    hysteresis: float = 0.0         # 恢复需越过告警线这么多才算出边沿
    cooldown_s: float = 120.0
    label: str = ""
    unit: str = ""
    recover_kind: str = "task_success"

    def level_for(self, value: float) -> str:
        if self.direction == "high":
            if value >= self.crit_at:
                return "crit"
            if value >= self.warn_at:
                return "warn"
            return "ok"
        if value <= self.crit_at:
            return "crit"
        if value <= self.warn_at:
            return "warn"
        return "ok"

    def as_dict(self) -> dict:
        return {"feed": self.feed, "domain": self.domain, "metric": self.metric,
                "kind": self.kind, "direction": self.direction,
                "warn_at": self.warn_at, "crit_at": self.crit_at,
                "cooldown_s": self.cooldown_s, "label": self.label}


@dataclass
class Reading:
    feed: str
    key: str
    value: float
    level: str
    rule: FeedRule
    captured_epoch: float = 0.0
    evidence: dict = field(default_factory=dict)


def message_for(reading: Reading, level: str) -> tuple[str, str]:
    """返回 (中文播报文案, severity)。severity ∈ critical/warning/info。"""
    label = reading.rule.label or f"{reading.domain}.{reading.metric}"
    unit = reading.rule.unit

    def fmt(v: float) -> str:
        return f"{v:g}{unit}"

    if level == "crit":
        return (f"严重：{label}到 {fmt(reading.value)} 了，已过临界，我立刻处理。",
                "critical")
    if level == "warn":
        return (f"注意：{label}到 {fmt(reading.value)} 了，我在盯。", "warning")
    return (f"{label}恢复了，现在是 {fmt(reading.value)}。", "info")


class EdgeGate:
    """记忆上一次档位；只有档位变化且过了冷却才放行。"""

    def __init__(self) -> None:
        self._level: dict[str, str] = {}
        self._at: dict[str, float] = {}

    def decide(self, reading: Reading, now: float | None = None) -> str | None:
        now = now or time.time()
        key = f"{reading.feed}:{reading.key}"
        prev = self._level.get(key, "ok")
        cur = reading.level
        if cur == prev:
            return None
        if now - self._at.get(key, 0.0) < reading.rule.cooldown_s:
            return None                        # 冷却中，抑制抖动
        self._level[key] = cur
        self._at[key] = now
        return cur

    def export(self) -> dict:
        return {"levels": self._level, "at": self._at}

    def load(self, data: dict) -> None:
        self._level = dict(data.get("levels", {}))
        self._at = {k: float(v) for k, v in dict(data.get("at", {})).items()}


# ---- 三条默认规则（读 body_read_snapshots 的最新读数）---------------------
def default_rules() -> list[FeedRule]:
    return [
        # 吞噬能：丢帧 / 不可补帧段
        FeedRule("devour", "devour", "dropped_frames", kind="gap_detected",
                 warn_at=1, crit_at=10, hysteresis=1, cooldown_s=60,
                 label="吞噬丢帧", unit=" 帧"),
        FeedRule("devour", "devour", "unrecoverable_segments", kind="gap_detected",
                 warn_at=1, crit_at=3, hysteresis=1, cooldown_s=120,
                 label="不可补帧段", unit=" 段"),
        # 扫描覆盖：覆盖率（越低越坏） / 漏扫页面
        FeedRule("scan", "scan", "coverage_percent", kind="coverage_regression",
                 direction="low", warn_at=80, crit_at=70, hysteresis=1,
                 cooldown_s=180, label="扫描覆盖率", unit="%"),
        FeedRule("scan", "scan", "unscanned_pages", kind="gap_detected",
                 warn_at=1, crit_at=50, hysteresis=1, cooldown_s=120,
                 label="漏扫页面", unit=" 页"),
        # 队列：积压 / 等待 / 死信
        FeedRule("queue", "queue", "queue_depth", kind="queue_backlog",
                 warn_at=50, crit_at=200, hysteresis=10, cooldown_s=90,
                 label="队列深度", unit=" 个"),
        FeedRule("queue", "queue", "oldest_wait_s", kind="queue_backlog",
                 warn_at=120, crit_at=600, hysteresis=15, cooldown_s=120,
                 label="最长等待", unit=" 秒"),
        FeedRule("queue", "queue", "dlq_count", kind="task_failed",
                 warn_at=1, crit_at=5, hysteresis=1, cooldown_s=180,
                 label="死信任务", unit=" 个"),
    ]


def rules_from_env() -> list[FeedRule]:
    """可选：FEED_RULES_JSON 覆盖默认阈值。"""
    raw = os.getenv("FEED_RULES_JSON", "").strip()
    if not raw:
        return default_rules()
    try:
        data = json.loads(raw)
        rules = []
        for item in data:
            rules.append(FeedRule(**item))
        return rules or default_rules()
    except (TypeError, ValueError, json.JSONDecodeError):
        return default_rules()


class SnapshotRuleFeed:
    """从 body_read_snapshots 读每个 (domain, method) 的最新读数。

    假设列：domain TEXT, metric TEXT, value REAL, captured_epoch BIGINT。
    表/列缺失时静默返回空，不崩。
    """

    def __init__(self, rules: Iterable[FeedRule] | None = None,
                 table: str = "body_read_snapshots") -> None:
        self.rules = [r for r in (rules or default_rules()) if r.domain and r.metric]
        self.table = table

    async def read(self, db) -> list[Reading]:
        if db is None or not self.rules:
            return []
        domains = sorted({r.domain for r in self.rules})
        marks = ",".join("?" for _ in domains)
        sql = (
            f"SELECT s.domain, s.metric, s.value, s.captured_epoch "
            f"FROM {self.table} s "
            f"JOIN (SELECT domain, metric, MAX(captured_epoch) AS mx "
            f"      FROM {self.table} WHERE domain IN ({marks}) "
            f"      GROUP BY domain, metric) t "
            f"ON s.domain=t.domain AND s.metric=t.metric AND s.captured_epoch=t.mx")
        try:
            rows = await db.fetch_all(sql, tuple(domains))
        except Exception:
            return []
        latest: dict[tuple[str, str], dict] = {}
        for row in rows or []:
            r = dict(row)
            latest[(str(r["domain"]), str(r["metric"]))] = r
        out: list[Reading] = []
        for rule in self.rules:
            row = latest.get((rule.domain, rule.metric))
            if row is None:
                continue
            value = float(row["value"])
            out.append(Reading(
                feed=rule.feed, key=f"{rule.domain}:{rule.metric}", value=value,
                level=rule.level_for(value), rule=rule,
                captured_epoch=float(row.get("captured_epoch") or 0.0)))
        return out


class CoverageSnapshotFeed:
    """备选：直接读 coverage_snapshots，最新 per backend 覆盖不足即告警。"""

    def __init__(self, *, default_threshold: float = 80.0, crit_margin: float = 10.0,
                 cooldown_s: float = 180.0, drop_warn: float = 2.0,
                 drop_crit: float = 5.0) -> None:
        self.default_threshold = default_threshold
        self.crit_margin = crit_margin
        self.cooldown_s = cooldown_s
        # 回归阈值（百分点）：某次提交比上次下降超过它就告警（主人 2026-10-06 口径）
        self.drop_warn = float(os.getenv("COVERAGE_DROP_WARN", str(drop_warn)))
        self.drop_crit = float(os.getenv("COVERAGE_DROP_CRIT", str(drop_crit)))

    async def read(self, db) -> list[Reading]:
        if db is None:
            return []
        try:
            rows = await db.fetch_all(
                "SELECT backend, overall_percent, threshold, generated_epoch, commit_sha "
                "FROM coverage_snapshots "
                "ORDER BY generated_epoch DESC, snapshot_id DESC")
        except Exception:
            return []
        latest: dict[str, dict] = {}
        prev: dict[str, dict] = {}
        for row in rows or []:
            r = dict(row)
            b = str(r["backend"])
            if b not in latest:
                latest[b] = r
            elif b not in prev:
                prev[b] = r                        # 同一 backend 的第二新 = 上一次
        out: list[Reading] = []
        for backend, r in latest.items():
            pct = float(r["overall_percent"])
            thr = float(r.get("threshold") or self.default_threshold)
            if pct >= thr:
                level = "ok"
            elif pct >= thr - self.crit_margin:
                level = "warn"
            else:
                level = "crit"
            rule = FeedRule(
                "scan", "scan", "coverage_percent", kind="coverage_regression",
                direction="low", warn_at=thr, crit_at=thr - self.crit_margin,
                hysteresis=1.0, cooldown_s=self.cooldown_s,
                label=f"{backend} 覆盖率", unit="%")
            out.append(Reading("scan", f"scan:{backend}", pct, level, rule,
                               float(r.get("generated_epoch") or 0.0)))
            # ★回归：与上一次比下降多少（百分点）；下降 = 变坏 → level_for 用 low 方向口径
            p = prev.get(backend)
            if p is not None:
                drop = float(p["overall_percent"]) - pct
                dlevel = ("ok" if drop < self.drop_warn
                          else "crit" if drop >= self.drop_crit else "warn")
                drule = FeedRule(
                    "scan", "scan", "coverage_drop_pts", kind="coverage_regression",
                    direction="high", warn_at=self.drop_warn, crit_at=self.drop_crit,
                    hysteresis=0.5, cooldown_s=self.cooldown_s,
                    label=f"{backend} 覆盖率回归", unit=" 个百分点")
                out.append(Reading(
                    "scan", f"scan:{backend}:drop", drop, dlevel, drule,
                    float(r.get("generated_epoch") or 0.0),
                    evidence={"from_commit": p.get("commit_sha"),
                              "to_commit": r.get("commit_sha"),
                              "prev_percent": float(p["overall_percent"]),
                              "now_percent": pct}))
        return out


def default_feeds() -> list:
    """真实事件源（按优先级）：
    ① SnapshotJsonFeed —— 读 body_read_snapshots.payload_json（采集器真实写入的结构）
    ② CoverageSnapshotFeed —— 读 coverage_snapshots 历史，做"覆盖率回归"判定
    旧的 SnapshotRuleFeed 假设 (metric,value) 列，那套列在本库并不存在（等于空转），
    故不再作为默认源，仅在自定义表结构时才用。
    """
    rules = rules_from_env()
    return [SnapshotJsonFeed(rules), CoverageSnapshotFeed()]


# ══════════ 真实快照源：解析 payload_json（与 body/tools/collect.py 写入结构一致）══════════
def _dig(payload, path, default=0.0) -> float:
    cur = payload
    for p in path:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return float(default)
    try:
        return float(cur)
    except (TypeError, ValueError):
        return float(default)


# (domain, metric) → payload 取值路径（collect.py 写的就是这些字段）
METRIC_PATHS: dict[tuple, tuple] = {
    ("devour", "dropped_frames"): ("gaps",),
    ("queue", "queue_depth"): ("depth", "queued"),
    ("queue", "oldest_wait_s"): ("wait", "oldest_runnable_age"),
    ("queue", "dlq_count"): ("depth", "dead"),
}


class SnapshotJsonFeed:
    """从 body_read_snapshots.payload_json 取真实读数。

    诚实口径：coverage_percent 由索引真实计数推导（clean/(clean+dirty+missing)）；
    快照不可用（coverage=unavailable）时**不报 0**，而是跳过该 metric（宁缺勿假）。
    """

    def __init__(self, rules=None, table: str = "body_read_snapshots") -> None:
        self.rules = list(rules or default_rules())
        self.table = table

    async def read(self, db) -> list[Reading]:
        if db is None or not self.rules:
            return []
        try:
            rows = await db.fetch_all(
                "SELECT domain, payload_json, observed_at FROM " + self.table)
        except Exception:
            return []
        payloads: dict = {}
        for row in rows or []:
            r = dict(row)
            try:
                payloads[str(r["domain"])] = json.loads(r.get("payload_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        out: list[Reading] = []
        for rule in self.rules:
            payload = payloads.get(rule.domain)
            if not isinstance(payload, dict) or payload.get("coverage") == "unavailable":
                continue
            value = None
            if (rule.domain, rule.metric) in METRIC_PATHS:
                value = _dig(payload, METRIC_PATHS[(rule.domain, rule.metric)])
            elif rule.metric == "coverage_percent":
                idx = payload.get("index") or {}
                total = (float(idx.get("clean", 0)) + float(idx.get("dirty", 0))
                         + float(idx.get("missing", 0)))
                value = (float(idx.get("clean", 0)) / total * 100.0) if total > 0 else None
            elif rule.metric == "unscanned_pages":
                value = _dig(payload, ("index", "missing"))
            elif rule.metric == "unrecoverable_segments":
                value = _dig(payload, ("unrecoverable",))
            if value is None:
                continue
            out.append(Reading(feed=rule.feed, key=f"{rule.domain}:{rule.metric}",
                               value=value, level=rule.level_for(value), rule=rule,
                               evidence={"source": "payload_json"}))
        return out
