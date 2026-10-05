# tests/contracts/test_evictor_watermark.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

import pytest
import pytest_asyncio

from tests.adapter_factory import (
    ContractSystem,
    FakeArtifactStore,
    build_contract_system,
)

PREFIX = "evict-test-"


def _ref() -> str:
    return PREFIX + uuid.uuid4().hex


def _content(ref: str) -> bytes:
    return f"test-bytes:{ref}".encode()


def _put(system: ContractSystem, ref: str) -> bytes:
    """只假外部字节；对象状态仍写进真库。ref 不是内容哈希，故直接塞 store。"""
    content = _content(ref)
    system.store.objects[ref] = content
    return content


async def _artifact(system: ContractSystem, ref: str) -> dict[str, Any]:
    row = await system.db.fetch_one(
        "SELECT * FROM artifact_objects WHERE ref=?", (ref,)
    )
    assert row is not None
    return row


async def _table_columns(system: ContractSystem, table: str) -> set[str]:
    if system.db.dialect == "sqlite":
        rows = await system.db.fetch_all(f"PRAGMA table_info({table})")
        return {row["name"] for row in rows}
    rows = await system.db.fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=?",
        (table,),
    )
    return {row["column_name"] for row in rows}


async def _cleanup_postgres(system: ContractSystem) -> None:
    """别让本套件的前缀行影响后续运行（PG 是共享库）。"""
    columns = await _table_columns(system, "frame_verification_evidence")
    refs = [n for n in ("ref", "baseline_ref", "result_ref") if n in columns]
    if refs:
        where = " OR ".join(f"{n} LIKE ?" for n in refs)
        await system.db.execute(
            f"DELETE FROM frame_verification_evidence WHERE {where}",
            tuple(f"{PREFIX}%" for _ in refs),
        )
    await system.db.execute(
        "DELETE FROM frame_leases WHERE ref LIKE ?", (f"{PREFIX}%",)
    )
    await system.db.execute(
        "DELETE FROM artifact_objects WHERE ref LIKE ?", (f"{PREFIX}%",)
    )


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
        dsn = str(tmp_path / "evictor-watermark.sqlite")

    systems: list[ContractSystem] = []

    async def make_system(*, store: Any | None = None) -> ContractSystem:
        system = await build_contract_system(backend, dsn, store=store)
        systems.append(system)
        await system.install_schema()
        if use_postgres:
            await _cleanup_postgres(system)
        return system

    yield make_system

    for system in reversed(systems):
        try:
            if use_postgres:
                await _cleanup_postgres(system)
        finally:
            await system.close()


async def _pin(stack: AsyncExitStack, system: ContractSystem, ref: str) -> None:
    await stack.enter_async_context(
        system.leases.hold([ref], holder="watermark-test", purpose="pin", ttl_s=3600)
    )
    assert await system.count(
        "frame_leases", "ref=? AND status='active'", (ref,)
    ) == 1


async def _set_age(system: ContractSystem, ref: str, age: int) -> None:
    await system.db.execute(
        "UPDATE artifact_objects SET last_access_epoch=? WHERE ref=?", (age, ref)
    )


@pytest.mark.asyncio
async def test_under_water_no_eviction(system_factory: Any) -> None:
    system = await system_factory()
    refs = [_ref(), _ref()]
    for ref, size in zip(refs, (3, 4)):
        _put(system, ref)
        await system.seed_object(ref, "ready", bytes_=size)

    before = await system.total_bytes()
    result = await system.evictor.run_watermark_pass(before + 1)

    assert result["status"] == "ok"
    assert result["freed_bytes"] == 0
    assert await system.total_bytes() == before
    for ref in refs:
        assert (await _artifact(system, ref))["state"] == "ready"
    assert system.alerts == []


@pytest.mark.asyncio
async def test_over_water_evicts_oldest_first_to_target(system_factory: Any) -> None:
    system = await system_factory()
    baseline = await system.total_bytes()
    refs = [_ref() for _ in range(4)]
    sizes = [7, 5, 4, 2]

    for index, (ref, size) in enumerate(zip(refs, sizes)):
        _put(system, ref)
        await system.seed_object(ref, "ready", bytes_=size)
        await _set_age(system, ref, -1_000_000 + index)      # 越靠前越老

    result = await system.evictor.run_watermark_pass(baseline + 10)

    assert result["status"] == "ok"
    assert result["freed_bytes"] == 12
    assert await system.total_bytes() <= baseline + 10
    assert [(await _artifact(system, ref))["state"] for ref in refs] == [
        "deleted", "deleted", "ready", "ready"             # ★ 老的先走
    ]
    for ref in refs[:2]:
        with pytest.raises(FileNotFoundError):
            await system.store.get_bytes(ref)
    for ref in refs[2:]:
        assert await system.store.get_bytes(ref) == _content(ref)


@pytest.mark.asyncio
async def test_pinned_blocks_with_alert(system_factory: Any) -> None:
    system = await system_factory()
    refs, sizes = [_ref(), _ref()], [5, 7]
    for ref, size in zip(refs, sizes):
        _put(system, ref)
        await system.seed_object(ref, "ready", bytes_=size)

    async with AsyncExitStack() as stack:
        for ref in refs:
            await _pin(stack, system, ref)

        result = await system.evictor.run_watermark_pass(0)
        expected_pinned = sum(sizes)

        assert result["status"] == "watermark_blocked_pinned"    # ★ 不空转、不强删
        for ref in refs:
            assert (await _artifact(system, ref))["state"] == "ready"
            assert await system.store.get_bytes(ref) == _content(ref)
        assert await system.pinned_bytes() == expected_pinned
        alerts = [d for n, d in system.alerts if n == "watermark_blocked_pinned"]
        assert alerts
        assert alerts[-1]["pinned_bytes"] == expected_pinned


@pytest.mark.asyncio
async def test_mixed_free_and_pinned_evicts_free_then_blocks(
    system_factory: Any,
) -> None:
    system = await system_factory()
    pinned_refs, pinned_sizes = [_ref(), _ref()], [5, 4]
    free_ref, free_size = _ref(), 7

    for ref, size in zip(pinned_refs, pinned_sizes):
        _put(system, ref)
        await system.seed_object(ref, "ready", bytes_=size)
    _put(system, free_ref)
    await system.seed_object(free_ref, "ready", bytes_=free_size)

    async with AsyncExitStack() as stack:
        for ref in pinned_refs:
            await _pin(stack, system, ref)

        result = await system.evictor.run_watermark_pass(8)
        expected_pinned = sum(pinned_sizes)

        assert result["status"] == "watermark_blocked_pinned"
        assert (await _artifact(system, free_ref))["state"] == "deleted"
        for ref in pinned_refs:
            assert (await _artifact(system, ref))["state"] == "ready"
        with pytest.raises(FileNotFoundError):
            await system.store.get_bytes(free_ref)
        for ref in pinned_refs:
            assert await system.store.get_bytes(ref) == _content(ref)
        alerts = [d for n, d in system.alerts if n == "watermark_blocked_pinned"]
        assert alerts and alerts[-1]["pinned_bytes"] == expected_pinned


@pytest.mark.asyncio
async def test_staged_is_counted_but_never_evicted(system_factory: Any) -> None:
    system = await system_factory()
    baseline = await system.total_bytes()
    ref, size = _ref(), 9
    _put(system, ref)
    now = await system.db.db_now_epoch()
    await system.seed_object(ref, "staged", bytes_=size, staged_until=now + 3600)

    result = await system.evictor.run_watermark_pass(baseline + size - 1)

    assert result["status"] == "watermark_no_candidates"       # ★ 只计量、不回收
    assert await system.total_bytes() == baseline + size
    assert (await _artifact(system, ref))["state"] == "staged"
    assert await system.store.get_bytes(ref) == _content(ref)
    assert await system.pinned_bytes() == 0
    assert system.alerts == []


@pytest.mark.asyncio
async def test_expired_lease_not_pinned(system_factory: Any) -> None:
    system = await system_factory()
    ref, size = _ref(), 3
    _put(system, ref)
    await system.seed_object(ref, "ready", bytes_=size)
    now = await system.db.db_now_epoch()
    await system.db.execute(
        "INSERT INTO frame_leases "
        "(lease_id,ref,holder,purpose,acquired_epoch,expires_epoch,renew_count,status) "
        "VALUES(?,?,?,?,?,?,0,'active')",
        (uuid.uuid4().hex, ref, "expired-test", "test", now - 100_000, now - 99_999),
    )

    assert await system.pinned_bytes() == 0
    result = await system.evictor.run_watermark_pass(0)

    assert result["status"] == "ok"
    assert (await _artifact(system, ref))["state"] == "deleted"
    with pytest.raises(FileNotFoundError):
        await system.store.get_bytes(ref)


@pytest.mark.asyncio
async def test_grace_window_protects_recent_lease(system_factory: Any) -> None:
    system = await system_factory()
    ref, size = _ref(), 3
    _put(system, ref)
    await system.seed_object(ref, "ready", bytes_=size)
    now = await system.db.db_now_epoch()
    await system.db.execute(
        "INSERT INTO frame_leases "
        "(lease_id,ref,holder,purpose,acquired_epoch,expires_epoch,renew_count,status) "
        "VALUES(?,?,?,?,?,?,0,'active')",
        (uuid.uuid4().hex, ref, "grace-test", "test", now - 2, now - 1),  # 刚过期
    )

    assert await system.pinned_bytes(grace_s=60) == size
    protected = await system.evictor.run_watermark_pass(0, grace_s=60)
    assert protected["status"] == "watermark_blocked_pinned"
    assert (await _artifact(system, ref))["state"] == "ready"

    released = await system.evictor.run_watermark_pass(0, grace_s=0)
    assert released["status"] == "ok"
    assert (await _artifact(system, ref))["state"] == "deleted"


@pytest.mark.asyncio
async def test_delete_failure_restores_ready(system_factory: Any) -> None:
    ref = _ref()

    class FailingStore(FakeArtifactStore):
        async def delete_local_and_r2(self, delete_ref: str) -> None:
            if delete_ref == ref:
                raise RuntimeError("delete failed")
            await super().delete_local_and_r2(delete_ref)

    store = FailingStore()
    system = await system_factory(store=store)
    content = _content(ref)
    store.objects[ref] = content
    await system.seed_object(ref, "ready", bytes_=len(content))

    with pytest.raises(RuntimeError, match="delete failed"):
        await system.evictor.run_watermark_pass(0)

    assert (await _artifact(system, ref))["state"] == "ready"  # ★ 回滚成 ready
    assert await store.get_bytes(ref) == content


@pytest.mark.asyncio
async def test_evidence_marked_unavailable_on_evict(system_factory: Any) -> None:
    system = await system_factory()
    columns = await _table_columns(system, "frame_verification_evidence")
    required = {"ref", "baseline_ref", "result_ref", "raw_available"}
    if not required.issubset(columns):
        pytest.skip("frame_verification_evidence lacks required columns")

    ref = _ref()
    _put(system, ref)
    await system.seed_object(ref, "ready", bytes_=3)
    cols = ["ref", "baseline_ref", "result_ref", "raw_available"]
    quoted = ",".join(f'"{c}"' for c in cols)
    marks = ",".join("?" for _ in cols)
    try:
        await system.db.execute(
            f"INSERT INTO frame_verification_evidence ({quoted}) VALUES({marks})",
            tuple([ref, ref, ref, 1]),
        )
    except Exception as exc:
        pytest.skip(f"cannot seed minimal evidence row: {exc}")

    result = await system.evictor.run_watermark_pass(0)
    assert result["status"] == "ok"
    row = await system.db.fetch_one(
        "SELECT raw_available FROM frame_verification_evidence "
        "WHERE ref=? OR baseline_ref=? OR result_ref=?",
        (ref, ref, ref),
    )
    assert row is not None
    assert int(row["raw_available"]) == 0                       # 结论留着，帧标不可用
