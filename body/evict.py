# body/evict.py
"""帧工件驱逐与 staging 回收。
dev: 自由的风 · 本署名不可删除、不可篡改归属

铁律：
- 驱逐与租约获取走【同一 artifact 行锁】；
- 删除顺序 = DB claim(ready→deleting) → 外部字节删除 → 提交(deleting→deleted)；
  外部删失败必须回滚成 ready 并重抛；
- staged 永不进普通驱逐，只由 StagingReclaimer 处理；
- 全被 pin 住时【不空转、不强删】，报 watermark_blocked_pinned。
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from body.ports.store import ArtifactStore

AlertFn = Callable[[str, dict], Awaitable[None]]


class _ReclaimerBase:
    def __init__(
        self, db: Any, store: ArtifactStore, alert: AlertFn | None = None
    ) -> None:
        self.db, self.store, self.alert = db, store, alert

    def _tx(self):
        return self.db.transaction(immediate=(self.db.dialect == "sqlite"))

    async def _alert(self, name: str, detail: dict) -> None:
        if self.alert is not None:
            await self.alert(name, detail)

    async def _transition(self, ref: str, source: str, target: str) -> None:
        """持行锁做条件状态迁移；不匹配就炸，绝不静默。"""
        async with self._tx():
            row = await self.db.lock_artifact(ref)
            if row is None or row["state"] != source:
                raise RuntimeError(
                    f"cannot transition artifact {ref!r}: expected {source!r}"
                )
            if not await self.db.transition(ref, source, target):
                raise RuntimeError(
                    f"conditional transition failed for artifact {ref!r}: "
                    f"{source!r}->{target!r}"
                )


class Evictor(_ReclaimerBase):
    """只在 DB 里原子 claim 之后才删 ready 工件。"""

    async def claim_for_eviction(self, ref: str, grace_s: int = 60) -> bool:
        if grace_s < 0:
            raise ValueError("grace_s must be >= 0")
        async with self._tx():
            await self.db.lock_key("body:evict:watermark")
            obj = await self.db.lock_artifact(ref)          # 与租约获取同一行锁
            if obj is None or obj["state"] != "ready":
                return False
            now = await self.db.db_now_epoch()
            lease = await self.db.fetch_one(
                "SELECT 1 FROM frame_leases "
                "WHERE ref=? AND status='active' AND expires_epoch>? LIMIT 1",
                (ref, now - grace_s),                       # grace 覆盖瞬时卡顿
            )
            if lease is not None:
                return False
            return await self.db.transition(
                ref, "ready", "deleting", guard="state='ready'"
            )

    async def evict(self, ref: str, grace_s: int = 60) -> bool:
        """不可回收返回 False；回收成功返回 True。"""
        if not await self.claim_for_eviction(ref, grace_s):
            return False

        try:
            await self.store.delete_local_and_r2(ref)
        except Exception:
            try:
                await self._transition(ref, "deleting", "ready")   # ★ 删失败 → 回 ready
            except Exception as rollback_error:
                raise RuntimeError(
                    f"delete failed and artifact {ref!r} could not be restored"
                ) from rollback_error
            raise

        async with self._tx():
            obj = await self.db.lock_artifact(ref)
            if obj is None or obj["state"] != "deleting":
                raise RuntimeError(f"artifact {ref!r} left deleting state")
            if not await self.db.transition(
                ref, "deleting", "deleted", guard="state='deleting'"
            ):
                raise RuntimeError(f"could not finalize deletion of {ref!r}")
            # 帧没了，但"曾经验证过"的结论要保留 → 只标记原始证据不可用
            await self.db.execute(
                "UPDATE frame_verification_evidence SET raw_available=0 "
                "WHERE ref=? OR baseline_ref=? OR result_ref=?",
                (ref, ref, ref),
            )
        return True

    async def _total_bytes(self) -> int:
        # 水位计量【含 staged】，但回收不动 staged
        row = await self.db.fetch_one(
            "SELECT COALESCE(SUM(bytes),0) AS total FROM artifact_objects "
            "WHERE state IN ('staged','ready')"
        )
        return int(row["total"] or 0)

    async def pinned_bytes(self, grace_s: int = 60) -> int:
        if grace_s < 0:
            raise ValueError("grace_s must be >= 0")
        now = await self.db.db_now_epoch()
        row = await self.db.fetch_one(
            "SELECT COALESCE(SUM(a.bytes),0) AS total FROM artifact_objects a "
            "WHERE a.state='ready' AND EXISTS ("
            "SELECT 1 FROM frame_leases l WHERE l.ref=a.ref "
            "AND l.status='active' AND l.expires_epoch>?)",
            (now - grace_s,),
        )
        return int(row["total"] or 0)

    async def _candidates(self, batch: int) -> list[dict]:
        async with self._tx():
            # 事务级选举只用于候选挑选；真正的正确性由下面每行的 claim 保证
            await self.db.lock_key("body:evict:watermark")
            return await self.db.fetch_all(
                "SELECT ref,bytes FROM artifact_objects WHERE state='ready' "
                "ORDER BY last_access_epoch ASC,ref ASC LIMIT ?",
                (batch,),
            )

    async def run_watermark_pass(
        self, high_water: int, grace_s: int = 60, batch: int = 500
    ) -> dict:
        if high_water < 0 or grace_s < 0 or batch <= 0:
            raise ValueError("high_water/grace_s must be >= 0 and batch > 0")

        freed = 0
        while await self._total_bytes() > high_water:
            candidates = await self._candidates(batch)
            if not candidates:
                pinned = await self.pinned_bytes(grace_s)
                status = ("watermark_blocked_pinned" if pinned
                          else "watermark_no_candidates")
                if pinned:
                    await self._alert(status, {"pinned_bytes": pinned})
                return {
                    "status": status,
                    "freed_bytes": freed,
                    "total_bytes": await self._total_bytes(),
                    "pinned_bytes": pinned,
                }

            progressed = False
            for candidate in candidates:
                if await self._total_bytes() <= high_water:
                    break
                if await self.evict(candidate["ref"], grace_s):
                    freed += int(candidate["bytes"])
                    progressed = True

            if not progressed:                          # 全被 pin → 不 spin、不强删
                pinned = await self.pinned_bytes(grace_s)
                status = ("watermark_blocked_pinned" if pinned
                          else "watermark_no_progress")
                if pinned:
                    await self._alert(status, {"pinned_bytes": pinned})
                return {
                    "status": status,
                    "freed_bytes": freed,
                    "total_bytes": await self._total_bytes(),
                    "pinned_bytes": pinned,
                }

        return {
            "status": "ok",
            "freed_bytes": freed,
            "total_bytes": await self._total_bytes(),
            "pinned_bytes": await self.pinned_bytes(grace_s),
        }


class StagingReclaimer(_ReclaimerBase):
    """回收过期的 staged 行；成功回收后置为 deleted（证据结论保留）。"""

    def __init__(
        self,
        db: Any,
        store: ArtifactStore,
        alert: AlertFn | None = None,
        *,
        retry_delay_s: int = 60,
    ) -> None:
        super().__init__(db, store, alert)
        if retry_delay_s <= 0:
            raise ValueError("retry_delay_s must be positive")
        self.retry_delay_s = retry_delay_s

    async def extend_staged_until(self, ref: str, ttl_s: int = 900) -> bool:
        """上传进行中续期 staging 租约；一旦被回收 claim 走就返回 False。"""
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        async with self._tx():
            row = await self.db.lock_artifact(ref)          # ★ 只锁一次
            if row is None or row["state"] != "staged":
                return False
            now = await self.db.db_now_epoch()
            await self.db.execute(
                "UPDATE artifact_objects SET staged_until_epoch=? "
                "WHERE ref=? AND state='staged'",
                (now + ttl_s, ref),
            )
            return True

    async def renew_staging(self, ref: str, ttl_s: int = 900) -> bool:
        return await self.extend_staged_until(ref, ttl_s)

    async def _claim_staged(self, ref: str) -> bool:
        async with self._tx():
            await self.db.lock_key("body:staging:reclaim")
            row = await self.db.lock_artifact(ref)
            if row is None or row["state"] != "staged":
                return False
            now = await self.db.db_now_epoch()
            if int(row["staged_until_epoch"]) >= now:       # 复核没过期才 claim
                return False
            return await self.db.transition(
                ref, "staged", "deleting", guard="state='staged'"
            )

    async def _restore_staged(self, ref: str) -> None:
        async with self._tx():
            row = await self.db.lock_artifact(ref)
            if row is None or row["state"] != "deleting":
                raise RuntimeError(f"staged artifact {ref!r} left deleting state")
            now = await self.db.db_now_epoch()
            if not await self.db.transition(ref, "deleting", "staged"):
                raise RuntimeError(f"could not restore staged artifact {ref!r}")
            await self.db.execute(
                "UPDATE artifact_objects SET staged_until_epoch=? "
                "WHERE ref=? AND state='staged'",
                (now + self.retry_delay_s, ref),            # 失败延后重试，不卡 deleting
            )

    async def _finish_staged(self, ref: str) -> None:
        await self._transition(ref, "deleting", "deleted")

    async def reclaim_expired(self, *, batch: int = 500) -> int:
        if batch <= 0:
            raise ValueError("batch must be positive")

        async with self._tx():
            await self.db.lock_key("body:staging:reclaim")
            now = await self.db.db_now_epoch()
            rows = await self.db.fetch_all(
                "SELECT ref FROM artifact_objects "
                "WHERE state='staged' AND staged_until_epoch<? "
                "ORDER BY staged_until_epoch ASC,ref ASC LIMIT ?",
                (now, batch),
            )

        reclaimed = 0
        for row in rows:
            ref = row["ref"]
            if not await self._claim_staged(ref):
                continue
            try:
                await self.store.delete_local_and_r2(ref)
            except Exception:
                try:
                    await self._restore_staged(ref)         # ★ 删失败 → 回 staged 延后
                except Exception as rollback_error:
                    raise RuntimeError(
                        f"staging delete failed and {ref!r} could not be restored"
                    ) from rollback_error
                raise
            await self._finish_staged(ref)
            reclaimed += 1
        return reclaimed
