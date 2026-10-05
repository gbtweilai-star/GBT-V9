"""语音导演：把事件→情绪→语气→韵律→TTS 队列串起来，并暴露只读状态。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import time
from collections import deque
from typing import Any

from body.emotion import EmotionEngine, EmotionState, upsert_state
from body.prosody import choose_style, render
from body.social import SocialLayer

_PRIORITY = {"alert": 2, "celebrate": 3, "comfort": 4, "report": 6, "chitchat": 8}

_DIRECTOR: "VoiceDirector | None" = None


def install(director: "VoiceDirector") -> None:
    global _DIRECTOR
    _DIRECTOR = director


def current() -> "VoiceDirector | None":
    return _DIRECTOR


class VoiceDirector:
    def __init__(self, voice, emotion: EmotionEngine, social: SocialLayer, *,
                 tts: Any = None, db=None, recent: int = 40) -> None:
        self.voice = voice                 # 需有 async enqueue(text, priority=..., [dedupe_key=...])
        self.emotion = emotion
        self.social = social
        self.tts = tts                     # 可选：SSML 直发通道
        self.db = db
        self._recent: deque[dict] = deque(maxlen=recent)
        self._last_dedupe: dict[str, float] = {}

    # ---- 事件入口 ---------------------------------------------------------
    async def notify(self, kind: str, severity: str = "info", *, user: str = "主人",
                     weight: float = 1.0, say: str | None = None,
                     purpose: str | None = None, dedupe_key: str | None = None) -> EmotionState:
        state = self.emotion.apply_event(kind, severity, weight)
        self.social.observe(user, kind)
        if say is not None:
            await self.say(say, purpose=purpose or self._purpose_for(kind, severity),
                           severity=severity, user=user, dedupe_key=dedupe_key)
        if self.db is not None:
            await self.emotion.save()
            await self.social.save(user)
        return state

    @staticmethod
    def _purpose_for(kind: str, severity: str | None) -> str:
        if severity in ("critical", "severe"):
            return "alert"
        if kind in ("task_failed", "gap_detected", "coverage_regression",
                    "witness_conflict", "queue_backlog"):
            return "alert"
        if kind == "user_praise":
            return "celebrate"
        if kind == "user_blame":
            return "comfort"
        if kind == "greeting":
            return "chitchat"
        return "report"

    # ---- 说话 -------------------------------------------------------------
    async def say(self, text: str, purpose: str = "report", severity: str | None = None,
                  user: str = "主人", dedupe_key: str | None = None,
                  priority: int | None = None) -> str:
        self.emotion.tick()
        st = self.emotion.state
        style_name = choose_style(st.mood, st.intensity, purpose)
        tone = self.social.tone_for(purpose, user, st, severity)
        if tone.style_hint:
            style_name = tone.style_hint

        composed = tone.opening + text.strip() + tone.closing
        result = render(composed, style_name, intensity=st.intensity)
        prio = priority if priority is not None else _PRIORITY.get(purpose, 6)
        await self._enqueue(result.text, prio, dedupe_key)

        self._recent.append({
            "text": result.text, "purpose": purpose, "mood": st.mood,
            "style": result.style, "severity": severity, "user": user,
            "rate": result.rate, "pitch": result.pitch, "at": time.time()})
        return result.text

    async def _enqueue(self, text: str, priority: int, dedupe_key: str | None) -> None:
        enqueue = self.voice.enqueue
        try:
            if dedupe_key is not None:
                await enqueue(text, priority=priority, dedupe_key=dedupe_key)
            else:
                await enqueue(text, priority=priority)
        except TypeError:                      # 队列不支持 dedupe_key → 优雅降级
            await enqueue(text, priority=priority)

    # ---- 只读状态（面板 / 数字人工具同源）--------------------------------
    def status(self) -> dict:
        snap = self.emotion.snapshot()
        st = self.emotion.state
        purpose = "report"
        return {
            "pad": {"p": snap["p"], "a": snap["a"], "d": snap["d"]},
            "mood": snap["mood"], "intensity": snap["intensity"],
            "valence": snap["valence"], "baseline": snap["baseline"],
            "style": choose_style(st.mood, st.intensity, purpose),
            "rapport": self.social.all(),
            "recent_lines": list(self._recent)[-12:],
        }


async def emotion_status(db=None) -> dict:
    """数字人只读工具：情绪/关系/最近播报。与面板同源。"""
    director = current()
    if director is not None:
        return director.status()

    # 兜底：直接读持久状态（无运行中导演时）
    import json
    result: dict = {"pad": None, "mood": "平", "intensity": 0.0, "valence": 0.0,
                    "rapport": [], "recent_lines": [], "table_missing": False}
    if db is None:
        return result
    try:
        row = await db.fetch_one("SELECT value FROM voice_state WHERE kind=?", ("emotion",))
        rows = await db.fetch_all(
            "SELECT kind, value FROM voice_state WHERE kind LIKE ?", ("rapport:%",))
    except Exception:
        result["table_missing"] = True
        return result
    if row:
        data = json.loads(row["value"] if isinstance(row, dict) else row[0])
        result["pad"] = {"p": data.get("p"), "a": data.get("a"), "d": data.get("d")}
        result["mood"] = data.get("mood", "平")
    for r in rows or []:
        kind = r["kind"] if isinstance(r, dict) else r[0]
        value = r["value"] if isinstance(r, dict) else r[1]
        result["rapport"].append({"user": str(kind).split(":", 1)[1],
                                  **json.loads(value)})
    return result
