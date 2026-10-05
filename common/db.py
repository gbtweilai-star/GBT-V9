# common/db.py —— 面板读路径适配层（对齐点 D）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 本文件**不含任何 SQL**：只把面板路由的读请求薄转发到身体库适配器
# （panel.deps.db → body.adapters 的 SqliteDb / PostgresDb，两者同接口）。
# 这样 SQL 永远写在路由里（字面量），适配层不可能成为拼接 SQL 的地方。
from __future__ import annotations

from panel.deps import db as _db

# 身体库默认与面板同库（单一事实源）：面板默认 data/gbt_v9.sqlite3
# 可用 GBT_DB_PATH 改路径、或用 DATABASE_URL 切 PG（由 panel.deps 决定）


def get_db():
    """FastAPI 依赖：返回身体库只读句柄（连接按适配器自身策略复用）。"""
    return _db


def dialect() -> str:
    """sqlite | pg（由身体库适配器自报，不猜）"""
    return getattr(_db, "dialect", "sqlite")


async def fetch_all(conn, sql: str, params=(), *, db=None):
    """统一读路径：`await fetch_all(db, sql, args, db=db)` → list[dict]。"""
    handle = db if db is not None else conn
    return await handle.fetch_all(sql, tuple(params or ()))


async def fetch_one(conn, sql: str, params=(), *, db=None):
    handle = db if db is not None else conn
    rows = await handle.fetch_all(sql, tuple(params or ()))
    return rows[0] if rows else None
