# tests/test_put_and_hold.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_staged_is_protected_from_eviction(db, store):
    p = leases.hold([], holder="h", purpose="v"); await p.__aenter__()
    ref = await p.put_and_hold(b"PNGDATA")                 # 已 ready + 有 lease
    # 再造一个纯 staged(不提升)
    await db.insert_object("refX", state="staged", staged_until=999999)
    assert await claim_for_eviction(db, "refX", grace_s=0) is False      # staged 永不回收

async def test_crash_leak_reclaimed_by_ttl(db, store):
    await db.insert_object("refY", state="staged", staged_by="dead", staged_until=1)
    await reclaim_expired_staging(db, store, now=1000)
    assert db.get_object("refY")["state"] == "deleted"

async def test_dedup_two_holders_single_object(db, store):
    async def put(h):
        async with leases.hold([], holder=h, purpose="v") as p: return await p.put_and_hold(b"SAME")
    r1, r2 = await asyncio.gather(put("h1"), put("h2"))
    assert r1 == r2 and db.count_objects(r1) == 1 and db.active_leases(r1) == 2

async def test_refuse_when_deleting(db, store):
    await db.insert_object("refZ", state="deleting")
    with pytest.raises(EvidenceUnavailable):
        await (await leases.hold([], holder="h", purpose="v").__aenter__()).put_and_hold(b"x")

async def test_no_ready_without_lease_window(db, store):
    p = leases.hold([], holder="h", purpose="v"); await p.__aenter__()
    ref = await p.put_and_hold(b"ABC")
    # put_and_hold 返回时, ready 与 lease 必须已同时存在
    assert db.get_object(ref)["state"] == "ready" and db.active_leases(ref) == 1
