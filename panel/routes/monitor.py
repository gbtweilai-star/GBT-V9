# panel/routes/monitor.py —— 会话三态 / 账本健康 / 媒体队列（53 项能力验收的 api_response 面）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 三个只读接口：
#   GET /api/session             会话三态（unknown / anonymous / user）
#   GET /api/monitor/db-health   账本与库健康（真读数：迁移版本/证据行数/库体积）
#   GET /api/media/queue?limit=N 媒体任务队列快照（表缺失时诚实返回 table_missing=true）
from __future__ import annotations
from core.swallow import swallow as _swallow

import os
from pathlib import Path

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/api/session")
async def session_state(request: Request) -> dict:
    """三态会话：没有任何凭据 → unknown；有凭据但无鉴权后端 → anonymous；
    命中 V9_SESSION_TOKEN 环境变量 → user（离线验收用，不落任何密钥字面量）。"""
    header = request.headers.get("authorization", "")
    token = header[7:].strip() if header.lower().startswith("bearer ") else request.query_params.get("access_token")
    if not token:
        return {"state": "unknown", "user": None}
    expected = os.environ.get("V9_SESSION_TOKEN")
    if expected and token == expected:
        return {"state": "user", "user": os.environ.get("V9_SESSION_USER", "operator")}
    return {"state": "anonymous", "user": None}


def _db():
    from panel.deps import db as panel_db

    return panel_db


@router.get("/api/monitor/db-health")
async def db_health() -> dict:
    """账本/库健康：真读数；任何读失败都降级为 degraded 而不是 500。"""
    db = _db()
    out: dict = {"dialect": getattr(db, "dialect", "unknown"), "ok": True}
    try:
        row = await db.fetch_one("SELECT MAX(version) AS v FROM schema_migrations")
        out["schema_version"] = None if row is None else row["v"]
    except Exception as exc:  # noqa: BLE001
        out["schema_version"] = None
        out["schema_note"] = type(exc).__name__
    try:
        row = await db.fetch_one("SELECT COUNT(*) AS n FROM frame_verification_evidence")
        out["evidence_rows"] = None if row is None else int(row["n"])
    except Exception:  # noqa: BLE001
        out["evidence_rows"] = None
    try:
        path = getattr(db, "path", None)
        if path and Path(str(path)).exists():
            out["db_bytes"] = Path(str(path)).stat().st_size
    except Exception as e:
        _swallow(__file__, e)
    if out.get("schema_version") is None:
        out["ok"] = False
        out["status"] = "degraded"
    else:
        out["status"] = "ok"
    return out


@router.get("/api/media/queue")
async def media_queue(limit: int = Query(10, ge=1, le=100)) -> dict:
    """媒体任务队列快照（media_job_events）。表缺失时 table_missing=true，不 500。"""
    db = _db()
    try:
        rows = await db.fetch_all(
            "SELECT job_id, operation, status, priority, created_epoch "
            "FROM media_job_events ORDER BY id DESC LIMIT ?",
            (int(limit),),
        )
        return {"items": [dict(r) for r in rows], "table_missing": False}
    except Exception:  # noqa: BLE001
        return {"items": [], "table_missing": True}


__all__ = ["router"]
