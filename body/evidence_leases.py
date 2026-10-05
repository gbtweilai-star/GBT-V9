"""帧证据工件租约。
dev: 自由的风 · 本署名不可删除、不可篡改归属
铁律：租约获取/续租与 eviction 都按 artifact 行串行化；获取全有或全无；
      ready 与首个 lease 必须同事务提交。
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any, AsyncIterator

from body.ports.leases import EvidenceLeases as EvidenceLeasesPort
from body.ports.leases import LeaseSession as LeaseSessionPort
from body.ports.store import ArtifactStore as ArtifactStorePort
from body.ports.types import LeaseId, Ref


class EvidenceUnavailable(RuntimeError):
    """工件不存在、未就绪或已进入删除流程。"""


class LeaseLost(RuntimeError):
    """租约不存在、失效、过期或续租失败。"""


class _RenewConflict(RuntimeError):
    """内部信号：续租未能完整更新，应回滚事务。"""


class EvidenceLeases(EvidenceLeasesPort):
    """SQLite/PostgreSQL 共用租约实现。"""

    def __init__(self, db: Any, store: ArtifactStorePort, *,
                 ttl_s: int = 60, staging_ttl_s: int = 900) -> None:
        if ttl_s <= 0 or staging_ttl_s <= 0:
            raise ValueError("ttl_s and staging_ttl_s must be positive")
        self.db, self.store = db, store
        self.ttl_s, self.staging_ttl_s = ttl_s, staging_ttl_s

    def _tx(self):
        return self.db.transaction(immediate=(self.db.dialect == "sqlite"))

    def hold(self, refs: list[Ref], *, holder: str, purpose: str,
             ttl_s: int | None = None) -> "LeaseSession":
        ttl = self.ttl_s if ttl_s is None else ttl_s
        if ttl <= 0:
            raise ValueError("ttl_s must be positive")
        return LeaseSession(self, refs, holder=holder, purpose=purpose, ttl_s=ttl)

    async def _acquire(self, refs: list[Ref], *, holder: str, purpose: str,
                       ttl_s: int, conn: Any = None) -> list[LeaseId]:
        """在单一事务内排序锁定并全有或全无地获取租约。"""
        refs = sorted(set(refs))
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")

        async def _run() -> list[LeaseId]:
            for ref in refs:                                  # ① 排序锁定，全部 ready 才继续
                obj = await self.db.lock_artifact(ref)
                if obj is None or obj["state"] != "ready":
                    raise EvidenceUnavailable(f"artifact is not ready: {ref}")
            now = await self.db.db_now_epoch()
            ids: list[LeaseId] = []
            for ref in refs:                                  # ② 每个 ref 一条 active lease
                lease_id = uuid.uuid4().hex
                await self.db.execute(
                    """INSERT INTO frame_leases
                       (lease_id,ref,holder,purpose,acquired_epoch,expires_epoch,
                        renew_count,status)
                       VALUES (?,?,?,?,?,?,0,'active')""",
                    (lease_id, ref, holder, purpose, now, now + ttl_s))
                ids.append(lease_id)
            return ids

        if conn is not None:
            return await _run()                               # 调用方已在其事务内
        async with self._tx():
            return await _run()                               # 任一异常 → 全部回滚

    async def acquire_many_atomic(self, refs, *, holder, purpose, ttl_s) -> list[LeaseId]:
        return await self._acquire(refs, holder=holder, purpose=purpose, ttl_s=ttl_s)

    async def _renew_many(self, lease_ids: list[LeaseId], *, holder: str, ttl_s: int) -> bool:
        ids = sorted(set(lease_ids))
        if not ids:
            return True
        marks = ",".join("?" for _ in ids)
        try:
            async with self._tx():
                rows = await self.db.fetch_all(
                    f"""SELECT lease_id,ref,holder,status,expires_epoch
                        FROM frame_leases WHERE lease_id IN ({marks})""", tuple(ids))
                if len(rows) != len(ids):
                    return False
                for ref in sorted({r["ref"] for r in rows}):  # 与 eviction 同锁序：先 artifact
                    if await self.db.lock_artifact(ref) is None:
                        return False
                locked = []
                for lease_id in ids:                          # 再 lease
                    row = await self.db.lock_lease(lease_id)
                    if row is None:
                        return False
                    locked.append(row)
                now = await self.db.db_now_epoch()
                if any(r["holder"] != holder or r["status"] != "active"
                       or int(r["expires_epoch"]) <= now for r in locked):
                    return False                              # 不复活已过期/他人租约
                for lease_id in ids:
                    changed = await self.db.execute(
                        """UPDATE frame_leases
                           SET expires_epoch=?, renew_count=renew_count+1
                           WHERE lease_id=? AND holder=? AND status='active'
                             AND expires_epoch>?""",
                        (now + ttl_s, lease_id, holder, now))
                    if changed != 1:
                        raise _RenewConflict(lease_id)
            return True
        except _RenewConflict:
            return False

    async def renew_many(self, lease_ids, *, holder) -> bool:
        return await self._renew_many(lease_ids, holder=holder, ttl_s=self.ttl_s)

    async def release_many(self, lease_ids, *, holder) -> None:
        ids = sorted(set(lease_ids))
        if not ids:
            return
        marks = ",".join("?" for _ in ids)
        async with self._tx():                                # 幂等条件更新
            await self.db.execute(
                f"""UPDATE frame_leases SET status='released'
                    WHERE holder=? AND status='active' AND lease_id IN ({marks})""",
                (holder, *ids))


class LeaseSession(LeaseSessionPort):
    """租约 session；退出时停止续租并幂等释放。"""

    def __init__(self, service: EvidenceLeases, refs: list[Ref], *,
                 holder: str, purpose: str, ttl_s: int) -> None:
        self.service = service
        self.refs = sorted(set(refs))
        self.holder, self.purpose, self.ttl_s = holder, purpose, ttl_s
        self._held: dict[Ref, LeaseId] = {}
        self._lost = False
        self._entered = False
        self._closed = False
        self._renew_task: asyncio.Task | None = None
        self._close_lock = asyncio.Lock()
        self._put_lock = asyncio.Lock()

    async def __aenter__(self) -> "LeaseSession":
        if self._entered or self._closed:
            raise RuntimeError("lease session cannot be entered twice")
        ids = await self.service._acquire(
            self.refs, holder=self.holder, purpose=self.purpose, ttl_s=self.ttl_s)
        self._held.update(zip(self.refs, ids))
        self._entered = True
        self._renew_task = asyncio.create_task(self._renew_loop())
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        await self.close()
        return False

    @property
    def lease_ids(self) -> list[LeaseId]:
        """本 session 当前持有的租约 id（按 refs 顺序；未进入时为空）。"""
        return [self._held[ref] for ref in self.refs if ref in self._held]

    async def _renew_loop(self) -> None:
        interval = max(0.05, self.ttl_s / 3)                  # 续租节奏 ≈ ttl/3
        try:
            while True:
                await asyncio.sleep(interval)
                if self._held and not await self.service._renew_many(
                        list(self._held.values()), holder=self.holder, ttl_s=self.ttl_s):
                    self._lost = True
                    return
        except asyncio.CancelledError:
            raise
        except Exception:
            self._lost = True

    async def _check_lease(self, ref: Ref) -> None:
        if not self._entered or self._closed or self._lost:
            raise LeaseLost("lease session is not active")
        lease_id = self._held.get(ref)
        if lease_id is None:
            raise LeaseLost(f"session does not hold artifact: {ref}")
        row = await self.service.db.fetch_one(
            "SELECT holder,status,expires_epoch FROM frame_leases WHERE lease_id=?",
            (lease_id,))
        now = await self.service.db.db_now_epoch()
        if (row is None or row["holder"] != self.holder or row["status"] != "active"
                or int(row["expires_epoch"]) <= now):
            self._lost = True
            raise LeaseLost(f"lease is no longer active: {ref}")

    async def read(self, ref: Ref) -> bytes:
        await self._check_lease(ref)
        data = await self.service.store.get_bytes(ref)
        await self._check_lease(ref)                          # 读前后都校验
        return data

    async def iter_bytes(self, ref: Ref, chunk_size: int = 262_144) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        await self._check_lease(ref)
        async for chunk in self.service.store.iter_bytes(ref, chunk_size):
            await self._check_lease(ref)                      # 每块校验，丢租即中断
            yield chunk
        await self._check_lease(ref)

    async def put_and_hold(self, content: bytes) -> Ref:
        """staged → 外部写入 → 同一 DB 事务内 ready + 首个 lease。"""
        if not self._entered or self._closed or self._lost:
            raise LeaseLost("put_and_hold requires an active session")
        ref = hashlib.sha256(content).hexdigest()

        async with self._put_lock:
            if ref in self._held:                             # 本 session 已持有 → 直接复用
                await self._check_lease(ref)
                return ref

            # ① 创建/复用 staged 行（内容寻址去重；staged 不被普通 eviction 回收）
            async with self.service._tx():
                now = await self.service.db.db_now_epoch()
                await self.service.db.execute(
                    """INSERT INTO artifact_objects
                       (ref,state,bytes,created_epoch,last_access_epoch,staged_by,
                        staged_until_epoch)
                       VALUES (?,'staged',?,?,?,?,?)
                       ON CONFLICT(ref) DO NOTHING""",
                    (ref, len(content), now, now, self.holder,
                     now + self.service.staging_ttl_s))
                obj = await self.service.db.lock_artifact(ref)
                if obj is None:
                    raise EvidenceUnavailable(f"could not stage artifact: {ref}")
                if obj["state"] == "staged":
                    await self.service.db.execute(
                        """UPDATE artifact_objects SET staged_until_epoch=?
                           WHERE ref=? AND state='staged'""",
                        (now + self.service.staging_ttl_s, ref))
                    needs_upload = True
                elif obj["state"] == "ready":
                    needs_upload = False                      # 已有同样字节，跳过上传
                else:
                    raise EvidenceUnavailable(
                        f"artifact cannot be reused in state {obj['state']}: {ref}")

            # ② 事务外写对象；此处崩溃它仍是 staged，安全
            if needs_upload:
                await self.service.store.put_verified(ref, content)

            # ③ ready 与首个 lease 同一事务提交 —— 不存在"无租约的 ready"
            async with self.service._tx():
                obj = await self.service.db.lock_artifact(ref)
                if obj is None:
                    raise EvidenceUnavailable(f"artifact disappeared: {ref}")
                if obj["state"] == "staged":
                    if not await self.service.db.transition(ref, "staged", "ready"):
                        raise EvidenceUnavailable(f"could not publish artifact: {ref}")
                elif obj["state"] != "ready":
                    raise EvidenceUnavailable(
                        f"artifact cannot be published in state {obj['state']}: {ref}")
                now = await self.service.db.db_now_epoch()
                lease_id = uuid.uuid4().hex
                await self.service.db.execute(
                    """INSERT INTO frame_leases
                       (lease_id,ref,holder,purpose,acquired_epoch,expires_epoch,
                        renew_count,status)
                       VALUES (?,?,?,?,?,?,0,'active')""",
                    (lease_id, ref, self.holder, self.purpose, now, now + self.ttl_s))

            self._held[ref] = lease_id
            return ref

    async def close(self) -> None:
        async with self._close_lock:
            if self._closed:
                return
            task = self._renew_task
            if task is not None and task is not asyncio.current_task():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if self._held:
                await self.service.release_many(
                    list(self._held.values()), holder=self.holder)  # 失败则抛，可重试
            self._closed = True
            self._renew_task = None
