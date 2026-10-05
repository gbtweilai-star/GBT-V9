# tests/test_landing.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_migration_apply_once_and_rerun_noop(db):
    await apply_pending(db); await apply_pending(db)            # 重跑
    assert db.count("schema_migrations", id="m2026_frame_evidence_v2") == 1

async def test_migration_double_worker_single_apply(db):
    await asyncio.gather(apply_pending(db), apply_pending(db))  # advisory/BEGIN IMMEDIATE 串行
    assert db.count("schema_migrations") == 1

async def test_edit_invalidates_cache_before_and_after(ctx):
    g0 = ctx.devour.cache.generation("p")
    await run_edit_op(ctx, "p", "t", 0, {"op": "split", "at_s": 3.0})
    assert ctx.devour.cache.generation("p") > g0                # 世代至少 +1

async def test_evidence_pair_never_cached(ctx):
    ctx.devour.cache.items.clear()
    t = {"op": "color", "start_s": 1, "end_s": 3}
    await run_edit_op(ctx, "p", "t", 0, t)
    # evidence_pair=True 的帧不得进缓存
    assert all(not k[3] for k in ctx.devour.cache.items)        # 无 token 键

async def test_blocked_edit_writes_no_evidence(ctx):
    ctx.touch.stub_state = "blocked"
    before = ctx.db.count("frame_verification_evidence")
    r = await run_edit_op(ctx, "p", "t", 0, {"op": "split", "at_s": 1.0})
    assert r["evidence_written"] is False
    assert ctx.db.count("frame_verification_evidence") == before
