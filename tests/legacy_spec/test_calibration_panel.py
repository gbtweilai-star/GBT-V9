# tests/test_calibration_panel.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 边界（与 LANDING.md 第 4 节一致）：
#   过期永不绿 / null 渲染为未知而不是 0 / ETag 越过过期边界必须变化 /
#   同 updated_at 翻页不重不漏 / 篡改游标或换筛选复用 → 400。
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from panel.routes.calibrations import calibration_status

ALGO = "lab-delta-e76-v1"
POLICY = "policy-v3"


def row(*, expires=10_000, created=1_000, algo=ALGO, policy=POLICY, samples=5):
    return {
        "expires_at": expires,
        "created_at": created,
        "algorithm_version": algo,
        "policy_version": policy,
        "sample_count": samples,
        "encode_fingerprint": "fp-1",
        "op_kind": "color",
    }


def test_status_precedence():
    now = 1000
    assert calibration_status(row(expires=999), now) == "expired"                 # 过期优先
    assert calibration_status(row(expires=2000, algo="old"), now) == "algorithm_stale"
    assert calibration_status(row(expires=2000, policy="old"), now) == "policy_mismatch"
    assert calibration_status(row(expires=2000, samples=2), now) == "insufficient_samples"
    assert calibration_status(row(expires=1100), now) == "expiring_soon"          # 边界
    assert calibration_status(row(expires=999999), now) == "ok"


class FakeDb:
    """最小假库：只覆盖面板端点用到的读路径。"""

    dialect = "sqlite"

    def __init__(self, rows):
        self.rows = list(rows)
        self.now = 1000

    def bind(self, _n):
        return "?"

    async def db_now_epoch(self):
        return self.now

    async def fetch_all(self, _sql, _args=None):
        return list(self.rows)

    async def fetch_one(self, _sql, args=None):
        for r in self.rows:
            if args and r["encode_fingerprint"] == args[-1]:
                return dict(r)
        return None


def _client(rows):
    """构造只挂载 calibrations 路由的最小 ASGI 应用（依赖覆盖注入假库）。"""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from panel.routes import calibrations as mod

    app = FastAPI()
    app.include_router(mod.router, prefix="/api")
    fake = FakeDb(rows)
    app.dependency_overrides[mod.get_db] = lambda: fake
    app.state.fake_db = fake
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_expired_never_ok():
    rows = [
        dict(row(expires=900), project_id="p1", updated_at=1),
        dict(row(expires=999999), project_id="p1", updated_at=2),
    ]
    async with _client(rows) as client:
        r = (await client.get("/api/p1/devour-calibrations")).json()
    assert all(it["status"] != "ok" for it in r["items"] if it["expires_at"] <= r["server_now"])


@pytest.mark.asyncio
async def test_null_renders_unknown_not_zero():
    rows = [dict(row(), project_id="p1", updated_at=1, delta_e_p99=None)]
    async with _client(rows) as client:
        it = (await client.get("/api/p1/devour-calibrations")).json()["items"][0]
    assert it["delta_e_p99"] is None          # 服务端不回 0；前端显示「—（未知）」


@pytest.mark.asyncio
async def test_detail_missing_returns_calibration_required():
    async with _client([]) as client:
        r = (await client.get("/api/p1/devour-calibrations/color/nope")).json()
    assert r["found"] is False and r["status"] == "calibration_required"


@pytest.mark.asyncio
async def test_etag_changes_across_expiry_boundary():
    row_ = dict(row(expires=1100), project_id="p1", updated_at=1)
    async with _client([row_]) as client:
        e1 = (await client.get("/api/p1/devour-calibrations")).headers["etag"]
        # 越界后 ETag 必须变（status 由服务端时钟计算并进入 etag）
        app = client._transport.app  # type: ignore[attr-defined]
        app.state.fake_db.now = 2000
        e2 = (await client.get("/api/p1/devour-calibrations")).headers["etag"]
    assert e1 != e2


@pytest.mark.asyncio
async def test_tampered_cursor_rejected():
    from panel.routes.calibrations import decode_signed_cursor
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        decode_signed_cursor("tampered.token", expected={"project_id": "p1"})
    assert ei.value.status_code == 400
