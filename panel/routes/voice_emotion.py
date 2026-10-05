"""情绪只读 API；SQL 用 ? 占位符（PostgresDb 转 $n）。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

from fastapi import APIRouter

from panel.deps import db          # 与 backend 报告同源适配器

try:
    from body.voice_director import current as _current_director, emotion_status
except Exception:                  # 模块未落盘时不 500
    _current_director = None

    async def emotion_status(db=None):     # type: ignore
        return {"pad": None, "mood": "平", "rapport": [], "recent_lines": [],
                "table_missing": True}

router = APIRouter()


@router.get("/api/voice/emotion")
async def voice_emotion() -> dict:
    director = _current_director() if _current_director else None
    if director is not None:
        return director.status()
    return await emotion_status(db)
