# tests/contracts/test_lease_eviction.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from body.evidence_leases import EvidenceUnavailable
from tests.adapter_factory import ContractSystem, build_contract_system

PREFIX = "lease-race-"


def _ref(tag: str = "") -> str:
    return f"{PREFIX}{tag}{uuid.uuid4().hex}"


def _content(ref: str) -> bytes:
    return f"test-bytes:{ref}".encode()


async def _artifact(system: ContractSystem, ref: str) -> dict[str, Any]:
    row = await system.db.fetch_one(
        "SELECT * FROM artifact_objects WHERE ref=?", (ref,))
    assert row is not None
    return row


async def _columns(system: ContractSystem, table: str) -> set[str]:
    if system.db.dialect == "sqlite":
        rows = await system.db.fetch_all(f"PRAGMA table_info({table})")
        return {row["name"] for row in rows}
    rows = await system.db.fetch_all(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name=?", (table,))
    return {row["column_name"] for row in rows}


async def _cleanup_postgres(system: ContractSystem) -> None:
    columns = await _columns(system, "frame_verification_evidence")
    refs = [c for c in ("ref", "baseline_ref", "result_ref") if c in columns]
    if refs:
        where = " OR ".join(f"{c} LIKE ?" for c in refs)
        await system.db.execute(
            f"DELETE FROM frame_verification_evidence WHERE {where}",
            tuple(f"{PREFIX}%" for _ in refs))
    await system.db.execute("DELETE FROM frame_leases WHERE ref LIKE ?", (f"{PREFIX}%",))
    await system.db.execute("DELETE FROM artifact_objects WHERE ref LIKE ?", (f"{PREFIX}%",))


@pytest_asyncio.fixture
async def system(tmp_path: Any) -> AsyncIterator[ContractSystem]:
    use_pg = os.getenv("RUN_PG_CONTRACTS") == "1"
    if use_pg:
        dsn = os.getenv("TEST_DATABASE_URL")
        if not dsn:
            pytest.skip("RUN_PG_CONTRACTS=1 requires TEST_DATABASE_URL")
        backend = "postgres"
    else:
        backend = "sqlite"
        dsn = str(tmp_path / "lease-eviction.sqlite")

    instance = await build_contract_system(backend, dsn)
    await instance.install_schema()
    if use_pg:
        await _cleanup_postgres(instance)
    try:
        yield instance
    finally:
        if use_pg:
            await _cleanup_postgres(instance)
        await instance.close()


async def _seed_ready(system: ContractSystem, ref: str) -> bytes:
    content = _content(ref)
    system.store.objects[ref] = content
    await system.seed_object(ref, "ready", bytes_=len(content))
    return content


async def _release_sessions(sessions: dict[str, Any]) -> None:
    for session in sessions.values():
        await session.__aexit__(None, None, None)


async def _run_race(
    system: ContractSystem, refs: list[str]
) -> tuple[dict[str, Any], list[list[str]]]:
    """并发：每根 ref 同时尝试取租约和驱逐；赢的租约保持开启。"""
    sessions: dict[str, Any] = {}

    async def acquire(ref: str) -> str:
        session = system.leases.hold([ref], holder=f"race-{ref}", purpose="race", ttl_s=3600)
        try:
            await session.__aenter__()
        except EvidenceUnavailable:
            return "lease_lost"
        sessions[ref] = session
        return "lease_won"

    async def evict(ref: str) -> str:
        return "evict_won" if await system.evictor.evict(ref, grace_s=0) else "evict_lost"

    async def race_one(ref: str) -> list[str]:
        return await asyncio.gather(acquire(ref), evict(ref))

    try:
        outcomes = await asyncio.wait_for(
            asyncio.gather(*(race_one(ref) for ref in refs)), timeout=20)
        return sessions, outcomes
    except BaseException:
        await _release_sessions(sessions)
        raise


async def _release_refs(system: ContractSystem, refs: list[str]) -> None:
    if not refs:
        return
    marks = ",".join("?" for _ in refs)
    async with system.db.transaction(immediate=(system.db.dialect == "sqlite")):
        await system.db.execute(
            f"UPDATE frame_leases SET status='released' "
            f"WHERE status='active' AND ref IN ({marks})", tuple(refs))


@pytest.mark.asyncio
async def test_lease_wins_then_evict_skips(system: ContractSystem) -> None:
    ref = _ref()
    content = await _seed_ready(system, ref)
    async with system.leases.hold([ref], holder="test", purpose="pin", ttl_s=3600):
        assert not await system.evictor.claim_for_eviction(ref, grace_s=0)
        assert not await system.evictor.evict(ref, grace_s=0)
        assert (await _artifact(system, ref))["state"] == "ready"
        assert await system.count("frame_leases", "ref=? AND status='active'", (ref,)) == 1
        assert await system.store.get_bytes(ref) == content


@pytest.mark.asyncio
async def test_evict_wins_then_lease_fails(system: ContractSystem) -> None:
    ref = _ref()
    await _seed_ready(system, ref)
    before = await system.count("frame_leases")

    assert await system.evictor.evict(ref, grace_s=0)
    with pytest.raises(EvidenceUnavailable):
        await system.leases.acquire_many_atomic([ref], holder="late", purpose="test", ttl_s=60)

    assert (await _artifact(system, ref))["state"] == "deleted"
    assert await system.count("frame_leases") == before          # 没有半截租约
    assert await system.count("frame_leases", "ref=?", (ref,)) == 0
    with pytest.raises(FileNotFoundError):
        await system.store.get_bytes(ref)


@pytest.mark.asyncio
async def test_race_has_exactly_one_consistent_winner(system: ContractSystem) -> None:
    refs = [_ref() for _ in range(25)]
    contents = {ref: await _seed_ready(system, ref) for ref in refs}
    sessions, outcomes = await _run_race(system, refs)
    try:
        for ref, outcome in zip(refs, outcomes):
            state = (await _artifact(system, ref))["state"]
            active = await system.count("frame_leases", "ref=? AND status='active'", (ref,))
            deleted_case = state == "deleted" and active == 0
            leased_case = state == "ready" and active >= 1
            assert deleted_case != leased_case, (ref, outcome, state, active)   # ★ 只有一个赢家
            if deleted_case:
                with pytest.raises(FileNotFoundError):
                    await system.store.get_bytes(ref)
            else:
                assert await system.store.get_bytes(ref) == contents[ref]
    finally:
        await _release_sessions(sessions)


@pytest.mark.asyncio
async def test_race_never_leaves_deleting_ghost(system: ContractSystem) -> None:
    refs = [_ref() for _ in range(25)]
    for ref in refs:
        await _seed_ready(system, ref)
    sessions, _ = await _run_race(system, refs)
    try:
        assert await system.count(
            "artifact_objects", "ref LIKE ? AND state='deleting'", (f"{PREFIX}%",)) == 0
        for ref in refs:
            assert (await _artifact(system, ref))["state"] in {"ready", "deleted"}
    finally:
        await _release_sessions(sessions)


@pytest.mark.asyncio
async def test_evict_marks_evidence_unavailable_when_it_wins(system: ContractSystem) -> None:
    try:
        columns = await _columns(system, "frame_verification_evidence")
    except Exception:
        pytest.skip("frame_verification_evidence table is absent")
    required = {"id", "ref", "baseline_ref", "result_ref", "raw_available", "created_epoch"}
    if not required.issubset(columns):
        pytest.skip("frame evidence table lacks required columns")

    ref = _ref()
    await _seed_ready(system, ref)
    now = await system.db.db_now_epoch()
    await system.db.execute(
        "INSERT INTO frame_verification_evidence "
        "(id,ref,baseline_ref,result_ref,raw_available,created_epoch) "
        "VALUES(?,?,?,?,1,?)", (uuid.uuid4().hex, ref, ref, ref, now))

    assert await system.evictor.evict(ref, grace_s=0)
    row = await system.db.fetch_one(
        "SELECT raw_available FROM frame_verification_evidence WHERE ref=?", (ref,))
    assert row is not None and int(row["raw_available"]) == 0
    assert (await _artifact(system, ref))["state"] == "deleted"


@pytest.mark.asyncio
async def test_batch_all_or_nothing_rolls_back_missing_ref(system: ContractSystem) -> None:
    ready, missing = _ref("a-"), _ref("z-")
    await _seed_ready(system, ready)
    before = await system.count("frame_leases")

    with pytest.raises(EvidenceUnavailable):
        await system.leases.acquire_many_atomic(
            [ready, missing], holder="batch-missing", purpose="test", ttl_s=60)

    assert await system.count("frame_leases") == before
    assert await system.count("frame_leases", "ref=?", (ready,)) == 0     # ★ 好 ref 也不留租约
    assert await system.count("frame_leases", "ref=?", (missing,)) == 0


@pytest.mark.asyncio
async def test_batch_all_or_nothing_rolls_back_staged_ref(system: ContractSystem) -> None:
    ready, staged = _ref("a-"), _ref("z-")
    await _seed_ready(system, ready)
    now = await system.db.db_now_epoch()
    await system.seed_object(staged, "staged", staged_until=now + 3600)
    before = await system.count("frame_leases")

    with pytest.raises(EvidenceUnavailable):
        await system.leases.acquire_many_atomic(
            [ready, staged], holder="batch-staged", purpose="test", ttl_s=60)

    assert await system.count("frame_leases") == before
    assert await system.count("frame_leases", "ref=?", (ready,)) == 0
    assert await system.count("frame_leases", "ref=?", (staged,)) == 0


@pytest.mark.asyncio
async def test_batch_success_inserts_one_active_lease_per_ref(system: ContractSystem) -> None:
    refs = [_ref() for _ in range(3)]
    for ref in refs:
        await _seed_ready(system, ref)
    try:
        lease_ids = await system.leases.acquire_many_atomic(
            refs, holder="batch-success", purpose="test", ttl_s=300)
        assert len(lease_ids) == 3
        now = await system.db.db_now_epoch()
        rows = []
        for ref in refs:
            row = await system.db.fetch_one(
                "SELECT * FROM frame_leases WHERE ref=? AND holder=?",
                (ref, "batch-success"))
            assert row is not None
            rows.append(row)
        assert len(rows) == 3
        assert all(row["status"] == "active" for row in rows)
        assert all(int(row["expires_epoch"]) > now for row in rows)
        assert await system.count("frame_leases", "holder=?", ("batch-success",)) == 3
    finally:
        await _release_refs(system, refs)


@pytest.mark.asyncio
async def test_overlapping_batches_do_not_deadlock_or_partially_apply(system: ContractSystem) -> None:
    r1, r2, r3 = _ref(), _ref(), _ref()
    refs = [r1, r2, r3]
    for ref in refs:
        await _seed_ready(system, ref)

    async def acquire(holder: str, batch: list[str]) -> tuple[str, list[str] | None]:
        try:
            ids = await system.leases.acquire_many_atomic(
                batch, holder=holder, purpose="overlap", ttl_s=300)
            return holder, ids
        except EvidenceUnavailable:
            return holder, None

    try:
        results = await asyncio.wait_for(                      # ★ 死锁会 20s 超时炸掉
            asyncio.gather(
                acquire("overlap-a", [r1, r2]),
                acquire("overlap-b", [r2, r3])),
            timeout=20)
        for holder, batch in (("overlap-a", {r1, r2}), ("overlap-b", {r2, r3})):
            result = dict(results)[holder]
            rows = await system.db.fetch_all(
                "SELECT ref,status FROM frame_leases WHERE holder=?", (holder,))
            if result is None:
                assert rows == []                              # 失败就一行都不留
            else:
                assert len(result) == 2 and len(rows) == 2
                assert {row["ref"] for row in rows} == batch
                assert all(row["status"] == "active" for row in rows)
    finally:
        await _release_refs(system, refs)


@pytest.mark.asyncio
async def test_release_after_lease_win_lets_eviction_proceed(system: ContractSystem) -> None:
    ref = _ref()
    await _seed_ready(system, ref)
    async with system.leases.hold([ref], holder="release-test", purpose="pin", ttl_s=3600):
        assert not await system.evictor.evict(ref, grace_s=0)
        assert (await _artifact(system, ref))["state"] == "ready"

    assert await system.count("frame_leases", "ref=? AND status='active'", (ref,)) == 0
    assert await system.evictor.evict(ref, grace_s=0)          # 释放后驱逐才放行
    assert (await _artifact(system, ref))["state"] == "deleted"
    with pytest.raises(FileNotFoundError):
        await system.store.get_bytes(ref)
