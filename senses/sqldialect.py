# senses/sqldialect.py —— 传感器 SQL 方言适配：同一套代码跑 sqlite / pg 两种账本
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律（对齐安全规约）：所有外部输入一律参数绑定；本模块只统一"占位符/时间函数/
# 列类型"这类**常量**差异，绝不参与含外部输入的 SQL 组装。
from contextlib import contextmanager

_PG = "pg"


def is_pg(led) -> bool:
    """账本后端判定：Ledger.backend == 'sqlite' · PGLedger.backend == 'pg'"""
    return getattr(led, "backend", "sqlite") == _PG


@contextmanager
def cur(conn):
    """cursor 上下文：sqlite3.Cursor 不带上下文管理（psycopg2 带），统一手工关闭。"""
    c = conn.cursor()
    try:
        yield c
    finally:
        try:
            c.close()
        except Exception:
            pass


@contextmanager
def txn(led, write=False):
    """账本事务 + 游标一步到位：with txn(self.led) as cur: ..."""
    with led._tx(write=write) as c, cur(c) as cu:
        yield cu
