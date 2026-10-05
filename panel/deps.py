"""面板依赖：模块级 DB 适配器与租约服务（与 backend 报告同源）。
dev: 自由的风 · 本署名不可删除、不可篡改归属

用法：`from panel.deps import db, leases`

- 后端选择：设置了 DATABASE_URL 环境变量 → Postgres(asyncpg)；
  否则 SQLite 文件（默认 data/gbt_v9.sqlite3，可用 GBT_DB_PATH 覆盖）。
  LEDGER_BACKEND=sqlite 可强制走 SQLite（即使有 DSN）。
- 工件存储：设置了 R2 相关环境变量 → R2ArtifactStore（本地+远端双写）；
  否则本地内容寻址存储（root 由 GBT_STORE_ROOT 指定，默认 data/artifacts）。
- 凭据只从环境变量读取；源码中不出现任何真实密钥字面量。
"""
from __future__ import annotations

import os
from pathlib import Path

from body.adapters.local_store import LocalArtifactStore
from body.adapters.postgres_db import PostgresDb
from body.adapters.sqlite_db import SqliteDb
from body.evidence_leases import EvidenceLeases
from body.net_guard import assert_safe_outbound_url

_DB_PATH_DEFAULT = str(Path("data") / "gbt_v9.sqlite3")
_STORE_ROOT_DEFAULT = str(Path("data") / "artifacts")
_LEASE_TTL_S = int(os.getenv("LEASE_TTL_S", "120"))
_STAGING_TTL_S = int(os.getenv("STAGING_TTL_S", "900"))


def _build_db():
    dsn = os.getenv("DATABASE_URL")
    backend = os.getenv("LEDGER_BACKEND", "").strip().lower()
    if dsn and backend != "sqlite":
        return PostgresDb(
            dsn,
            min_size=int(os.getenv("PG_MINCONN", "1")),
            max_size=int(os.getenv("PG_MAXCONN", "10")),
        )
    path = os.getenv("GBT_DB_PATH", _DB_PATH_DEFAULT)
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return SqliteDb(path)


def _build_store():
    local = LocalArtifactStore(os.getenv("GBT_STORE_ROOT", _STORE_ROOT_DEFAULT))
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    bucket = os.getenv("R2_BUCKET")
    if not (account and bucket):
        return local
    endpoint = os.getenv("R2_ENDPOINT_URL") or f"https://{account}.r2.cloudflarestorage.com"
    try:
        assert_safe_outbound_url(endpoint)   # 拒绝环回/私有/保留地址（fail-closed）
    except ValueError:
        return local                          # 端点不合规 → 只用本地内容寻址存储
    try:
        import boto3  # 可选依赖：未安装时退回纯本地存储

        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
        )
        from body.adapters.r2_store import R2ArtifactStore

        return R2ArtifactStore(local, client, bucket)
    except Exception:
        return local


db = _build_db()
store = _build_store()
leases = EvidenceLeases(db, store, ttl_s=_LEASE_TTL_S, staging_ttl_s=_STAGING_TTL_S)

__all__ = ["db", "store", "leases"]
