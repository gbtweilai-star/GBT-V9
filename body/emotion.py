"""情绪状态模型：PAD 连续状态 + 离散情绪；确定性的语气模拟。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from typing import Callable
from core.swallow import swallow as _swallow

# 稳态基准：温和、放松、略有掌控
BASELINE: tuple[float, float, float] = (0.15, 0.0, 0.1)
MOODS = ("喜", "怒", "哀", "乐", "惊", "平")


def _clamp(value: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


# kind -> (ΔP 愉悦, ΔA 唤醒, ΔD 掌控)
EVENT_DELTAS: dict[str, tuple[float, float, float]] = {
    "task_success":        (0.25,  0.05,  0.10),
    "task_failed":         (-0.30, 0.15, -0.15),
    "gap_detected":        (-0.20, 0.25, -0.05),
    "coverage_regression": (-0.25, 0.20,  0.05),
    "queue_backlog":       (-0.10, 0.15, -0.05),
    "witness_conflict":    (-0.20, 0.20,  0.10),
    "user_praise":         (0.35,  0.15,  0.15),
    "user_blame":          (-0.40, 0.20, -0.25),
    "idle":                (0.02, -0.15,  0.00),
    "deadline_pressure":   (-0.10, 0.30,  0.05),
    "long_task_progress":  (0.05,  0.05,  0.05),
    "greeting":            (0.10,  0.05,  0.05),
}

SEVERITY_SCALE: dict[str, float] = {
    "critical": 1.6, "severe": 1.6, "warning": 1.0, "info": 0.6,
}


def classify_mood(p: float, a: float, d: float) -> str:
    """把 PAD 落到离散情绪面。"""
    if a >= 0.50 and abs(p) < 0.30:
        return "惊"
    if p >= 0.20:
        return "乐" if a >= 0.25 else "喜"
    if p <= -0.15:
        if a >= 0.20 and d >= -0.05:
            return "怒"
        return "哀"
    return "平"


@dataclass
class EmotionState:
    p: float = BASELINE[0]
    a: float = BASELINE[1]
    d: float = BASELINE[2]
    mood: str = "平"
    updated: float = field(default_factory=time.time)

    @property
    def intensity(self) -> float:
        return _clamp(
            (abs(self.p - BASELINE[0]) + abs(self.a - BASELINE[1])
             + abs(self.d - BASELINE[2])) / 3.0, 0.0, 1.0)

    @property
    def valence(self) -> float:
        return self.p

    def as_dict(self) -> dict:
        return {"p": round(self.p, 4), "a": round(self.a, 4), "d": round(self.d, 4),
                "mood": self.mood, "intensity": round(self.intensity, 4),
                "valence": round(self.valence, 4), "updated": self.updated}


async def upsert_state(db, kind: str, value: str, *, now: float | None = None) -> None:
    """写单表 voice_state(kind, value, updated_epoch)，SQLite/PG 双兼容。"""
    epoch = int(now or time.time())
    dialect = str(getattr(db, "dialect", "sqlite"))
    if dialect.startswith("postgres"):
        sql = ("INSERT INTO voice_state (kind, value, updated_epoch) VALUES (?, ?, ?) "
               "ON CONFLICT (kind) DO UPDATE SET value=EXCLUDED.value, "
               "updated_epoch=EXCLUDED.updated_epoch")
    elif dialect == "sqlite":
        sql = ("INSERT INTO voice_state (kind, value, updated_epoch) VALUES (?, ?, ?) "
               "ON CONFLICT(kind) DO UPDATE SET value=excluded.value, "
               "updated_epoch=excluded.updated_epoch")
    else:
        raise RuntimeError(f"unsupported DB dialect: {dialect!r}")
    await db.execute(sql, (kind, value, epoch))


class EmotionEngine:
    """同步更新内存状态，异步 load/save 持久化（重启不失忆）。"""

    def __init__(self, db=None, *, baseline: tuple[float, float, float] = BASELINE,
                 decay_rate: float = 0.02,
                 hook: Callable[[str, dict], None] | None = None,
                 persist: bool = True) -> None:
        self.db = db
        self.baseline = baseline
        self.decay_rate = decay_rate
        self.hook = hook
        self.persist = persist
        self.state = EmotionState(*baseline)

    # ---- 事件 -------------------------------------------------------------
    def apply_event(self, kind: str, severity: str = "info",
                    weight: float = 1.0, *, now: float | None = None) -> EmotionState:
        now = now or time.time()
        self.tick(now)
        delta = EVENT_DELTAS.get(kind)
        if delta is None:
            return self.state
        scale = SEVERITY_SCALE.get(str(severity).lower(), 1.0) * float(weight)
        p = _clamp(self.state.p + delta[0] * scale)
        a = _clamp(self.state.a + delta[1] * scale)
        d = _clamp(self.state.d + delta[2] * scale)
        self.state = EmotionState(p, a, d, classify_mood(p, a, d), now)
        if self.hook is not None:
            try:
                self.hook(kind, self.state.as_dict())
            except Exception as e:
                _swallow(__file__, e)

        return self.state

    # ---- 稳态回归 ---------------------------------------------------------
    def tick(self, now: float | None = None) -> EmotionState:
        now = now or time.time()
        dt = max(0.0, now - self.state.updated)
        if dt <= 0.0:
            return self.state
        k = 1.0 - math.exp(-self.decay_rate * dt)      # 指数回归
        bp, ba, bd = self.baseline
        p = self.state.p + (bp - self.state.p) * k
        a = self.state.a + (ba - self.state.a) * k
        d = self.state.d + (bd - self.state.d) * k
        self.state = EmotionState(p, a, d, classify_mood(p, a, d), now)
        return self.state

    def snapshot(self) -> dict:
        self.tick()
        snap = self.state.as_dict()
        snap["baseline"] = {"p": self.baseline[0], "a": self.baseline[1],
                            "d": self.baseline[2]}
        return snap

    # ---- 持久化 -----------------------------------------------------------
    async def load(self) -> EmotionState:
        if self.db is None or not self.persist:
            return self.state
        try:
            row = await self.db.fetch_one(
                "SELECT value FROM voice_state WHERE kind=?", ("emotion",))
        except Exception:                              # 缺表不崩
            return self.state
        if row:
            try:
                data = json.loads(row["value"] if isinstance(row, dict) else row[0])
                self.state = EmotionState(
                    _clamp(float(data.get("p", self.baseline[0]))),
                    _clamp(float(data.get("a", self.baseline[1]))),
                    _clamp(float(data.get("d", self.baseline[2]))),
                    str(data.get("mood", "平")),
                    float(data.get("updated", time.time())))
            except (TypeError, ValueError, json.JSONDecodeError) as e:
                _swallow(__file__, e)

        return self.state

    async def save(self) -> None:
        if self.db is None or not self.persist:
            return
        payload = json.dumps({"p": self.state.p, "a": self.state.a, "d": self.state.d,
                              "mood": self.state.mood, "updated": self.state.updated})
        await upsert_state(self.db, "emotion", payload)
