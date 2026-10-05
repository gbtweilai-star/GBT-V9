# tests/test_evidence_leases.py
import pytest
from body.evict import claim_for_eviction, evict

@pytest.mark.asyncio
async def test_eviction_skips_pinned(db, store):
    db.put_object("ref1", state="ready"); lid = await db.add_lease("ref1", ttl=120)
    assert await claim_for_eviction(db, "ref1", grace_s=60) is False   # 有活跃锁 → 跳过

@pytest.mark.asyncio
async def test_expired_lease_reclaimed(db, store):
    db.put_object("ref2", state="ready"); await db.add_lease("ref2", ttl=-1)  # 已过期
    assert await claim_for_eviction(db, "ref2", grace_s=0) is True

@pytest.mark.asyncio
async def test_all_pinned_blocks_watermark(db, store):
    for r in ("a","b","c"):
        db.put_object(r, state="ready"); await db.add_lease(r, ttl=120)
    res = await db.run_watermark_pass(high_water=0)
    assert db.deleted_count() == 0 and db.last_alert() == "watermark_blocked_pinned"

@pytest.mark.asyncio
async def test_evidence_survives_raw_eviction(db, store):
    db.put_object("ref3", state="ready")
    db.put_evidence("E1", ref="ref3", digest="d", summary='{"verdict":"verified"}')
    await evict(db, store, "ref3", grace_s=0)
    ev = db.get_evidence("E1")
    assert ev["raw_available"] == 0 and ev["summary_json"]        # 结论还在, 帧没了
