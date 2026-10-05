"""情绪喂料器：定时轮询事件源，边沿触发即调 VoiceDirector.notify。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque

from body.emotion import upsert_state
from body.event_feeds import (EdgeGate, Reading, default_feeds, message_for)


class EmotionFeeder:
    def __init__(self, director, *, db=None, feeds=None, interval_s: float = 15.0,
                 state_kind: str = "feedstate:emotion", user: str = "主人") -> None:
        self.director = director
        self.db = db if db is not None else getattr(director, "db", None)
        self.feeds = feeds if feeds is not None else default_feeds()
        self.interval_s = float(interval_s)
        self.state_kind = state_kind
        self.user = user
        self.gate = EdgeGate()
        self.last_events: deque = deque(maxlen=30)
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        try:
            director.feeder = self          # 让 /api/voice/emotion 能带上 feed_events
        except Exception:
            pass

    # ---- 状态持久（重启后不再重播同一边沿）-------------------------------
    async def load(self) -> None:
        if self.db is None:
            return
        try:
            row = await self.db.fetch_one(
                "SELECT value FROM voice_state WHERE kind=?", (self.state_kind,))
        except Exception:
            return
        if row:
            try:
                self.gate.load(json.loads(row["value"] if isinstance(row, dict) else row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass

    async def save(self) -> None:
        if self.db is None:
            return
        await upsert_state(self.db, self.state_kind, json.dumps(self.gate.export()))

    # ---- 单轮 -------------------------------------------------------------
    async def poll_once(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        emitted: list[dict] = []
        for feed in self.feeds:
            try:
                readings = await feed.read(self.db)
            except Exception:
                continue
            for reading in readings:
                level = self.gate.decide(reading, now)
                if level is None:
                    continue
                say, severity = message_for(reading, level)
                kind = reading.rule.kind if level != "ok" else reading.rule.recover_kind
                purpose = "alert" if level != "ok" else "report"
                try:
                    await self.director.notify(
                        kind, severity, user=self.user, say=say, purpose=purpose,
                        dedupe_key=f"feed:{reading.feed}:{reading.key}:{level}")
                except Exception:
                    continue
                evt = {"feed": reading.feed, "key": reading.key, "level": level,
                       "value": reading.value, "kind": kind, "severity": severity,
                       "say": say, "at": now}
                self.last_events.append(evt)
                emitted.append(evt)
        if emitted:
            await self.save()
        return emitted

    # ---- 循环 / 生命周期 --------------------------------------------------
    async def run(self) -> None:
        await self.load()
        while not self._stop.is_set():
            try:
                await self.poll_once()
            except Exception:
                pass
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                pass

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)
