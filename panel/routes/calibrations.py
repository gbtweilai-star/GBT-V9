"""校准列表/详情 + 服务端 status + ETag（游标 HMAC 与帧证据端点同源）。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from panel.deps import db as _default_db

router = APIRouter()


def get_db():
    """默认取 panel.deps 的模块级适配器；测试可用依赖覆盖注入假库。"""
    return _default_db


def filters_of(pid, op_kind=None, algorithm_version=None) -> dict:
    return {"project_id": pid, "op_kind": op_kind, "algorithm_version": algorithm_version}


def _secret() -> bytes:
    return os.getenv("FRAME_EVIDENCE_CURSOR_SECRET", "dev-only-change-this-cursor-secret").encode()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _filters_hash(filters: dict) -> str:
    payload = json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()[:16]


def _make_cursor(updated_at: int, key: tuple, filters: dict) -> str:
    payload = json.dumps(
        {"updated_at": updated_at, "key": list(key), "filters": _filters_hash(filters)},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    signature = hmac.new(_secret(), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def decode_signed_cursor(token: str, *, expected: dict) -> dict:
    """校验签名与筛选一致性；被篡改或换筛选复用 -> 400。"""
    try:
        encoded, signature = token.split(".", 1)
        payload = _unb64(encoded)
        supplied = _unb64(signature)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="malformed_cursor") from exc
    if not hmac.compare_digest(hmac.new(_secret(), payload, hashlib.sha256).digest(), supplied):
        raise HTTPException(status_code=400, detail="cursor_signature_mismatch")
    data = json.loads(payload)
    if data.get("filters") != _filters_hash(expected):
        raise HTTPException(status_code=400, detail="cursor_filters_mismatch")
    return data


def _etag(items: list) -> str:
    payload = json.dumps(items, sort_keys=True, separators=(",", ":"), default=str).encode()
    return '"' + hashlib.sha256(payload).hexdigest()[:32] + '"'


def _cursor(rows: list, limit: int):
    if len(rows) <= limit or not rows:
        return None
    last = rows[limit - 1]
    return _make_cursor(
        last["updated_at"],
        (last["op_kind"], last["algorithm_version"], last["encode_fingerprint"]),
        filters_of(last["project_id"], last.get("op_kind"), last.get("algorithm_version")),
    )
# panel/routes/calibrations.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: status 服务端算(用服务端时钟+当前算法/策略版本); null 渲染为未知, 绝不置 0;
#       过期永不绿; 找不到校准显示 calibration_required, 不伪造零值行。
CURRENT_ALGORITHM, CURRENT_POLICY, MIN_SAMPLES = "lab-delta-e76-v1", "policy-v3", 5


def calibration_status(row, now, *, algorithm=CURRENT_ALGORITHM,
                       policy=CURRENT_POLICY, min_samples=MIN_SAMPLES) -> str:
    if row["expires_at"] <= now:                      return "expired"
    if row["algorithm_version"] != algorithm:         return "algorithm_stale"
    if row["policy_version"] != policy:               return "policy_mismatch"
    if row["sample_count"] < min_samples:             return "insufficient_samples"
    ttl = max(1, row["expires_at"] - row["created_at"])
    soon_window = min(ttl * 0.5, max(86400, ttl * 0.20))   # 剩余 <20% 或 <24h → 即将过期
    if row["expires_at"] - now <= soon_window:        return "expiring_soon"
    return "ok"

@router.get("/{pid}/devour-calibrations")
async def list_calibrations(pid, op_kind=None, algorithm_version=None, limit=50, cursor=None,
                            db=Depends(get_db)):
    now = await db.db_now_epoch()
    where, args = ["project_id = " + db.bind(1)], [pid]
    if op_kind:            args.append(op_kind);            where.append(f"op_kind = {db.bind(len(args))}")
    if algorithm_version:  args.append(algorithm_version);  where.append(f"algorithm_version = {db.bind(len(args))}")
    after = decode_signed_cursor(cursor, expected=filters_of(pid, op_kind, algorithm_version)) if cursor else None
    if after:  # updated_at DESC, (op,algo,fp) 双键翻页
        args += [after["updated_at"], after["updated_at"], *after["key"]]; n = len(args)
        where.append(f"(updated_at < {db.bind(n-3)} OR (updated_at = {db.bind(n-2)} "
                     f"AND (op_kind, algorithm_version, encode_fingerprint) > "
                     f"({db.bind(n-1)}, {db.bind(n)}, {db.bind(n+1)})))")
    args.append(limit + 1)
    rows = await db.fetch_all(f"""SELECT * FROM devour_calibrations WHERE {' AND '.join(where)}
        ORDER BY updated_at DESC, op_kind, algorithm_version, encode_fingerprint
        LIMIT {db.bind(len(args))}""", args)
    items = [{**r, "status": calibration_status(r, now),
              "remaining_seconds": max(0, r["expires_at"] - now)} for r in rows]
    etag = _etag(items)                                    # 含 status → 过期越过边界会变
    return JSONResponse({"items": items, "server_now": now, "next_cursor": _cursor(rows, limit)},
                        headers={"ETag": etag, "Cache-Control": "private, no-cache"})

@router.get("/{pid}/devour-calibrations/{op_kind}/{encode_fingerprint}")
async def calibration_detail(pid: str, op_kind: str, encode_fingerprint: str,
                             db=Depends(get_db)) -> JSONResponse:
    """单条校准详情；找不到回 calibration_required，绝不伪造零值行。"""
    now = await db.db_now_epoch()
    row = await db.fetch_one(
        "SELECT * FROM devour_calibrations"
        " WHERE project_id=? AND op_kind=? AND encode_fingerprint=?",
        (pid, op_kind, encode_fingerprint),
    )
    if row is None:
        return JSONResponse({"found": False, "status": "calibration_required",
                             "server_now": now}, status_code=200)
    item = {**row, "status": calibration_status(row, now),
            "remaining_seconds": max(0, row["expires_at"] - now)}
    return JSONResponse({"found": True, "item": item, "server_now": now},
                        headers={"ETag": _etag([item])})
