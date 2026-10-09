# tests/contracts/test_frame_evidence_contract.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 断言查库真读数(行数/状态), 不是"函数被调用过";
#       SQLite 与 PG 跑同一套; 任一后端不通过就不算契约达成。
import asyncio, hashlib, os, uuid
import pytest, pytest_asyncio
from body.evidence_leases import EvidenceUnavailable

pytestmark = pytest.mark.asyncio


# ── 后端参数化 ─────────────────────────────────────────────
@pytest.fixture(params=["sqlite", "postgres"])
def backend(request): return request.param


@pytest.fixture
def pg_dsn():
    dsn = os.getenv("TEST_DATABASE_URL")
    if dsn:
        yield dsn      # ★ 修：fixture 必须 yield（原来 return ⇒ pytest 报 did not yield a value）
        return
    if not os.getenv("RUN_PG_CONTRACTS"):
        pytest.skip("设 RUN_PG_CONTRACTS=1 或 TEST_DATABASE_URL 才跑 PG 契约")
    try:
        from testcontainers.postgres import PostgresContainer
        c = PostgresContainer("postgres:16"); c.start()
    except Exception as exc:
        pytest.skip(f"PG testcontainer 不可用: {exc}")
    yield c.get_connection_url().replace("postgresql+psycopg2://", "postgresql://")
    c.stop()


@pytest_asyncio.fixture
async def system(backend, request, tmp_path):
    """唯一工程相关缝隙: 接真实适配器, 返回 db/leases/evictor/staging_reclaimer/store。"""
    from tests.adapter_factory import build_contract_system
    dsn = (request.getfixturevalue("pg_dsn") if backend == "postgres"
           else str(tmp_path / "contract.sqlite"))
    svc = await build_contract_system(backend=backend, dsn=dsn)
    await svc.install_schema()
    yield svc
    await svc.close()


class FakeArtifactStore:
    """只假外部对象字节; DB 状态全真。"""
    def __init__(self): self.objects = {}
    async def put_verified(self, ref, content):
        assert hashlib.sha256(content).hexdigest() == ref      # 内容寻址真校验
        self.objects[ref] = content
    async def get_bytes(self, ref): return self.objects[ref]
    async def delete_local_and_r2(self, ref): self.objects.pop(ref, None)


async def count(db, table, where, params=()):
    row = await db.fetch_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", params)
    return int(row["n"])


async def seed_object(db, ref, state="ready", *, staged_until=0):
    now = await db.db_now_epoch()
    await db.execute(
        "INSERT INTO artifact_objects(ref,state,bytes,created_epoch,last_access_epoch,"
        "staged_by,staged_until_epoch) VALUES(?,?,?,?,?,?,?)",
        (ref, state, 1, now, now, "test", staged_until))

async def test_transaction_rollback_leaves_no_rows(system):
    db, ref = system.db, "rollback-" + uuid.uuid4().hex
    with pytest.raises(RuntimeError):
        async with db.transaction(immediate=(db.dialect == "sqlite")):
            now = await db.db_now_epoch()
            await db.execute("INSERT INTO artifact_objects(ref,state,bytes,created_epoch,"
                             "last_access_epoch) VALUES(?,'staged',1,?,?)", (ref, now, now))
            await db.execute("INSERT INTO frame_leases(lease_id,ref,holder,purpose,"
                             "acquired_epoch,expires_epoch,renew_count,status) "
                             "VALUES(?,?,?,?,?,?,0,'active')",
                             ("lease-" + uuid.uuid4().hex, ref, "test", "rollback", now, now+60))
            raise RuntimeError("force rollback")
    assert await count(db, "artifact_objects", "ref=?", (ref,)) == 0
    assert await count(db, "frame_leases", "ref=?", (ref,)) == 0

async def test_acquire_is_all_or_nothing_and_sorted(system, monkeypatch):
    db, leases = system.db, system.leases
    await seed_object(db, "r1", "ready")                       # r2 故意不存在
    with pytest.raises(EvidenceUnavailable):
        async with leases.hold(["r1", "r2"], holder="aon", purpose="test"):
            pytest.fail("部分获取成功不得进入")
    assert await count(db, "frame_leases", "ref=?", ("r1",)) == 0   # 整体回滚

    # 记录加锁顺序: 适配器必须走 db.lock_artifact()
    from contextvars import ContextVar
    orders = ContextVar("orders", default=None)
    orig = db.lock_artifact
    async def rec(ref):
        t = orders.get()
        if t is not None: t.append(ref)
        return await orig(ref)
    monkeypatch.setattr(db, "lock_artifact", rec)
    await seed_object(db, "r2", "ready")

    async def acquire(refs, holder):
        trace = []; token = orders.set(trace)
        try:
            s = leases.hold(refs, holder=holder, purpose="sorted")
            await s.__aenter__(); return s, trace
        finally:
            orders.reset(token)

    results = await asyncio.wait_for(asyncio.gather(
        acquire(["r1", "r2"], "forward"), acquire(["r2", "r1"], "reverse")), timeout=10)
    sessions = [s for s, _ in results]                  # gather 返回结果列表, 逐项解包
    traces = [t for _, t in results]
    try:
        assert all(t == ["r1", "r2"] for t in traces), traces   # 都按排序后加锁（失败时打印实际顺序）
    finally:
        await asyncio.gather(*(s.close() for s in sessions))

async def test_acquire_vs_eviction_has_one_winner(system):
    db, leases, evictor = system.db, system.leases, system.evictor
    for n in range(10):
        ref = f"race-{n}-{uuid.uuid4().hex}"
        await seed_object(db, ref, "ready")
        gate = asyncio.Event()

        async def acquire():
            await gate.wait()
            s = leases.hold([ref], holder=f"race-{n}", purpose="race")
            try: await s.__aenter__(); return s
            except EvidenceUnavailable: return None
        async def evict():
            await gate.wait()
            return await evictor.claim_for_eviction(ref, grace_s=0)

        tasks = [asyncio.create_task(acquire()), asyncio.create_task(evict())]
        gate.set()                                             # ★ 建好任务再放行 → 真并发
        a, claimed = await asyncio.wait_for(asyncio.gather(*tasks), timeout=10)

        row = await db.fetch_one("SELECT state FROM artifact_objects WHERE ref=?", (ref,))
        active = await count(db, "frame_leases",
                             "ref=? AND status='active' AND expires_epoch>?",
                             (ref, await db.db_now_epoch()))
        assert (a is not None) != bool(claimed), "必须恰好一方赢"   # ★ 核心断言
        if a is not None:
            assert row["state"] == "ready" and active >= 1
            await a.close()
        else:
            assert claimed is True and row["state"] == "deleting" and active == 0

async def test_put_and_hold_and_staging_reclaim(system):
    db, leases, evictor = system.db, system.leases, system.evictor
    payload = b"immutable-frame-bytes"

    async with leases.hold([], holder="put-test", purpose="frame") as pinned:
        ref = await pinned.put_and_hold(payload)
        row = await db.fetch_one("SELECT state FROM artifact_objects WHERE ref=?", (ref,))
        active = await count(db, "frame_leases",
                             "ref=? AND status='active' AND expires_epoch>?",
                             (ref, await db.db_now_epoch()))
        assert row["state"] == "ready" and active >= 1         # ★ ready 与 lease 同时存在

    # staged 永不进 eviction
    staged = "staged-" + uuid.uuid4().hex
    await seed_object(db, staged, "staged", staged_until=(await db.db_now_epoch()) + 3600)
    assert await evictor.claim_for_eviction(staged, grace_s=0) is False
    assert (await db.fetch_one("SELECT state FROM artifact_objects WHERE ref=?", (staged,)))["state"] == "staged"

    # 过期 staged → leader 回收成 deleted
    expired = "expired-" + uuid.uuid4().hex
    await seed_object(db, expired, "staged", staged_until=(await db.db_now_epoch()) - 1)
    await system.staging_reclaimer.reclaim_expired()
    assert (await db.fetch_one("SELECT state FROM artifact_objects WHERE ref=?", (expired,)))["state"] == "deleted"

async def test_expired_lease_cannot_be_renewed(system):
    db, leases = system.db, system.leases
    await seed_object(db, "renew-test", "ready")
    ids = await leases.acquire_many_atomic(["renew-test"], holder="h", purpose="t", ttl_s=60)
    now = await db.db_now_epoch()
    await db.execute("UPDATE frame_leases SET expires_epoch=? WHERE lease_id=?", (now-1, ids[0]))
    assert await leases.renew_many(ids, holder="h") is False
    row = await db.fetch_one("SELECT expires_epoch FROM frame_leases WHERE lease_id=?", (ids[0],))
    assert row["expires_epoch"] == now - 1                    # 没被复活

async def test_release_is_idempotent(system):
    db, leases = system.db, system.leases
    await seed_object(db, "release-test", "ready")
    s = leases.hold(["release-test"], holder="h", purpose="t"); await s.__aenter__()
    ids = list(s.lease_ids)
    await s.close(); await s.close()                          # 调两次
    assert await count(db, "frame_leases", "ref=? AND status='active'", ("release-test",)) == 0
    assert await count(db, "frame_leases", "lease_id=? AND status='released'", (ids[0],)) == 1
