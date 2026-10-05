"""为覆盖率快照添加可选 label 列。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations
from typing import Any


async def apply(db: Any) -> None:
    if db.dialect == "sqlite":
        rows = await db.fetch_all("PRAGMA table_info(coverage_snapshots)")
        columns = {row["name"] for row in rows}
    elif db.dialect in {"postgres", "postgresql"}:
        rows = await db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            ("coverage_snapshots",))
        columns = {row["column_name"] for row in rows}
    else:
        raise RuntimeError(f"unsupported DB dialect: {db.dialect!r}")

    if not columns:
        raise RuntimeError("coverage_snapshots table is missing; apply v3 first")
    if "label" not in columns:
        await db.execute("ALTER TABLE coverage_snapshots ADD COLUMN label TEXT")
