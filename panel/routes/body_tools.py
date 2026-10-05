# panel/routes/body_tools.py —— 只读工具的快照/清单/调用 HTTP 面
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 面板只读；本模块【不查快照表】，一律经 body.tools.view.read_snapshot → 工具本体，
#       保证"卡片看到的数字"与"数字人回答的数字"永远是同一份。
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from common.db import get_db

router = APIRouter(prefix="/api/body")


class ToolCall(BaseModel):
    session_id: str = "panel"
    params: dict = {}


@router.get("/tools")
async def tools_list():
    """给编辑器/数字人的函数清单（含意图路由与系统约束）。"""
    from body.tools.prompt import tool_specs, tool_names, SYSTEM_RULE
    return {"tools": tool_specs(), "names": tool_names(), "system_rule": SYSTEM_RULE}


@router.get("/snapshots")
async def snapshots(request: Request, db=Depends(get_db)):
    """三张卡片一次拿全：每域一份 ToolResult（含 safe_sentence / stale / coverage）。"""
    from body.tools.view import read_all
    views = await read_all(db)
    etag = _etag({d: v["revision"] for d, v in views.items()})
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(views, headers={
        "ETag": etag,
        "Cache-Control": "private, no-cache, must-revalidate"})   # ★可复用但必须回源验 stale


@router.get("/snapshots/{domain}")
async def snapshot_one(domain: str, db=Depends(get_db)):
    from body.tools.view import read_snapshot, DOMAINS
    if domain not in DOMAINS:
        raise HTTPException(404, "unknown_domain")
    return (await read_snapshot(db, domain)).as_dict()


@router.post("/tools/{name}")
async def call(name: str, body: ToolCall, db=Depends(get_db)):
    """数字人问询入口：走 call_tool（校验白名单 → 取数 → 落 read_tool_audit）。"""
    from body.tools.base import call_tool, TOOLS
    from body.tools import domains            # noqa: F401  ← 确保已注册
    if name not in TOOLS:
        raise HTTPException(404, "unknown_tool")
    res = await call_tool(db, name, session_id=body.session_id, params=body.params)
    return res.as_dict()


def _etag(revs: dict) -> str:
    import hashlib, json as _j
    return '"' + hashlib.sha256(_j.dumps(revs, sort_keys=True).encode()).hexdigest()[:16] + '"'
