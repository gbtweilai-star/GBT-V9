# panel/ops_page.py —— 操作绑定表面板（她能查，你也看得见）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from fastapi import APIRouter

router = APIRouter()


@router.get("/api/ops")
async def ops_table() -> dict:
    """专业化操作绑定表（触手↔工具↔判据↔坑）。**读的是源码，不是记忆**——重启必在。"""
    from core.operation_bindings import BINDINGS
    rows = [dict(r) for r in BINDINGS]
    return {"条数": len(rows), "已验证": sum(1 for r in rows if r["状态"] == "已验证"),
            "未验": sum(1 for r in rows if r["状态"] != "已验证"), "行": rows,
            "口径": "状态只有'已验证'与'未验'两种；已验证的都有本会话真读数"}
