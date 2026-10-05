# migrations/m2026_devour_calibrations_v7.py
"""ΔE 噪声门限标定表（devour_calibrations）—— 对话「一、表 DDL」原文落地。
dev: 自由的风 · 本署名不可删除、不可篡改归属

安全说明：本模块只执行下列静态 DDL 常量；带值查询一律使用占位符参数化。
"""
from __future__ import annotations

from typing import Any

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS devour_calibrations (
  project_id          TEXT NOT NULL,
  op_kind             TEXT NOT NULL,
  algorithm_version   TEXT NOT NULL,
  encode_fingerprint  TEXT NOT NULL,
  noise_threshold     REAL NOT NULL,
  effect_floor        REAL NOT NULL,
  outside_max         REAL NOT NULL,
  local_ratio         REAL NOT NULL,
  sample_count        INTEGER NOT NULL,
  delta_e_p50         REAL NOT NULL,
  delta_e_p99         REAL NOT NULL,
  noise_fraction_p99  REAL NOT NULL,
  policy_version      TEXT NOT NULL,
  created_at BIGINT NOT NULL,
  updated_at BIGINT NOT NULL,
  expires_at BIGINT NOT NULL,
  PRIMARY KEY (project_id, op_kind, algorithm_version, encode_fingerprint)
)
"""

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_devour_cal_expiry "
    "ON devour_calibrations(expires_at)",
    "CREATE INDEX IF NOT EXISTS ix_devour_cal_updated "
    "ON devour_calibrations(updated_at DESC)",
)

_REQUIRED = {
    "project_id", "op_kind", "algorithm_version", "encode_fingerprint",
    "noise_threshold", "effect_floor", "outside_max", "local_ratio",
    "sample_count", "delta_e_p50", "delta_e_p99", "noise_fraction_p99",
    "policy_version", "created_at", "updated_at", "expires_at",
}


async def _columns(db: Any) -> set[str]:
    if str(db.dialect) == "sqlite":
        rows = await db.fetch_all("PRAGMA table_info(devour_calibrations)")
    else:
        rows = await db.fetch_all(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            ("devour_calibrations",),
        )
    return {r["name"] if isinstance(r, dict) else r["column_name"] for r in rows}


async def apply(db: Any) -> None:
    await db.execute(_CREATE_TABLE)
    missing = _REQUIRED - await _columns(db)
    if missing:
        raise RuntimeError(
            f"devour_calibrations has an incompatible schema; missing: {sorted(missing)}"
        )
    for statement in _INDEXES:
        await db.execute(statement)
