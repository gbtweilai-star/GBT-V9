"""帧证据面板接口。

假设：
- panel.deps 暴露已初始化的模块级 db 与 leases。
- SQL 用 `?`；PostgresDb 会翻译成 $n。
- delta_kernel.heatmap_png(baseline_png, result_png, metrics=...) 返回 PNG bytes。
- operation 过滤启用前先跑迁移：
  ALTER TABLE frame_verification_evidence
      ADD COLUMN operation TEXT NOT NULL DEFAULT 'legacy';
"""
from __future__ import annotations

import base64, hashlib, hmac, inspect, json, os
from typing import Any, AsyncIterator, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response, StreamingResponse

from body.evidence_leases import EvidenceUnavailable, LeaseLost
from body.delta_kernel import heatmap_png
from panel.deps import db, leases

router = APIRouter()
JSON_COLUMNS = (
    "verification_times", "color_metrics_delta", "effect_metrics",
    "delta_metrics", "metrics", "verdict_and_spec",
)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value        # 非法 JSON 原样返回，读不崩


def _decode(row: Any) -> dict[str, Any]:
    result = dict(row)
    for key in JSON_COLUMNS:
        if key in result:
            result[key] = _json_value(result[key])
    return result


def _filters_hash(filters: dict[str, Any]) -> str:
    raw = json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _cursor_secret() -> bytes:
    return os.environ.get(
        "FRAME_EVIDENCE_CURSOR_SECRET", "dev-only-change-this-cursor-secret"
    ).encode()


def _make_cursor(created_epoch: int, evidence_id: str, filters_hash: str) -> str:
    payload = json.dumps(
        {"ck": created_epoch, "id": evidence_id, "f": filters_hash},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    signature = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def _read_cursor(token: str, filters_hash: str) -> tuple[int, str]:
    try:
        encoded, signature = token.split(".", 1)
        payload = _unb64(encoded)
        supplied = _unb64(signature)
        expected = hmac.new(_cursor_secret(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("signature mismatch")
        data = json.loads(payload)
        if data.get("f") != filters_hash:
            raise ValueError("cursor belongs to different filters")
        return int(data["ck"]), str(data["id"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(400, detail="invalid_or_mismatched_cursor") from exc


async def _get_evidence(evidence_id: str) -> dict[str, Any]:
    row = await db.fetch_one(
        "SELECT * FROM frame_verification_evidence WHERE id=?", (evidence_id,))
    if row is None:
        raise HTTPException(404, detail="evidence_not_found")
    return _decode(row)


async def _is_ready(ref: str | None) -> bool:
    if not ref:
        return False
    row = await db.fetch_one(
        "SELECT ref FROM artifact_objects WHERE ref=? AND state='ready'", (ref,))
    return row is not None


async def _artifact_evicted() -> None:
    raise HTTPException(status_code=409, detail={"reason": "artifact_evicted"})


async def _read_stream(session: Any, ref: str) -> bytes:
    chunks = bytearray()
    async for chunk in session.iter_bytes(ref):
        chunks.extend(chunk)
    return bytes(chunks)


@router.get("/frame-evidence")
async def list_frame_evidence(
    limit: int = Query(50, ge=1, le=100),
    cursor: str | None = None,
    calibration_key: str | None = None,
    raw_available: int | None = Query(None, ge=0, le=1),
    ref: str | None = None,
    operation: str | None = None,
) -> dict[str, Any]:
    filters = {"calibration_key": calibration_key, "raw_available": raw_available,
               "ref": ref, "operation": operation}
    filter_hash = _filters_hash(filters)
    clauses: list[str] = []
    params: list[Any] = []

    if calibration_key is not None:
        clauses.append("calibration_key=?"); params.append(calibration_key)
    if raw_available is not None:
        clauses.append("raw_available=?"); params.append(raw_available)
    if ref is not None:
        clauses.append("(ref=? OR baseline_ref=? OR result_ref=?)")
        params.extend((ref, ref, ref))
    if operation is not None:
        clauses.append("operation=?"); params.append(operation)
    if cursor:
        ck, eid = _read_cursor(cursor, filter_hash)
        clauses.append("(created_epoch<? OR (created_epoch=? AND id<?))")
        params.extend((ck, ck, eid))

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = await db.fetch_all(
        "SELECT * FROM frame_verification_evidence "
        f"{where} ORDER BY created_epoch DESC,id DESC LIMIT ?",
        tuple(params + [limit + 1]),
    )

    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _make_cursor(int(last["created_epoch"]), str(last["id"]), filter_hash)

    return {"items": [_decode(r) for r in page],
            "next_cursor": next_cursor, "has_more": has_more}


@router.get("/frame-evidence/{evidence_id}")
async def frame_evidence_detail(evidence_id: str) -> dict[str, Any]:
    item = await _get_evidence(evidence_id)
    baseline_ready = await _is_ready(item.get("baseline_ref"))
    result_ready = await _is_ready(item.get("result_ref"))
    item["frames_available"] = (
        int(item.get("raw_available") or 0) == 1 and baseline_ready and result_ready)
    return item


@router.post("/frame-evidence/{evidence_id}/frame")
async def get_frame(
    evidence_id: str, kind: Literal["baseline", "result"],
) -> StreamingResponse:
    item = await _get_evidence(evidence_id)
    ref = item.get("baseline_ref" if kind == "baseline" else "result_ref")
    if int(item.get("raw_available") or 0) != 1 or not await _is_ready(ref):
        await _artifact_evicted()

    session = leases.hold([ref], holder="panel_preview", purpose="panel_preview")
    try:
        await session.__aenter__()
    except (EvidenceUnavailable, LeaseLost, FileNotFoundError) as exc:
        raise HTTPException(409, detail={"reason": "artifact_evicted"}) from exc

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in session.iter_bytes(ref):
                yield chunk
        finally:
            await session.__aexit__(None, None, None)   # 结束或中断都释放

    return StreamingResponse(body(), media_type="image/png",
                             headers={"Cache-Control": "no-store"})


@router.get("/frame-evidence/{evidence_id}/diff")
async def get_frame_diff(evidence_id: str) -> Response:
    item = await _get_evidence(evidence_id)
    baseline_ref, result_ref = item.get("baseline_ref"), item.get("result_ref")
    if (int(item.get("raw_available") or 0) != 1
            or not await _is_ready(baseline_ref) or not await _is_ready(result_ref)):
        await _artifact_evicted()

    session = leases.hold([baseline_ref, result_ref],
                          holder="panel_diff", purpose="panel_diff")
    try:
        await session.__aenter__()
    except (EvidenceUnavailable, LeaseLost, FileNotFoundError) as exc:
        raise HTTPException(409, detail={"reason": "artifact_evicted"}) from exc

    try:
        baseline_png = await _read_stream(session, baseline_ref)
        result_png = await _read_stream(session, result_ref)
        metrics = item.get("delta_metrics") or item.get("metrics") or item.get("effect_metrics")
        generated = heatmap_png(baseline_png, result_png, metrics=metrics)
        if inspect.isawaitable(generated):
            generated = await generated
        if not isinstance(generated, (bytes, bytearray)):
            raise TypeError("heatmap_png must return PNG bytes")
        return Response(content=bytes(generated), media_type="image/png",
                        headers={"Cache-Control": "no-store"})
    finally:
        await session.__aexit__(None, None, None)
