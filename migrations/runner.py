"""幂等、方言感知的迁移执行器。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

from typing import Any

from migrations.ddl import MIGRATIONS as DDL_MIGRATIONS
from migrations.m2026_coverage_alerts_v5 import apply as apply_coverage_alerts_v5
from migrations.m2026_coverage_label_v4 import apply as apply_coverage_label_v4
from migrations.m2026_coverage_v3 import apply as apply_coverage_v3
from migrations.m2026_devour_calibrations_v7 import apply as apply_devour_calibrations_v7
from migrations.m2026_emotion_v6 import apply as apply_emotion_v6
from migrations.m2026_body_chain_v8 import TABLES as BODY_CHAIN_TABLES
from migrations.m2026_body_bindings_v9 import TABLES as BODY_BINDING_TABLES
from migrations.m2026_witness_tools_v10 import TABLES as WITNESS_TOOL_TABLES
from migrations.m2026_witness_runtime_v11 import TABLES as WITNESS_RUNTIME_TABLES
from migrations.m2026_operation_v2 import apply as apply_operation_v2

# (version, name, statements-or-callable)；顺序即执行顺序。
MIGRATIONS: list[tuple[int, str, Any]] = [
    *DDL_MIGRATIONS,
    (2, "frame_evidence_operation_v2", apply_operation_v2),
    (3, "coverage_snapshots_v3", apply_coverage_v3),
    (4, "coverage_label_v4", apply_coverage_label_v4),
    (5, "coverage_alerts_v5", apply_coverage_alerts_v5),
    (6, "emotion_voice_state_v6", apply_emotion_v6),
    (7, "devour_calibrations_v7", apply_devour_calibrations_v7),
    (8, "body_chain_v8", BODY_CHAIN_TABLES),
    (9, "body_bindings_v9", BODY_BINDING_TABLES),
    (10, "witness_tools_v10", WITNESS_TOOL_TABLES),
    (11, "witness_runtime_v11", WITNESS_RUNTIME_TABLES),
]


async def _run_migration(db: Any, statements: Any) -> None:
    """兼容两种形态：async 可调用（自带幂等）或 SQL 语句序列（仓库内静态 DDL）。"""
    if callable(statements):
        await statements(db)
        return
    for ddl_constant in statements:
        await db.execute(ddl_constant)

_SCHEMA_MIGRATIONS_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY NOT NULL,
    name TEXT NOT NULL,
    applied_epoch BIGINT NOT NULL
)
"""

_REQUIRED_TABLES = (
    "artifact_objects",
    "frame_leases",
    "frame_verification_evidence",
)


def _transaction(db: Any):
    return db.transaction(immediate=(db.dialect == "sqlite"))


async def _ensure_migration_table(db: Any) -> None:
    async with _transaction(db):
        await db.lock_key("migrations")     # PG 事务级 advisory lock
        await db.execute(_SCHEMA_MIGRATIONS_SQL)


async def apply_pending(db: Any) -> list[int]:
    """按序执行待应用迁移；每个迁移一个事务；可重复调用。"""
    await _ensure_migration_table(db)
    applied_now: list[int] = []

    for version, name, statements in sorted(MIGRATIONS, key=lambda it: it[0]):
        async with _transaction(db):
            # 拿锁后再复查版本，避免并发重复应用
            await db.lock_key("migrations")
            existing = await db.fetch_one(
                "SELECT version FROM schema_migrations WHERE version=?",
                (version,),
            )
            if existing is not None:
                continue

            await _run_migration(db, statements)

            epoch = await db.db_now_epoch()
            await db.execute(
                "INSERT INTO schema_migrations (version, name, applied_epoch) "
                "VALUES (?, ?, ?)",
                (version, name, epoch),
            )
            applied_now.append(version)

    return applied_now


async def boot_check(db: Any) -> None:
    """校验三张表都在、且已应用到期望版本；不满足就炸。"""
    for table in _REQUIRED_TABLES:
        try:
            await db.fetch_one(f"SELECT 1 FROM {table} WHERE 1=0")
        except Exception as exc:
            raise RuntimeError(
                f"boot check failed: required frame-evidence table is missing: {table}"
            ) from exc

    try:
        row = await db.fetch_one(
            "SELECT MAX(version) AS max_version FROM schema_migrations"
        )
    except Exception as exc:
        raise RuntimeError(
            "boot check failed: schema_migrations is missing or unreadable"
        ) from exc

    actual = None if row is None else row["max_version"]
    expected = max(version for version, _, _ in MIGRATIONS)
    if actual is None or int(actual) < expected:
        raise RuntimeError(
            f"boot check failed: applied migration version {actual!r}; "
            f"expected at least {expected}"
        )
