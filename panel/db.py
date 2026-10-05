# panel/db.py —— 面板只读 db 适配器（契约与测试 TestDB 完全一致）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 契约(不可变): .dialect  .fetch_all(sql, params)  .timestamp_param(value)
# 纪律: 面板读路径一律只读(PG default_transaction_read_only / SQLite mode=ro);
#       写入走账本自己的连接, 绝不从这里写

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiosqlite
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from common.timeutil import timestamp_param

STATEMENT_TIMEOUT_MS = 5000
SQLITE_BUSY_TIMEOUT_MS = 5000


class PanelDB:
    """两后端共用壳；routes 侧 `_ph(db)` 无需任何改动。"""

    def __init__(self, conn, dialect: str):
        self.conn = conn
        self.dialect = dialect

    def timestamp_param(self, value):
        return timestamp_param(self.dialect, value)

    async def fetch_all(self, sql: str, params=()) -> list[dict]:
        if self.dialect == "sqlite":
            cur = await self.conn.execute(sql, tuple(params))
            try:
                return [dict(row) for row in await cur.fetchall()]
            finally:
                await cur.close()

        async with self.conn.cursor() as cur:
            await cur.execute(sql, tuple(params))        # psycopg 用 %s
            return [dict(row) for row in await cur.fetchall()]


# ─────────────────────────────────────────────
# 只读 DSN 处理
# ─────────────────────────────────────────────
def _sqlite_ro_uri(dsn: str) -> str:
    path = Path(urlsplit(dsn).path).resolve()
    if not path.is_file():
        raise RuntimeError(f"SQLite 面板只读库不存在: {path}")
    return f"file:{quote(str(path))}?mode=ro"            # ★内核级只读


async def _configure_pg(conn) -> None:
    """纵深防护；生产仍应给面板配只读 DB role。"""
    await conn.execute("SET default_transaction_read_only = on")
    await conn.execute(f"SET statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")


# ─────────────────────────────────────────────
# 生命周期：池在 lifespan 建，依赖从 app.state 取
# ─────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app):
    dsn = getattr(app.state, "database_url", "")
    if dsn.startswith(("postgres://", "postgresql://")):
        pool = AsyncConnectionPool(
            conninfo=dsn,
            kwargs={"autocommit": True, "row_factory": dict_row},
            min_size=1, max_size=10,
            configure=_configure_pg, open=False)
        await pool.open(wait=True)
        app.state.pg_pool = pool
    elif dsn.startswith("sqlite:///"):
        app.state.sqlite_uri = _sqlite_ro_uri(dsn)
    else:
        raise RuntimeError(f"不支持的面板数据库 DSN: {dsn!r}")

    try:
        yield
    finally:
        pool = getattr(app.state, "pg_pool", None)
        if pool is not None:
            await pool.close()


async def get_db(request: Request):
    """FastAPI 依赖；每请求取一条连接，用完归还/关闭。"""
    pool = getattr(request.app.state, "pg_pool", None)
    if pool is not None:
        async with pool.connection() as conn:
            yield PanelDB(conn, "postgres")
        return

    conn = await aiosqlite.connect(request.app.state.sqlite_uri,
                                   uri=True, timeout=5)
    conn.row_factory = aiosqlite.Row
    try:
        await conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        await conn.execute("PRAGMA query_only = ON")      # ★SQLite 只读
        yield PanelDB(conn, "sqlite")
    finally:
        await conn.close()
