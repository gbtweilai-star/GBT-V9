# tests/test_lease_session.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import asyncio, pytest
from body.evidence_leases import EvidenceLeases, EvidenceUnavailable, LeaseLost

async def test_single_and_many(db, store):
    async with leases.hold(["r1"], holder="h", purpose="t") as p: assert p.lease_ids
    async with leases.hold(["r1", "r2", "r1"], holder="h", purpose="t") as p:
        assert p.refs == ["r1", "r2"]                      # 去重排序

async def test_partial_acquire_rolls_back(db, store):
    db.mark_deleted("r2")
    with pytest.raises(EvidenceUnavailable):
        async with leases.hold(["r1", "r2"], holder="h", purpose="t"): pass
    assert db.active_leases("r1") == 0                     # 全部回滚, 无残留

async def test_renew_across_ttl(db, store):
    async with leases.hold(["r1"], holder="h", purpose="t", ttl_s=0.3) as p:
        await asyncio.sleep(0.8)                           # 跨过 TTL
        assert await p.read("r1")                          # 续租成功仍可读

@pytest.mark.asyncio
async def test_renewal_failure_raises_lost(db, store):
    db.renew_returns = False
    async with leases.hold(["r1"], holder="h", purpose="t", ttl_s=0.2) as p:
        await asyncio.sleep(0.4)
        with pytest.raises(LeaseLost): await p.read("r1")

async def test_streaming_release_on_disconnect(db, store):
    async with leases.hold(["r1"], holder="h", purpose="t") as p:
        gen = p.iter_bytes("r1"); await gen.__anext__()
        await gen.aclose()                                 # 提前关闭
    assert db.active_leases("r1") == 0

async def test_idempotent_close(db, store):
    p = leases.hold(["r1"], holder="h", purpose="t"); await p.__aenter__()
    await p.close(); await p.close()
    assert db.release_calls("r1") == 1                     # 只释放一次

async def test_expired_does_not_revive(db, store):
    lid = await db.add_lease("r1", ttl=-1)                 # 已过期
    assert await db.renew_many([lid], holder="h") is False
