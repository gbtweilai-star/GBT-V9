"""PostgreSQL/asyncpg DB 适配器。
铁律：事务内所有 SQL 使用同一条专用连接；锁操作必须在事务内。
"""
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import asyncpg

from .base_db import _BaseDb


class PostgresDb(_BaseDb):
    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 10) -> None:
        super().__init__("postgres")
        if min_size < 1 or max_size < min_size:
            raise ValueError("require 1 <= min_size <= max_size")
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self._pool: asyncpg.Pool | None = None
        self._pool_lock = asyncio.Lock()

    @classmethod
    async def open(cls, dsn: str, *, min_size: int = 1, max_size: int = 10) -> "PostgresDb":
        """工厂入口：按 DSN 打开；连接池在首次查询时惰性建立。"""
        return cls(dsn, min_size=min_size, max_size=max_size)

    async def _get_pool(self) -> asyncpg.Pool:
        self._ensure_open()
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(
                        dsn=self.dsn, min_size=self.min_size, max_size=self.max_size)
        return self._pool

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[asyncpg.Connection]:
        current = self._current_connection.get()
        if current is not None:
            yield current
            return
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            yield conn

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = False) -> AsyncIterator[None]:
        """事务用一条专用 asyncpg 连接，中途不换连接。

        PostgreSQL 不区分 SQLite 的 immediate；该参数仅为统一调用契约。
        """
        del immediate
        self._ensure_open()
        if self._current_connection.get() is not None:
            raise RuntimeError("nested transactions are not supported")
        conn = await asyncpg.connect(self.dsn)
        token = None
        try:
            await conn.execute("BEGIN")
            token = self._set_transaction_connection(conn)
            try:
                yield
                await conn.execute("COMMIT")
            except BaseException as original:
                try:
                    await conn.execute("ROLLBACK")
                except BaseException as rollback_error:
                    raise rollback_error from original
                raise
        finally:
            if token is not None:
                self._reset_transaction_connection(token)
            await conn.close()

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self._ensure_open()
        values = tuple(params)
        query = self._translate_qmarks(sql, len(values))
        async with self._connection() as conn:
            status = await conn.execute(query, *values)
            return self._command_rowcount(status)

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self._ensure_open()
        values = tuple(params)
        query = self._translate_qmarks(sql, len(values))
        async with self._connection() as conn:
            record = await conn.fetchrow(query, *values)
            return dict(record) if record is not None else None

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self._ensure_open()
        values = tuple(params)
        query = self._translate_qmarks(sql, len(values))
        async with self._connection() as conn:
            records = await conn.fetch(query, *values)
            return [dict(r) for r in records]

    async def db_now_epoch(self) -> int:
        row = await self.fetch_one("SELECT EXTRACT(EPOCH FROM now())::bigint AS epoch")
        if row is None:
            raise RuntimeError("PostgreSQL did not return its database clock")
        return int(row["epoch"])

    async def lock_artifact(self, ref: str) -> dict[str, Any] | None:
        self._require_transaction()
        return await self.fetch_one(
            "SELECT * FROM artifact_objects WHERE ref = ? FOR UPDATE", (ref,))

    async def lock_lease(self, lease_id: str) -> dict[str, Any] | None:
        self._require_transaction()
        return await self.fetch_one(
            "SELECT * FROM frame_leases WHERE lease_id = ? FOR UPDATE", (lease_id,))

    async def lock_key(self, key: str) -> None:
        self._require_transaction()
        await self.fetch_one(
            "SELECT pg_advisory_xact_lock(hashtextextended(?, 0)) AS locked", (key,))

    async def transition(self, ref: str, from_state: str, to_state: str, *,
                         guard: str = "", params: tuple[Any, ...] = ()) -> bool:
        self._require_transaction()
        if not guard and params:
            raise ValueError("transition params require a non-empty guard")
        sql = "UPDATE artifact_objects SET state = ? WHERE ref = ? AND state = ?"
        if guard:
            sql += f" AND ({guard})"
        rowcount = await self.execute(sql, (to_state, ref, from_state, *tuple(params)))
        return rowcount > 0

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            pool, self._pool = self._pool, None
            await pool.close()

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> "PostgresDb":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
