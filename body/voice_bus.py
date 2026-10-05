# body/voice_bus.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 全部声音在服务端串行出; 客户端只显示 —— 多标签页重复播报在架构上不存在;
#       critical 只在句子边界抢占; 未播文本必须存下来, 不丢话
import asyncio, logging, os, re, time, uuid

log = logging.getLogger("voice.bus")
SENT = re.compile(r"(?<=[。！？!?；;])")
RESUME_PREFIX = "刚才被安全告警打断，我继续回答。"


def _iso(ts) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


class VoiceBus:
    def __init__(self, tts, *, now_fn=time.time, ledger=None,
                 on_page_event=None):
        self.tts, self.now_fn = tts, now_fn
        self.ledger = ledger                      # 用于把 outbox 事件标成已播
        self.on_page_event = on_page_event        # 面板注册的推送钩子（SSE）
        self._queues: dict[str, asyncio.Queue] = {}
        self._current: dict[str, dict] = {}       # session_id -> 正在播的 utterance
        self._workers: dict[str, asyncio.Task] = {}

    def _q(self, sid):
        if sid not in self._queues:
            self._queues[sid] = asyncio.Queue(maxsize=int(os.getenv("VOICE_Q_MAX", 50)))
            self._workers[sid] = asyncio.create_task(self._run(sid))
        return self._queues[sid]

    async def submit(self, session_id: str, text: str, *, priority: int,
                     source: str, event_ids: list[str] | None = None) -> dict:
        """source: dialog | alert。dialog=LLM 对答，alert=见证告警。"""
        u = {"utterance_id": uuid.uuid4().hex, "text": text, "priority": priority,
             "source": source, "event_ids": event_ids or [], "queued_at": self.now_fn()}
        q = self._q(session_id)
        try:
            q.put_nowait(u)
        except asyncio.QueueFull:
            # ★背压：critical 必须落库不可丢，这里只拒绝排队并把文本交给页面
            await self._on_backpressure(session_id, u)
            return {"accepted": False, "reason": "queue_full", "text": text,
                    "visible_in_page": True}
        if priority == 0:
            await self._preempt(session_id, u)      # 抢占在句子边界生效
        return {"accepted": True, "utterance_id": u["utterance_id"]}

    async def _preempt(self, sid, incoming):
        cur = self._current.get(sid)
        if not cur or cur.get("interrupt_requested"):
            return
        cur["interrupt_requested"] = True           # 由 _speak 在句子边界检查
        cur["interrupted_by"] = incoming["utterance_id"]

    async def _run(self, sid):
        q = self._queues[sid]
        while True:
            u = await q.get()
            self._current[sid] = u
            try:
                await self._speak(sid, u)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("voice bus speak error")
            finally:
                self._current.pop(sid, None)

    async def _speak(self, sid, u):
        """按句子边界播；被打断则保存未播文本，随后续接。"""
        sentences = [s for s in SENT.split(u["text"]) if s.strip()]
        # ★先让页面拿到文本：音频失败（没有 TTS）时，话也不能丢
        await self._notify_page(sid, {"type": "speaking", "utterance_id": u["utterance_id"],
                                      "text": u["text"], "source": u["source"],
                                      "priority": u["priority"]})
        spoken = []
        for i, s in enumerate(sentences):
            if u.get("interrupt_requested") and i > 0:        # 只在句子边界让位
                remaining = "".join(sentences[i:])
                await self._park_remainder(sid, u, remaining)
                await self._notify_page(sid, {"type": "interrupted",
                                              "utterance_id": u["utterance_id"],
                                              "spoken": "".join(spoken),
                                              "remaining": remaining})
                return
            await self._speak_one(s, u["priority"] == 0)
            spoken.append(s)
        if u["event_ids"]:
            await self._mark_events(u["event_ids"], "spoken")
        await self._notify_page(sid, {"type": "spoken", "utterance_id": u["utterance_id"],
                                      "text": u["text"], "source": u["source"]})

    async def _park_remainder(self, sid, u, remaining):
        """续接：把未播部分作为一条新的对答式播报排到队首之后。"""
        if u["source"] != "dialog":
            return                                   # 告警不需要续接（本身是短句）
        await self._q(sid).put({"utterance_id": uuid.uuid4().hex,
                                "text": RESUME_PREFIX + remaining, "priority": 1,
                                "source": "dialog_resume", "event_ids": [], "queued_at": self.now_fn()})

    # ─────────── 页面推送 / 事件状态 / 背压（此前只有调用、没有实现）───────────
    async def _speak_one(self, text, critical: bool):
        """TTS 适配层：speak_priority > speak > enqueue。

        senses.voice.VoiceAdapter 是**队列式**的（enqueue + 自己线程播），
        没有 speak 方法 —— 直接调 speak 会 AttributeError，声音就永远出不来。
        """
        t = getattr(self, "tts", None)
        if t is None:
            raise RuntimeError("no_tts")
        prio = "critical" if critical else "normal"
        for attr in ("speak_priority", "speak"):
            fn = getattr(t, attr, None)
            if fn is None:
                continue
            try:
                r = (fn(text, priority=prio, interrupt=critical) if attr == "speak_priority"
                     else fn(text, priority=prio))
            except TypeError:                                # 适配器签名更简
                r = fn(text)
            if asyncio.iscoroutine(r):
                await r
            return
        fn = getattr(t, "enqueue", None)
        if fn is None:
            raise RuntimeError("tts_has_no_speak_or_enqueue")
        r = fn(text, priority=0 if critical else 1)
        if isinstance(r, dict) and r.get("queued") is False:   # 背压/去重 → 如实算失败
            raise RuntimeError("tts_refused:" + str(r.get("reason")))

    async def _notify_page(self, sid, payload):
        """把播报状态推给数字人页面。没有订阅者也要静默成功（页面可能没开）。"""
        if self.on_page_event is None:
            return
        try:
            r = self.on_page_event(sid, payload)
            if asyncio.iscoroutine(r):
                await r
        except Exception:                                # noqa: BLE001
            log.exception("page notify failed")

    async def _mark_events(self, event_ids, state):
        """把 outbox 事件标成已播。★失败必须留痕：不许静默当作已播。"""
        if self.ledger is None or not event_ids:
            return
        for eid in event_ids:
            try:
                await self.ledger.execute(
                    "UPDATE witness_voice_outbox SET state=?, updated_at=? "
                    "WHERE event_id=?", (state, _iso(self.now_fn()), eid))
            except Exception:                            # noqa: BLE001
                log.exception("mark spoken failed: %s", eid)

    async def _on_backpressure(self, sid, u):
        """队列满：critical 的文本必须让页面看见（不丢话）。"""
        log.warning("voice queue full for %s: %s", sid, u["text"])
        await self._notify_page(sid, {"type": "backpressure", "text": u["text"],
                                      "utterance_id": u["utterance_id"],
                                      "priority": u["priority"]})

