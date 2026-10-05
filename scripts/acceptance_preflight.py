#!/usr/bin/env python3
"""帧证据契约迁移/建表 preflight。"""
from __future__ import annotations
import asyncio, inspect, os, sys, tempfile
from pathlib import Path
from typing import Any

_REQUIRED = (
    "body.adapters.sqlite_db.SqliteDb",
    "body.adapters.postgres_db.PostgresDb",
    "body.evidence_leases.EvidenceLeases",
    "body.evict.Evictor",
    "body.evict.StagingReclaimer",
    "migrations.runner.apply_pending",
    "migrations.runner.boot_check",
)
try:
    from body.adapters.sqlite_db import SqliteDb
    from body.adapters.postgres_db import PostgresDb
    from body.evidence_leases import EvidenceLeases
    from body.evict import Evictor, StagingReclaimer
    from migrations.runner import apply_pending, boot_check
except ImportError as exc:
    print(f"PREFLIGHT IMPORT FAIL: {exc}\nRequired imports: {', '.join(_REQUIRED)}\n"
          "Likely cause: YOUR_APP 适配器路径未接上，或缺依赖。", file=sys.stderr)
    raise SystemExit(3)

TABLES = ("artifact_objects", "frame_leases", "frame_verification_evidence")

async def _resolve(v: Any) -> Any:
    return await v if inspect.isawaitable(v) else v

async def _close(db: Any) -> None:
    closer = getattr(db, "aclose", None) or getattr(db, "close", None)
    if closer is not None:
        await _resolve(closer())

async def _open_db(backend: str, sqlite_path: str | None = None) -> Any:
    if backend == "sqlite":
        return await _resolve(SqliteDb.open(sqlite_path))
    dsn = os.environ.get("TEST_DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError("TEST_DATABASE_URL required for postgres preflight")
    if "?" in dsn or "#" in dsn:
        raise RuntimeError("TEST_DATABASE_URL 不能带 query/fragment（asyncpg 不认 sslmode）")
    return await _resolve(PostgresDb.connect(dsn, min_size=1, max_size=2))

async def _run(backend: str, sqlite_path: str | None = None) -> None:
    db = None; phase = "database open"
    try:
        db = await _open_db(backend, sqlite_path)
        phase = "apply_pending"; applied = await apply_pending(db)
        phase = "boot_check"; await boot_check(db)
        counts: list[str] = []
        for table in TABLES:
            phase = f"table check: {table}"
            await db.fetch_one(f"SELECT 1 FROM {table} WHERE 1=0")
            row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table}")
            counts.append(f"{table}={int(row['n'])}")
        print(f"MIGRATIONS ({backend}): applied={applied}")
        print(f"TABLES ({backend}): {', '.join(TABLES)}")
        print(f"ROW COUNTS ({backend}): {', '.join(counts)}")
        print(f"PREFLIGHT OK ({backend})")
    except Exception as exc:
        print(f"PREFLIGHT FAIL ({backend}) during {phase}: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        raise SystemExit(1) from exc
    finally:
        if db is not None:
            await _close(db)

def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"sqlite", "postgres"}:
        print("Usage: acceptance_preflight.py sqlite|postgres", file=sys.stderr)
        return 2
    backend = sys.argv[1]
    if backend == "postgres":
        asyncio.run(_run(backend)); return 0
    fd, path = tempfile.mkstemp(prefix="frame-evidence-preflight-", suffix=".sqlite")
    os.close(fd)
    try:
        asyncio.run(_run(backend, path))
    finally:
        for suffix in ("", "-wal", "-shm"):
            Path(path + suffix).unlink(missing_ok=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
