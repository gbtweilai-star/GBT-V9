# panel/routes/digital_human.py —— 数字人：实时语音行 + 只读工具问询
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 页面数字一律来自 witness_snapshot / body_read_snapshots（与卡片同源）；
#       SSE 断线用 Last-Event-ID 补拉（含 spoken_failed 的文本，不许丢话）。
from core.swallow import swallow as _swallow
import asyncio
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from common.db import get_db

router = APIRouter(prefix="/api/digital-human")

BUS_SUBSCRIBERS: dict[str, list[asyncio.Queue]] = {}
MAX_SUB_Q = 200
SESSION = "witness"          # 见证告警固定一路会话；对答也走这一路

LEDGER_IMPORT = None          # 由 server.py 注入可写身体库（面板只读库写不了）


def publish(session_id: str, payload: dict) -> None:
    """语音总线的页面推送钩子：推给所有订阅者，队列满就丢最旧（不阻塞播报）。"""
    for q in list(BUS_SUBSCRIBERS.get(session_id, [])):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(payload)
            except Exception as e:
                _swallow(__file__, e)


class Ask(BaseModel):
    session_id: str = SESSION
    say: bool = True                 # 是否让本体念出来（默认念）


@router.get("/stream")
async def stream(request: Request,
                 last_event_id: str | None = Header(None, alias="Last-Event-ID"),
                 db=Depends(get_db)):
    sid = SESSION
    q: asyncio.Queue = asyncio.Queue(maxsize=MAX_SUB_Q)
    BUS_SUBSCRIBERS.setdefault(sid, []).append(q)

    async def gen():
        try:
            # ① 断线补拉：按 event_id 从库里捞漏掉的（含 spoken_failed 文本）
            if last_event_id:
                missed = await db.fetch_all(
                    """SELECT * FROM witness_voice_outbox WHERE created_at > (
                           SELECT created_at FROM witness_voice_outbox WHERE event_id=?)
                       ORDER BY created_at""", [last_event_id])
                for m in missed:
                    yield _sse(m["event_id"], {"type": "alert", **m})
            # ② 首帧永远推一次快照（页面刷新立刻有正确数字）
            snap = await db.fetch_all("SELECT * FROM witness_snapshot WHERE id=1", [])
            if snap:
                yield _sse(f"snap:{snap[0]['revision']}",
                           {"type": "snapshot", **snap[0],
                            "states": json.loads(snap[0]["states_json"] or "{}")})
            # ③ 订阅推送；keepalive 防中间层断连
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield _sse(msg.get("utterance_id") or "evt", msg)
        finally:
            subs = BUS_SUBSCRIBERS.get(sid, [])
            if q in subs:
                subs.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(event_id, payload):
    return (f"id: {event_id}\nevent: witness\n"
            f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n")


@router.get("/witness")
async def witness_line(db=Depends(get_db)):
    """见证实时行：与卡片、工具同一个读取路径（body.tools.view.read_snapshot）。"""
    from body.tools.view import read_snapshot
    return (await read_snapshot(db, "witness")).as_dict()


@router.get("/tools")
async def tools_list():
    from body.tools.prompt import tool_specs, tool_names, SYSTEM_RULE
    return {"tools": tool_specs(), "names": tool_names(), "system_rule": SYSTEM_RULE}


@router.get("/state/{domain}")
async def state(domain: str, db=Depends(get_db)):
    from body.tools.view import read_snapshot, DOMAINS
    if domain not in DOMAINS:
        raise HTTPException(404, "unknown_domain")
    return (await read_snapshot(db, domain)).as_dict()


@router.post("/ask/{tool}")
async def ask(tool: str, body: Ask, request: Request):
    """问一个只读工具 → 如实转述 → 让本体念出来（念的是 safe_sentence，不另编话）。"""
    from body.tools.base import TOOLS
    from body.tools import domains            # noqa: F401  ← 确保已注册
    if tool not in TOOLS:
        raise HTTPException(404, "unknown_tool")
    ledger = LEDGER_IMPORT
    if ledger is None:
        raise HTTPException(503, "body_ledger_unavailable")
    from body.tools.base import call_tool
    res = await call_tool(ledger, tool, session_id=body.session_id)
    if body.say:
        bus = getattr(request.app.state, "voice_bus", None)
        if bus is not None:
            await bus.submit(body.session_id, res.safe_sentence, priority=1,
                             source="dialog")
    return res.as_dict()


@router.post("/say-witness")
async def say_witness(request: Request):
    """主动说出当前有效见证数（对讲时用；数字来自快照，不重算）。"""
    from body.tools.view import read_snapshot
    ledger = LEDGER_IMPORT
    if ledger is None:
        raise HTTPException(503, "body_ledger_unavailable")
    res = await read_snapshot(ledger, "witness")
    bus = getattr(request.app.state, "voice_bus", None)
    if bus is not None:
        await bus.submit(SESSION, res.safe_sentence, priority=1, source="dialog")
    return res.as_dict()
