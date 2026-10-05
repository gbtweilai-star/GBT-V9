# body/tools/view.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 卡片路径与语音工具路径【必须】调本函数; 任何一处自己查快照表 = 漂移入口
#      （HTTP 路由在 panel/routes/body_tools.py —— 本模块只做取数，不依赖 FastAPI）
from __future__ import annotations
import time

from body.tools.base import TOOLS, ToolResult, unknown_sentence
from body.tools import domains            # noqa: F401  ← 导入即注册四个域工具

DOMAIN_TOOL = {"devour": "media.capture", "scan": "scan.coverage",
               "queue": "media.queue", "witness": "get_witness_snapshot"}
DOMAINS = ("devour", "scan", "queue", "witness")


async def read_snapshot(ledger, domain: str, *, now_fn=time.time,
                        ttl_mult=3) -> ToolResult:
    """唯一读取路径：工具与卡片都走这里。返回同一份 ToolResult。"""
    tool = TOOLS.get(DOMAIN_TOOL.get(domain, ""))
    if tool is None:
        return ToolResult(tool=domain, domain=domain, unknown_reason="unknown_domain",
                          safe_sentence=unknown_sentence("没有这个域"))
    return await tool.run(ledger, now_fn=now_fn, ttl_mult=ttl_mult)


async def read_all(ledger, *, now_fn=time.time, ttl_mult=3) -> dict:
    """三张卡片一次拿全（见证卡走 /api/body/witnesses 的顶层数字 + 同一份快照）。"""
    return {d: (await read_snapshot(ledger, d, now_fn=now_fn, ttl_mult=ttl_mult)).as_dict()
            for d in ("devour", "scan", "queue")}
