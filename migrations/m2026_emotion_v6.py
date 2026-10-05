# migrations/m2026_emotion_v6.py
"""情绪/关系持久表 voice_state。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations
from typing import Any

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS voice_state (
    kind TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL,
    updated_epoch BIGINT NOT NULL
)
"""


async def _columns(db: Any) -> set[str]:
    if str(db.dialect) == "sqlite":
        rows = await db.fetch_all("PRAGMA table_info(voice_state)")
        return {r["name"] for r in rows}
    rows = await db.fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=?", ("voice_state",))
    return {r["column_name"] for r in rows}


async def apply(db: Any) -> None:
    await db.execute(_CREATE_TABLE)
    missing = {"kind", "value", "updated_epoch"} - await _columns(db)
    if missing:
        raise RuntimeError(f"voice_state schema incompatible; missing {sorted(missing)}")
