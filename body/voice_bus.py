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
        """TTS 适配层：speak_priority > speak > enqueue；**全失败则退到本机 SAPI**。

        VoiceAdapter 是队列式的（enqueue + 自己线程播），没有 speak 方法；
        而 VoiceStudio(3900) 没起时主通道必然失败 —— 真机表现就是"说·TTS 完成 0 / 失败 N"。
        这里补一条本机免费通道（Windows SAPI，默认台湾腔韵律），并如实标出用的哪条通道。
        """
        t = getattr(self, "tts", None)
        prio = "critical" if critical else "normal"
        if t is not None:
            for attr in ("speak_priority", "speak"):
                fn = getattr(t, attr, None)
                if fn is None:
                    continue
                try:
                    r = (fn(text, priority=prio, interrupt=critical)
                         if attr == "speak_priority" else fn(text, priority=prio))
                except TypeError:                            # 适配器签名更简
                    r = fn(text)
                except Exception:                            # noqa: BLE001
                    continue                                 # 主通道抛错 → 试下一个/退本机
                if asyncio.iscoroutine(r):
                    try:
                        r = await r
                    except Exception:                        # noqa: BLE001
                        continue
                if not (isinstance(r, dict) and r.get("ok") is False):
                    self.last_channel = "voicestudio"
                    return
            fn = getattr(t, "enqueue", None)
            # ★2026-10-08：队列**没 start 就是空转** —— 入队成功不等于说过话，
            #   不许拿它冒充成功（否则本机 SAPI 兜底永远轮不到，页面记"说·TTS 完成"而人没听见）。
            if fn is not None and getattr(t, "started", False):
                try:
                    r = fn(text, priority=0 if critical else 1)
                except Exception:                            # noqa: BLE001
                    r = {"queued": False, "reason": "enqueue_failed"}
                if not (isinstance(r, dict) and r.get("queued") is False):
                    self.last_channel = "voicestudio-queue"
                    return
        try:                                                 # 本机免费通道（台湾腔韵律）
            from senses import voice_sapi as vs
            got = vs.speak(text, taiwan=True)
            if got.get("ok"):
                self.last_channel = "sapi"
                return
            raise RuntimeError(f"local_tts_failed:{got.get('reason')}")
        except Exception as exc:                              # noqa: BLE001
            raise RuntimeError(f"no_tts_channel:{type(exc).__name__}") from exc

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


# ─────────── 模块级播报口（此前被 body/witness_runtime.py:303 空引用）───────────
# 真机病因（2026-10-08）：witness_runtime 拿不到 bus 实例，只能
#   `from body.voice_bus import announce` —— 而本模块**从来没有 announce**，
#   ImportError 被 except 吞掉，于是那条告警只剩写日志（人听不见）。现在给出真实现。
_INSTALLED: dict = {}


def install_bus(bus) -> None:
    """登记装好的 VoiceBus，供模块级 announce() 用（panel/server.py 的 _wire_voice_bus 调）。"""
    _INSTALLED["bus"] = bus


async def announce(text: str, *, priority: int = 1) -> dict:
    """模块级播报：① 有 bus 就交给它；② 没有/失败就退本机 SAPI；③ 都不行如实 ok:False。

    每一步都标清走的哪条通道 —— 不许出现"报了但没人听见"还没有痕迹的情况。
    """
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "通道": "", "reason": "空文本"}
    bus = _INSTALLED.get("bus")
    if bus is not None:
        for attr in ("say", "speak", "notify", "announce"):
            fn = getattr(bus, attr, None)
            if not callable(fn):
                continue
            r = None
            try:
                r = fn(t, critical=(int(priority) == 0))
            except TypeError:
                try:
                    r = fn(t)
                except Exception:                            # noqa: BLE001
                    continue
            except Exception:                                # noqa: BLE001
                continue
            if asyncio.iscoroutine(r):
                try:
                    r = await r
                except Exception:                            # noqa: BLE001
                    continue
            return {"ok": True, "通道": f"voice_bus.{attr}", "结果": r}
    try:
        from senses import voice_sapi as _vs
        got = _vs.speak(t, taiwan=True)
        return {"ok": bool(got.get("ok")), "通道": "sapi", "结果": got}
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "通道": "", "reason": f"{type(exc).__name__}: {exc}"}

