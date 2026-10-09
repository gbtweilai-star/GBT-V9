"""读写帧验证证据行。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any
from core.swallow import swallow as _swallow

_JSON_COLUMNS = (
    "verification_times", "color_metrics_delta", "effect_metrics",
    "delta_metrics", "metrics", "verdict_and_spec",
)

_INSERT_COLUMNS = (
    "id", "ref", "baseline_ref", "result_ref", "raw_available",
    "verification_times", "color_metrics_delta", "effect_metrics",
    "delta_metrics", "metrics", "verdict_and_spec", "created_epoch",
    "calibration_key", "calibration_fingerprint", "fingerprint",
)

_INSERT_SQL = (
    f"INSERT INTO frame_verification_evidence ({','.join(_INSERT_COLUMNS)}) "
    f"VALUES ({','.join('?' for _ in _INSERT_COLUMNS)})"
)

# SQLite 与 PG 都支持 ON CONFLICT (id) DO UPDATE。
# 刻意不更新 created_epoch / raw_available：upsert 不能把创建时间重置，
# 也不能让已被驱逐(不可用)的证据重新变回"可用"。
_UPSERT_SQL = _INSERT_SQL + """
ON CONFLICT (id) DO UPDATE SET
    ref=excluded.ref,
    baseline_ref=excluded.baseline_ref,
    result_ref=excluded.result_ref,
    verification_times=excluded.verification_times,
    color_metrics_delta=excluded.color_metrics_delta,
    effect_metrics=excluded.effect_metrics,
    delta_metrics=excluded.delta_metrics,
    metrics=excluded.metrics,
    verdict_and_spec=excluded.verdict_and_spec,
    calibration_key=excluded.calibration_key,
    calibration_fingerprint=excluded.calibration_fingerprint,
    fingerprint=excluded.fingerprint
"""


def _active_connection(db: Any) -> Any | None:
    """取参考适配器的事务绑定连接；没有活动事务则返回 None。"""
    context = getattr(db, "_current_connection", None)
    if context is not None:
        return context.get()
    checker = getattr(db, "_require_transaction", None)
    if callable(checker):
        return checker()
    if getattr(db, "in_transaction", False):
        return True
    return None


def _require_transaction(db: Any, conn: Any | None) -> None:
    active = _active_connection(db)
    if active is None:
        raise RuntimeError(
            "write_evidence requires an active caller-owned db.transaction()"
        )
    if conn is not None and active is not True and conn is not active:
        raise RuntimeError("conn is not the active transaction connection")


def _json_text(value: Any) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str)


def _decode_row(row: Any) -> dict[str, Any]:
    result = dict(row)
    for column in _JSON_COLUMNS:
        value = result.get(column)
        if value is None or not isinstance(value, str):
            continue
        try:
            result[column] = json.loads(value)
        except (json.JSONDecodeError, TypeError) as e:
            _swallow(__file__, e)
  # 旧数据非法 JSON 原样保留，不因读失败崩。
    return result


async def write_evidence(
    db: Any,
    *,
    ref: str | None,
    baseline_ref: str | None,
    result_ref: str | None,
    verdict_and_spec: Any = None,
    color_metrics_delta: Any = None,
    effect_metrics: Any = None,
    delta_metrics: Any = None,
    metrics: Any = None,
    verification_times: Any = None,
    calibration_key: str | None = None,
    calibration_fingerprint: str | None = None,
    fingerprint: str | None = None,
    raw_available: int = 1,
    conn: Any = None,
    id: str | None = None,
) -> str:
    """在调用方事务内插入证据；传 id 时走 upsert。返回 evidence id。"""
    _require_transaction(db, conn)
    if raw_available != 1:
        raise ValueError("write_evidence only creates initially available evidence")

    evidence_id = id or uuid.uuid4().hex
    created_epoch = await db.db_now_epoch()
    times = [] if verification_times is None else verification_times

    values = (
        evidence_id, ref, baseline_ref, result_ref, 1,
        _json_text(times), _json_text(color_metrics_delta),
        _json_text(effect_metrics), _json_text(delta_metrics),
        _json_text(metrics), _json_text(verdict_and_spec), created_epoch,
        calibration_key, calibration_fingerprint, fingerprint,
    )
    await db.execute(_UPSERT_SQL if id is not None else _INSERT_SQL, values)
    return evidence_id


async def read_evidence(
    db: Any, *, id: str | None = None, ref: str | None = None
) -> dict[str, Any] | None:
    if id is not None:
        row = await db.fetch_one(
            "SELECT * FROM frame_verification_evidence WHERE id=?", (id,))
    elif ref is not None:
        row = await db.fetch_one(
            "SELECT * FROM frame_verification_evidence "
            "WHERE ref=? OR baseline_ref=? OR result_ref=? "
            "ORDER BY created_epoch DESC,id DESC LIMIT 1", (ref, ref, ref))
    else:
        raise ValueError("provide id or ref")
    return None if row is None else _decode_row(row)


async def list_evidence_for_ref(
    db: Any, ref: str, *, limit: int = 50
) -> list[dict[str, Any]]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    rows = await db.fetch_all(
        "SELECT * FROM frame_verification_evidence "
        "WHERE ref=? OR baseline_ref=? OR result_ref=? "
        "ORDER BY created_epoch DESC,id DESC LIMIT ?",
        (ref, ref, ref, limit),
    )
    return [_decode_row(row) for row in rows]


@asynccontextmanager
async def _transaction_if_needed(db: Any):
    if _active_connection(db) is not None:
        yield
    else:
        async with db.transaction(immediate=(db.dialect == "sqlite")):
            yield


async def attach_verification_time(db: Any, id: str, entry: Any) -> None:
    """原子追加一条 verification_times（读-改-写在一个事务内）。"""
    async with _transaction_if_needed(db):
        await db.lock_key(f"frame-evidence:{id}")
        row = await db.fetch_one(
            "SELECT verification_times FROM frame_verification_evidence WHERE id=?",
            (id,),
        )
        if row is None:
            raise KeyError(f"evidence row not found: {id}")

        current = row["verification_times"]
        if current is None:
            times = []
        elif isinstance(current, str):
            try:
                times = json.loads(current)
            except json.JSONDecodeError as exc:
                raise ValueError("verification_times is invalid JSON") from exc
        else:
            times = current
        if not isinstance(times, list):
            raise ValueError("verification_times must contain a JSON array")

        times.append(entry)
        await db.execute(
            "UPDATE frame_verification_evidence SET verification_times=? WHERE id=?",
            (_json_text(times), id),
        )
