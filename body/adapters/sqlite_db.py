"""SQLite DB 适配器。
铁律：写事务用 BEGIN IMMEDIATE；锁操作必须在事务内。
"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite

from .base_db import _BaseDb


class SqliteDb(_BaseDb):
    def __init__(self, path: str | Path) -> None:
        super().__init__("sqlite")
        raw_path = str(path)
        self._is_memory = raw_path == ":memory:"
        if self._is_memory:
            # 共享内存 URI，避免每条短连接看到不同的 :memory: 库
            self.path = f"file:ports_{uuid.uuid4().hex}?mode=memory&cache=shared"
            self._uri = True
        else:
            self.path = raw_path
            self._uri = False
        self._memory_anchor: aiosqlite.Connection | None = None
        self._anchor_lock = asyncio.Lock()
        self._wal_lock = asyncio.Lock()
        self._wal_ready = False

    @classmethod
    async def open(cls, dsn: str | Path) -> "SqliteDb":
        """工厂入口：按 DSN/路径打开（sqlite:/// 前缀可选，连接惰性建立）。"""
        path = str(dsn)
        if path.startswith("sqlite:///"):
            path = path[len("sqlite:///"):]
        return cls(path)

    async def _new_connection(self) -> aiosqlite.Connection:
        conn = await aiosqlite.connect(self.path, uri=self._uri,
                                       isolation_level=None)  # 事务由 transaction() 控制
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    async def _ensure_memory_anchor(self) -> None:
        if not self._is_memory or self._memory_anchor is not None:
            return
        async with self._anchor_lock:
            if self._memory_anchor is None:
                self._memory_anchor = await self._new_connection()

    async def _open_connection(self) -> aiosqlite.Connection:
        self._ensure_open()
        if self._is_memory:
            await self._ensure_memory_anchor()
        conn = await self._new_connection()
        if not self._is_memory and not self._wal_ready:
            try:
                async with self._wal_lock:
                    if not self._wal_ready:
                        cur = await conn.execute("PRAGMA journal_mode = WAL")
                        row = await cur.fetchone()
                        await cur.close()
                        mode = str(row[0]).lower() if row else ""
                        if mode != "wal":
                            raise RuntimeError(f"SQLite WAL unavailable (journal_mode={mode!r})")
                        self._wal_ready = True
            except BaseException:
                await conn.close()
                raise
        return conn

    @asynccontextmanager
    async def _connection(self) -> AsyncIterator[aiosqlite.Connection]:
        current = self._current_connection.get()
        if current is not None:
            yield current
            return
        conn = await self._open_connection()
        try:
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def transaction(self, *, immediate: bool = False) -> AsyncIterator[None]:
        """单连接显式事务；异常回滚，回滚失败会继续抛出。"""
        self._ensure_open()
        if self._current_connection.get() is not None:
            raise RuntimeError("nested transactions are not supported")
        conn = await self._open_connection()
        token = None
        try:
            await conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            token = self._set_transaction_connection(conn)
            try:
                yield
                await conn.commit()
            except BaseException as original:
                try:
                    await conn.rollback()
                except BaseException as rollback_error:
                    raise rollback_error from original
                raise
        finally:
            if token is not None:
                self._reset_transaction_connection(token)
            await conn.close()

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        self._ensure_open()
        async with self._connection() as conn:
            cur = await conn.execute(sql, tuple(params))
            try:
                return int(cur.rowcount)
            finally:
                await cur.close()

    async def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        self._ensure_open()
        async with self._connection() as conn:
            cur = await conn.execute(sql, tuple(params))
            try:
                row = await cur.fetchone()
                return dict(row) if row is not None else None
            finally:
                await cur.close()

    async def fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self._ensure_open()
        async with self._connection() as conn:
            cur = await conn.execute(sql, tuple(params))
            try:
                rows = await cur.fetchall()
                return [dict(r) for r in rows]
            finally:
                await cur.close()

    async def db_now_epoch(self) -> int:
        row = await self.fetch_one("SELECT CAST(strftime('%s','now') AS INTEGER) AS epoch")
        if row is None:
            raise RuntimeError("SQLite did not return its database clock")
        return int(row["epoch"])

    async def lock_artifact(self, ref: str) -> dict[str, Any] | None:
        self._require_transaction()
        # SQLite 无 SELECT FOR UPDATE；BEGIN IMMEDIATE 串行化写事务
        return await self.fetch_one("SELECT * FROM artifact_objects WHERE ref = ?", (ref,))

    async def lock_lease(self, lease_id: str) -> dict[str, Any] | None:
        self._require_transaction()
        return await self.fetch_one("SELECT * FROM frame_leases WHERE lease_id = ?", (lease_id,))

    async def lock_key(self, key: str) -> None:
        self._require_transaction()
        return None  # BEGIN IMMEDIATE 已串行化

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
        if self._memory_anchor is not None:
            anchor, self._memory_anchor = self._memory_anchor, None
            await anchor.close()

    async def aclose(self) -> None:
        await self.close()

    async def __aenter__(self) -> "SqliteDb":
        self._ensure_open()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()
