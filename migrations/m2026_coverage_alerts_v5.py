# migrations/m2026_coverage_alerts_v5.py
"""持久化覆盖率回归告警。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations
from typing import Any

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS coverage_alerts (
    alert_id TEXT PRIMARY KEY NOT NULL,
    backend TEXT NOT NULL,
    commit_sha TEXT,
    previous_commit_sha TEXT,
    overall_percent REAL NOT NULL,
    previous_percent REAL NOT NULL,
    delta_percent REAL NOT NULL,
    drop_threshold REAL NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('regression', 'severe')),
    label TEXT,
    run_url TEXT,
    created_epoch BIGINT NOT NULL,
    acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (acknowledged IN (0, 1))
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_coverage_alerts_backend_epoch "
    "ON coverage_alerts (backend, created_epoch DESC)",
    "CREATE INDEX IF NOT EXISTS idx_coverage_alerts_epoch "
    "ON coverage_alerts (created_epoch DESC)",
)

_REQUIRED = {
    "alert_id", "backend", "commit_sha", "previous_commit_sha",
    "overall_percent", "previous_percent", "delta_percent", "drop_threshold",
    "severity", "label", "run_url", "created_epoch", "acknowledged",
}


async def _columns(db: Any) -> set[str]:
    if db.dialect == "sqlite":
        rows = await db.fetch_all("PRAGMA table_info(coverage_alerts)")
        return {row["name"] for row in rows}
    if db.dialect in {"postgres", "postgresql"}:
        rows = await db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            ("coverage_alerts",))
        return {row["column_name"] for row in rows}
    raise RuntimeError(f"unsupported DB dialect: {db.dialect!r}")


async def apply(db: Any) -> None:
    await db.execute(_CREATE_TABLE)
    missing = _REQUIRED - await _columns(db)
    if missing:
        raise RuntimeError(
            f"coverage_alerts has an incompatible schema; missing: {sorted(missing)}")
    for statement in _INDEXES:
        await db.execute(statement)
