#!/usr/bin/env python3
"""对指定 PostgreSQL DSN 执行项目迁移并验证 schema。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
from typing import Any

from body.adapters.postgres_db import PostgresDb
from migrations.runner import apply_pending, boot_check


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def migrate(dsn: str) -> None:
    # 保留 DSN query 参数；asyncpg>=0.18 支持 ?sslmode=require。
    db = await PostgresDb.connect(dsn, min_size=1, max_size=4)
    try:
        applied = await apply_pending(db)
        await boot_check(db)
        tables = await db.fetch_all(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_type='BASE TABLE' "
            "ORDER BY table_name"
        )
        print(f"Applied migration versions: {applied or 'none (already current)'}")
        print("Tables: " + ", ".join(str(r["table_name"]) for r in tables))
    finally:
        closer = getattr(db, "aclose", None) or getattr(db, "close", None)
        if closer is not None:
            await _resolve(closer())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True, help="PostgreSQL DSN passed to asyncpg")
    args = parser.parse_args()
    try:
        asyncio.run(migrate(args.db))
    except Exception as exc:
        parser.exit(1, f"Migration failed: {type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
