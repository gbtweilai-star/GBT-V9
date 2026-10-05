# native-capabilities/ledger_schema.py —— 离线验收所需账本表（SQLite / PG 共用）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 说明：本模块只执行静态 DDL 常量（仓库内字面量，无任何用户输入参与构造）。
from __future__ import annotations

from typing import Any

TABLES: tuple[tuple[str, str], ...] = (
    ("workflow_runs", """
        CREATE TABLE IF NOT EXISTS workflow_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workflow TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            node_count INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("agent_runs", """
        CREATE TABLE IF NOT EXISTS agent_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            iterations INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("cross_scan_results", """
        CREATE TABLE IF NOT EXISTS cross_scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target TEXT NOT NULL,
            scanner TEXT NOT NULL,
            peer TEXT NOT NULL DEFAULT '',
            verdict TEXT NOT NULL,
            findings INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("scan_coverage", """
        CREATE TABLE IF NOT EXISTS scan_coverage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            root TEXT NOT NULL,
            total_targets INTEGER NOT NULL,
            scanned_targets INTEGER NOT NULL,
            missing_targets INTEGER NOT NULL,
            coverage REAL NOT NULL,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("web_scrape_runs", """
        CREATE TABLE IF NOT EXISTS web_scrape_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            status TEXT NOT NULL,
            items INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("voice_events", """
        CREATE TABLE IF NOT EXISTS voice_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            say TEXT NOT NULL DEFAULT '',
            dedupe_key TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("media_job_events", """
        CREATE TABLE IF NOT EXISTS media_job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            status TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            detail TEXT NOT NULL DEFAULT '{}',
            created_epoch REAL NOT NULL
        )"""),
    ("native_artifacts", """
        CREATE TABLE IF NOT EXISTS native_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            operation TEXT NOT NULL,
            path TEXT NOT NULL,
            bytes INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            created_epoch REAL NOT NULL
        )"""),
)

_DDL_ALL = ";\n".join(ddl.strip() for _, ddl in TABLES) + ";"


def apply_sync(conn: Any) -> None:
    """同步建表（sqlite3 连接）。DDL 为静态常量，用 executescript 执行。"""
    conn.executescript(_DDL_ALL)
    if hasattr(conn, "commit"):
        conn.commit()


async def apply(db: Any) -> None:
    """异步建表（V9 账本适配器 / PG）。"""
    for _, ddl in TABLES:
        await db.execute(ddl)


__all__ = ["TABLES", "apply", "apply_sync"]
