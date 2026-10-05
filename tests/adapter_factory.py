# tests/adapter_factory.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
"""唯一的契约测试接线点：SQLite/PostgreSQL 真数据库 + 可替换字节存储。"""

from __future__ import annotations

import hashlib
import inspect
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol


AlertFn = Callable[[str, dict], Awaitable[None]]


class AdapterBundle(Protocol):
    """后端接线：打开数据库、构造服务、执行迁移并关闭数据库。"""

    backend: str

    async def open_db(self, dsn: str) -> Any: ...

    def make_services(
        self, db: Any, store: Any, alert: AlertFn
    ) -> tuple[Any, Any, Any]:
        """返回共用同一 db/store/alert 的 (leases, evictor, staging_reclaimer)。"""
        ...

    async def migrate(self, db: Any) -> None: ...

    async def close_db(self, db: Any) -> None: ...


class FakeArtifactStore:
    """只假外部字节；对象状态和租约状态仍写入真实数据库。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put_verified(self, ref: str, content: bytes) -> None:
        if hashlib.sha256(content).hexdigest() != ref:
            raise ValueError("artifact_digest_mismatch")
        self.objects[ref] = content

    async def get_bytes(self, ref: str) -> bytes:
        try:
            return self.objects[ref]
        except KeyError as exc:
            raise FileNotFoundError(ref) from exc

    async def iter_bytes(self, ref: str, chunk_size: int = 262_144):
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        data = await self.get_bytes(ref)
        for start in range(0, len(data), chunk_size):
            yield data[start : start + chunk_size]

    async def delete_local_and_r2(self, ref: str) -> None:
        self.objects.pop(ref, None)


async def _await_if_needed(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _close_if_supported(obj: Any) -> None:
    close = getattr(obj, "aclose", None) or getattr(obj, "close", None)
    if close is not None:
        await _await_if_needed(close())


@dataclass
class ContractSystem:
    backend: str
    db: Any
    leases: Any
    evictor: Any
    staging_reclaimer: Any
    store: Any
    bundle: AdapterBundle
    alerts: list[tuple[str, dict]] = field(default_factory=list)

    async def install_schema(self) -> None:
        await self.bundle.migrate(self.db)
        await _sanity_check(self.db, self.backend)

    async def close(self) -> None:
        try:
            for service in (
                self.staging_reclaimer,
                self.evictor,
                self.leases,
                self.store,
            ):
                await _close_if_supported(service)   # 先关后台服务
        finally:
            await self.bundle.close_db(self.db)      # 再关连接

    # ── 便利方法（全部查真库） ──
    async def seed_object(
        self,
        ref: str,
        state: str = "ready",
        *,
        bytes_: int = 1,
        staged_by: str = "test",
        staged_until: int = 0,
    ) -> None:
        if state not in {"staged", "ready", "deleting", "deleted"}:
            raise ValueError(f"invalid artifact state: {state}")
        now = await self.db.db_now_epoch()
        await self.db.execute(
            "INSERT INTO artifact_objects "
            "(ref,state,bytes,created_epoch,last_access_epoch,staged_by,"
            "staged_until_epoch) VALUES(?,?,?,?,?,?,?)",
            (ref, state, bytes_, now, now, staged_by, staged_until),
        )

    async def count(
        self, table: str, where: str = "1=1", params: tuple[Any, ...] = ()
    ) -> int:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
            raise ValueError("table must be a simple SQL identifier")
        row = await self.db.fetch_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params
        )
        return int(row["n"])

    async def total_bytes(self) -> int:
        return int(await self.evictor._total_bytes())

    async def pinned_bytes(self, grace_s: int = 60) -> int:
        return int(await self.evictor.pinned_bytes(grace_s=grace_s))


async def _sanity_check(db: Any, backend: str) -> None:
    """配错就炸：方言、必需方法、迁移后的表。"""
    actual = getattr(db, "dialect", None)
    if {"postgresql": "postgres"}.get(actual, actual) != backend:
        raise RuntimeError(
            f"DB dialect mismatch: expected {backend!r}, got {actual!r}"
        )

    required = (
        "transaction", "execute", "fetch_one", "fetch_all", "db_now_epoch",
        "bind", "lock_artifact", "lock_lease", "lock_key", "transition",
    )
    for method in required:
        if not callable(getattr(db, method, None)):
            raise RuntimeError(f"DB adapter missing required method: {method}")

    for table in ("artifact_objects", "frame_leases"):   # 表名为固定常量
        try:
            await db.fetch_one(f"SELECT 1 FROM {table} WHERE 1=0")
        except Exception as exc:
            raise RuntimeError(
                f"Required table missing after migrations: {table}"
            ) from exc


@dataclass
class _SqliteBundle:
    db_type: Any
    backend: str = "sqlite"

    async def open_db(self, dsn: str) -> Any:
        return await self.db_type.open(dsn)

    def make_services(
        self, db: Any, store: Any, alert: AlertFn
    ) -> tuple[Any, Any, Any]:
        from body.evidence_leases import EvidenceLeases
        from body.evict import Evictor, StagingReclaimer

        return (
            EvidenceLeases(db, store),
            Evictor(db, store, alert=alert),              # ★ alert 注入
            StagingReclaimer(db, store, alert=alert),     # ★ alert 注入
        )

    async def migrate(self, db: Any) -> None:
        from migrations.runner import apply_pending
        await _await_if_needed(apply_pending(db))

    async def close_db(self, db: Any) -> None:
        await _close_if_supported(db)


@dataclass
class _PostgresBundle:
    db_type: Any
    db_min_size: int = 1
    db_max_size: int = 10
    backend: str = "postgres"

    async def open_db(self, dsn: str) -> Any:
        return await self.db_type.connect(
            dsn, min_size=self.db_min_size, max_size=self.db_max_size
        )

    def make_services(
        self, db: Any, store: Any, alert: AlertFn
    ) -> tuple[Any, Any, Any]:
        from body.evidence_leases import EvidenceLeases
        from body.evict import Evictor, StagingReclaimer

        return (
            EvidenceLeases(db, store),
            Evictor(db, store, alert=alert),              # ★ alert 注入
            StagingReclaimer(db, store, alert=alert),     # ★ alert 注入
        )

    async def migrate(self, db: Any) -> None:
        from migrations.runner import apply_pending
        await _await_if_needed(apply_pending(db))

    async def close_db(self, db: Any) -> None:
        await _close_if_supported(db)


def make_sqlite_bundle() -> AdapterBundle:
    # YOUR_APP IMPORT — 仅当你的 SQLite 适配器在别处时才改这里
    from body.adapters.sqlite_db import SqliteDb
    return _SqliteBundle(SqliteDb)


def make_postgres_bundle() -> AdapterBundle:
    # YOUR_APP IMPORT — 仅当你的 PG 适配器在别处时才改这里
    from body.adapters.postgres_db import PostgresDb
    return _PostgresBundle(PostgresDb)


# 唯一后端选择点
BACKENDS: dict[str, AdapterBundle] = {
    "sqlite": make_sqlite_bundle(),
    "postgres": make_postgres_bundle(),
}


async def build_contract_system(
    backend: str,
    dsn: str,
    *,
    store: Any | None = None,
    alerts: list[tuple[str, dict]] | None = None,
) -> ContractSystem:
    try:
        bundle = BACKENDS[backend]
    except KeyError as exc:
        raise ValueError(
            f"unsupported backend {backend!r}; choose one of {tuple(BACKENDS)}"
        ) from exc

    db = None
    try:
        db = await bundle.open_db(dsn)
        artifact_store = store if store is not None else FakeArtifactStore()
        alert_log = alerts if alerts is not None else []

        async def alert(name: str, detail: dict) -> None:
            alert_log.append((name, dict(detail)))   # 测试可读

        leases, evictor, staging_reclaimer = bundle.make_services(
            db, artifact_store, alert
        )
        return ContractSystem(
            backend=backend,
            db=db,
            leases=leases,
            evictor=evictor,
            staging_reclaimer=staging_reclaimer,
            store=artifact_store,
            bundle=bundle,
            alerts=alert_log,
        )
    except BaseException:
        if db is not None:
            await bundle.close_db(db)                # 构造失败别漏连接
        raise
