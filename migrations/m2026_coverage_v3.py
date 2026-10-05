"""创建覆盖率快照存储。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations
from typing import Any

_CREATE = """
CREATE TABLE IF NOT EXISTS coverage_snapshots (
    snapshot_id TEXT PRIMARY KEY NOT NULL,
    backend TEXT NOT NULL,
    generated_epoch BIGINT NOT NULL,
    overall_percent REAL NOT NULL,
    modules_json TEXT NOT NULL,
    files_json TEXT,
    lines_total BIGINT,
    lines_covered BIGINT,
    threshold REAL,
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    commit_sha TEXT,
    run_url TEXT
)
"""
_INDEX = """
CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_backend_epoch
ON coverage_snapshots (backend, generated_epoch DESC)
"""
_REQUIRED = {
    "snapshot_id", "backend", "generated_epoch", "overall_percent",
    "modules_json", "files_json", "lines_total", "lines_covered",
    "threshold", "passed", "commit_sha", "run_url",
}


async def _table_exists(db: Any) -> bool:
    if db.dialect == "sqlite":
        row = await db.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("coverage_snapshots",))
        return row is not None
    if db.dialect in {"postgres", "postgresql"}:
        row = await db.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=?",
            ("coverage_snapshots",))
        return row is not None
    raise RuntimeError(f"unsupported DB dialect: {db.dialect!r}")


async def apply(db: Any) -> None:
    if not await _table_exists(db):
        await db.execute(_CREATE)
    if db.dialect == "sqlite":
        rows = await db.fetch_all("PRAGMA table_info(coverage_snapshots)")
        columns = {row["name"] for row in rows}
    else:
        rows = await db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            ("coverage_snapshots",))
        columns = {row["column_name"] for row in rows}
    missing = _REQUIRED - columns
    if missing:
        raise RuntimeError(f"coverage_snapshots missing columns: {sorted(missing)}")
    await db.execute(_INDEX)
