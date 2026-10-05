# tests/test_evict.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from body.evict import Evictor, StagingReclaimer

async def test_staged_never_claimed(system):
    db = system.db
    await system.seed_object("s1", state="staged", staged_until=9999999999)
    assert await system.evictor.claim_for_eviction("s1", grace_s=0) is False
    assert (await db.fetch_one("SELECT state FROM artifact_objects WHERE ref='s1'"))["state"] == "staged"

async def test_pinned_skipped(system):
    await system.seed_object("r1", state="ready")
    async with system.leases.hold(["r1"], holder="h", purpose="t"):        # 持锁
        assert await system.evictor.claim_for_eviction("r1", grace_s=0) is False

async def test_over_watermark_all_pinned_reports_blocked(system):
    await system.seed_object("r1", state="ready", bytes=100)
    async with system.leases.hold(["r1"], holder="h", purpose="t"):
        res = await system.evictor.run_watermark_pass(high_water=0, grace_s=0)
    assert res["status"] == "blocked_pinned" and res["pinned_bytes"] == 100    # 不空转
    assert system.db.deleted_count() == 0                                     # 什么都没删

async def test_expired_staged_reclaimed(system):
    await system.seed_object("s2", state="staged", staged_until=1)            # 已过期
    out = await system.staging_reclaimer.reclaim_expired()
    assert out["reclaimed"] >= 1
    assert (await system.db.fetch_one("SELECT state FROM artifact_objects WHERE ref='s2'"))["state"] == "deleted"

async def test_failed_external_delete_returns_to_ready(system):
    await system.seed_object("r2", state="ready")
    system.store.delete_local_and_r2 = _boom                                 # 外部删除抛错
    with pytest.raises(RuntimeError):
        await system.evictor.evict("r2", grace_s=0)
    assert (await system.db.fetch_one("SELECT state FROM artifact_objects WHERE ref='r2'"))["state"] == "ready"

async def _boom(ref): raise RuntimeError("delete_failed")
