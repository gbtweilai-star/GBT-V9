# panel/dbmetrics.py —— 查询指标 + 告警状态机（进程内，有界）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 不记录参数值/域名/选择器; 只记录归一化 SQL 与指纹;
#       监控自身查询不走 fetch_all(防递归); 环形缓冲与 Top-K 都有上限

from __future__ import annotations
import contextvars, hashlib, os, re, time
from collections import Counter, deque
from dataclasses import dataclass, field

WORKER_ID = os.environ.get("WORKER_ID") or f"pid-{os.getpid()}"
ROUTE = contextvars.ContextVar("panel_route", default="-")

BUCKETS_MS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10_000, 30_000]

_CMT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_LIT = re.compile(r"'(?:[^']|'')*'")
_DQ  = re.compile(r'"(?:[^"]|"")*"')
_NUM = re.compile(r"\b\d+(?:\.\d+)?\b")
_SP  = re.compile(r"\s+")


def fingerprint(sql: str) -> tuple[str, str]:
    """(指纹, 归一化SQL)。归一化 SQL 已无字面量 → 可安全展示在面板上。"""
    s = _CMT.sub(" ", sql)
    s = _LIT.sub("?", s)
    s = _DQ.sub("?", s)
    s = _NUM.sub("?", s)
    s = _SP.sub(" ", s).strip()
    return hashlib.sha256(s.encode()).hexdigest()[:12], s[:200]


class QueryMetrics:
    MAX_SLOW   = 200      # 慢查询明细上限
    MAX_TOPK   = 200      # 指纹基数上限，防高基数
    SAMPLE_N   = 20       # 非慢查询 1/20 抽样进明细

    def __init__(self):
        self._hist = Counter()                 # bucket_ms -> count
        self._slow = deque(maxlen=self.MAX_SLOW)
        self._top  = Counter()                 # fp -> count
        self._shape = {}                       # fp -> 归一化 SQL
        self._worst = {}                       # fp -> 最大耗时
        self.total = self.slow_total = self.errors = 0
        self.busy_locked = 0                   # SQLite: database is locked
        self.acquire_wait_ms_total = 0.0       # 连接获取等待累计

    def record(self, sql: str, dur_ms: float, *, ok: bool = True,
               route: str = "-", sample: bool = True) -> None:
        fp, shape = fingerprint(sql)
        self._shape.setdefault(fp, shape)
        self._top[fp] += 1
        self.total += 1
        if not ok:
            self.errors += 1
        self._hist[self._bucket(dur_ms)] += 1

        if len(self._top) > self.MAX_TOPK:      # 淘汰最少见的，防内存膨胀
            for k, _ in self._top.most_common()[:-self.MAX_TOPK]:
                self._top.pop(k, None); self._shape.pop(k, None)
                self._worst.pop(k, None)

        self._worst[fp] = max(self._worst.get(fp, 0.0), dur_ms)

        is_slow = dur_ms >= SLOW_MS
        if is_slow:
            self.slow_total += 1
        if is_slow or (sample and self.total % self.SAMPLE_N == 0):
            self._slow.append({"fp": fp, "shape": shape, "ms": round(dur_ms, 1),
                               "route": route, "ok": ok,
                               "at": time.time()})

    @staticmethod
    def _bucket(dur_ms: float) -> int:
        for b in BUCKETS_MS:
            if dur_ms <= b:
                return b
        return BUCKETS_MS[-1]

    def percentile_ms(self, q: float = 0.95) -> float | None:
        """直方图近似分位（返回该桶上界，标注为"≤"）。"""
        if not self.total:
            return None
        target = q * self.total
        acc = 0
        for b in BUCKETS_MS:
            acc += self._hist.get(b, 0)
            if acc >= target:
                return float(b)
        return float(BUCKETS_MS[-1])

    def snapshot(self) -> dict:
        top = [{"fp": fp, "sql": self._shape.get(fp, ""), "count": n,
                "max_ms": round(self._worst.get(fp, 0.0), 1)}
               for fp, n in self._top.most_common(10)]
        recent = sorted(self._slow, key=lambda x: x["ms"], reverse=True)[:20]
        return {
            "worker_id": WORKER_ID,
            "scope": "per-worker",
            "total": self.total, "slow_total": self.slow_total,
            "errors": self.errors, "busy_locked": self.busy_locked,
            "acquire_wait_ms_total": round(self.acquire_wait_ms_total, 1),
            "p95_ms": self.percentile_ms(0.95),
            "p99_ms": self.percentile_ms(0.99),
            "p95_is_upper_bound": True,
            "top_slow_sql": top,
            "recent_slow": recent,
        }


METRICS = QueryMetrics()
SLOW_MS = float(os.environ.get("PANEL_SLOW_QUERY_MS", "200"))


# ───────────────────── 告警状态机（滞后 + 冷却） ─────────────────────
@dataclass
class AlertConfig:
    pool_warn: float = 0.80
    pool_crit: float = 0.90
    pool_recover: float = 0.70          # ★恢复阈值低于进入阈值 → 不抖动
    slow_p95_warn_ms: float = 500.0
    slow_p95_crit_ms: float = 2000.0
    enter_windows: int = 3              # 连续 3 个采样窗越线才升级
    exit_windows: int = 2               # 连续 2 个窗回落才解除
    cooldown_s: float = 60.0            # 同级别 60s 内不重复记 blocked


@dataclass
class AlertStateMachine:
    cfg: AlertConfig = field(default_factory=AlertConfig)
    level: str = "ok"
    _over: int = 0
    _under: int = 0
    _last_emit: dict = field(default_factory=dict)

    def _desired(self, s: dict) -> tuple[str, list[str]]:
        reasons = []
        used_ratio = s.get("pool_used_ratio")
        if s.get("lock_waits", 0) > 0:
            reasons.append(f"锁等待 {s['lock_waits']} 个")
        if used_ratio is not None and used_ratio > self.cfg.pool_crit:
            reasons.append(f"池占用 {used_ratio:.0%} > {self.cfg.pool_crit:.0%}")
        if reasons:
            return "crit", reasons

        if used_ratio is not None and used_ratio > self.cfg.pool_warn:
            reasons.append(f"池占用 {used_ratio:.0%} > {self.cfg.pool_warn:.0%}")
        p95 = s.get("p95_ms")
        if p95 is not None and p95 >= self.cfg.slow_p95_crit_ms:
            reasons.append(f"慢查询 p95 ≤{p95:.0f}ms ≥ {self.cfg.slow_p95_crit_ms:.0f}ms")
        if reasons:
            return "crit", reasons

        if p95 is not None and p95 >= self.cfg.slow_p95_warn_ms:
            reasons.append(f"慢查询 p95 ≤{p95:.0f}ms ≥ {self.cfg.slow_p95_warn_ms:.0f}ms")
        if s.get("waiting", 0) > 0:
            reasons.append(f"等待连接 {s['waiting']} 个")
        if reasons:
            return "warn", reasons
        return "ok", []

    def evaluate(self, sample: dict, now: float | None = None) -> dict:
        now = now or time.time()
        desired, reasons = self._desired(sample)

        if desired == "ok":
            self._over = 0
            self._under += 1
            if self.level != "ok" and self._under >= self.cfg.exit_windows:
                self.level = "ok"
                return {"level": "ok", "reasons": [], "changed": True, "emit": False}
            return {"level": self.level, "reasons": reasons, "changed": False, "emit": False}

        self._under = 0
        self._over += 1
        if self.level == "ok" and self._over < self.cfg.enter_windows:
            return {"level": "ok", "reasons": reasons, "changed": False, "emit": False}

        changed = desired != self.level
        self.level = desired
        emit = changed and (now - self._last_emit.get(desired, 0.0)) >= self.cfg.cooldown_s
        if emit:
            self._last_emit[desired] = now
        return {"level": desired, "reasons": reasons, "changed": changed, "emit": emit}


ALERTS = AlertStateMachine()
