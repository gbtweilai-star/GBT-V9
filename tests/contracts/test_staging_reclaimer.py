# tests/contracts/test_staging_reclaimer.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import pytest
import pytest_asyncio

from tests.adapter_factory import (
    ContractSystem,
    FakeArtifactStore,
    build_contract_system,
)


def _unique_ref() -> str:
    return f"staging-test-{uuid.uuid4().hex}"


def _unique_blob() -> tuple[str, bytes]:
    content = f"staging-test-{uuid.uuid4().hex}".encode()
    return hashlib.sha256(content).hexdigest(), content


async def _artifact(system: ContractSystem, ref: str) -> dict[str, Any]:
    row = await system.db.fetch_one(
        "SELECT * FROM artifact_objects WHERE ref=?", (ref,)
    )
    assert row is not None
    return row


@pytest_asyncio.fixture
async def system_factory(
    tmp_path: Any,
) -> AsyncIterator[Callable[..., Awaitable[ContractSystem]]]:
    use_postgres = os.getenv("RUN_PG_CONTRACTS") == "1"
    backend = "postgres" if use_postgres else "sqlite"

    if use_postgres:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("RUN_PG_CONTRACTS=1 requires TEST_DATABASE_URL")
    else:
        dsn = str(tmp_path / "staging-contracts.sqlite")

    systems: list[ContractSystem] = []

    async def make_system(*, store: Any | None = None) -> ContractSystem:
        system = await build_contract_system(backend, dsn, store=store)
        systems.append(system)
        await system.install_schema()
        return system

    yield make_system

    for system in reversed(systems):
        await system.close()


@pytest.mark.asyncio
async def test_renew_prevents_reclaim(system_factory: Any) -> None:
    system = await system_factory()
    ref = _unique_ref()
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now - 10)

    assert await system.staging_reclaimer.extend_staged_until(ref, ttl_s=3600)
    assert await system.staging_reclaimer.reclaim_expired() == 0

    row = await _artifact(system, ref)
    now = await system.db.db_now_epoch()
    assert row["state"] == "staged"
    assert int(row["staged_until_epoch"]) > now        # 续期真的推后了


@pytest.mark.asyncio
async def test_expired_staged_is_reclaimed(system_factory: Any) -> None:
    system = await system_factory()
    ref, content = _unique_blob()
    await system.store.put_verified(ref, content)
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now - 10)

    assert await system.staging_reclaimer.reclaim_expired() == 1

    assert (await _artifact(system, ref))["state"] == "deleted"
    with pytest.raises(FileNotFoundError):             # 外部字节真的没了
        await system.store.get_bytes(ref)


@pytest.mark.asyncio
async def test_unexpired_staged_not_reclaimed(system_factory: Any) -> None:
    system = await system_factory()
    ref, content = _unique_blob()
    await system.store.put_verified(ref, content)
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now + 3600)

    assert await system.staging_reclaimer.reclaim_expired() == 0

    assert (await _artifact(system, ref))["state"] == "staged"
    assert await system.store.get_bytes(ref) == content


@pytest.mark.asyncio
async def test_ready_untouched_by_staging_reclaimer(system_factory: Any) -> None:
    system = await system_factory()
    ref = _unique_ref()
    await system.seed_object(ref, "ready", staged_until=0)

    assert await system.staging_reclaimer.reclaim_expired() == 0
    assert (await _artifact(system, ref))["state"] == "ready"   # ready 归 Evictor 管


@pytest.mark.asyncio
async def test_delete_failure_restores_staged_and_bumps_retry(
    system_factory: Any,
) -> None:
    ref, content = _unique_blob()

    class FailingDeleteStore(FakeArtifactStore):
        async def delete_local_and_r2(self, delete_ref: str) -> None:
            if delete_ref == ref:
                raise RuntimeError("boom")
            await super().delete_local_and_r2(delete_ref)

    store = FailingDeleteStore()
    system = await system_factory(store=store)
    await store.put_verified(ref, content)
    now_before = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now_before - 10)
    retry_delay_s = system.staging_reclaimer.retry_delay_s

    with pytest.raises(RuntimeError, match="boom"):
        await system.staging_reclaimer.reclaim_expired()

    row = await _artifact(system, ref)
    now_after = await system.db.db_now_epoch()
    assert row["state"] == "staged"                    # ★ 回滚成 staged，不卡 deleting
    assert int(row["staged_until_epoch"]) >= now_after + retry_delay_s - 2
    assert await store.get_bytes(ref) == content       # 字节没被误删


@pytest.mark.asyncio
async def test_renew_after_claim_returns_false(system_factory: Any) -> None:
    system = await system_factory()
    ref = _unique_ref()
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now - 10)

    # 模拟回收器已经 claim 走（staged -> deleting）
    async with system.db.transaction(
        immediate=(system.db.dialect == "sqlite")
    ):
        row = await system.db.lock_artifact(ref)
        assert row is not None and row["state"] == "staged"
        assert await system.db.transition(
            ref, "staged", "deleting", guard="state='staged'"
        )

    assert not await system.staging_reclaimer.extend_staged_until(ref, ttl_s=3600)
    assert (await _artifact(system, ref))["state"] == "deleting"   # 不能被续活


@pytest.mark.asyncio
async def test_reclaim_is_idempotent(system_factory: Any) -> None:
    system = await system_factory()
    ref, content = _unique_blob()
    await system.store.put_verified(ref, content)
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", staged_until=now - 10)

    assert await system.staging_reclaimer.reclaim_expired() == 1
    assert await system.staging_reclaimer.reclaim_expired() == 0   # 二次不再重复回收
    assert (await _artifact(system, ref))["state"] == "deleted"


@pytest.mark.asyncio
async def test_batch_limits_reclaims(system_factory: Any) -> None:
    system = await system_factory()
    now = await system.db.db_now_epoch()
    refs = [_unique_ref() for _ in range(3)]
    for ref in refs:
        await system.seed_object(ref, "staged", staged_until=now - 10)

    assert await system.staging_reclaimer.reclaim_expired(batch=2) == 2
    states = [(await _artifact(system, ref))["state"] for ref in refs]
    assert states.count("deleted") == 2
    assert states.count("staged") == 1

    assert await system.staging_reclaimer.reclaim_expired(batch=2) == 1
    for ref in refs:
        assert (await _artifact(system, ref))["state"] == "deleted"
