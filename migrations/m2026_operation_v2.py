"""添加并回填 frame-evidence 的 operation 列。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

from typing import Any

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_frame_verification_evidence_operation
ON frame_verification_evidence (operation, created_epoch DESC, id DESC)
"""


async def _column_exists(db: Any) -> bool:
    if db.dialect == "sqlite":
        columns = await db.fetch_all(
            "PRAGMA table_info(frame_verification_evidence)")
        return any(row["name"] == "operation" for row in columns)
    if db.dialect in {"postgres", "postgresql"}:
        row = await db.fetch_one(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name=? AND column_name=?",
            ("frame_verification_evidence", "operation"),
        )
        return row is not None
    raise RuntimeError(f"unsupported DB dialect: {db.dialect!r}")


async def apply(db: Any) -> None:
    """在 runner 的事务内执行；可重复运行。"""
    # 表缺了就明确报错，不把"表不存在"当空 schema 静默放过
    await db.fetch_one("SELECT 1 FROM frame_verification_evidence WHERE 1=0")

    if not await _column_exists(db):
        await db.execute(
            "ALTER TABLE frame_verification_evidence "
            "ADD COLUMN operation TEXT NOT NULL DEFAULT 'legacy'"
        )

    await db.execute(                       # 回填历史行
        "UPDATE frame_verification_evidence SET operation='legacy' "
        "WHERE operation IS NULL OR operation=''"
    )
    await db.execute(INDEX_SQL)
