"""证据帧租约端口。
铁律：租约获取与 eviction 必须锁同一 artifact 行；批量获取全有或全无。
"""
from typing import Any, AsyncIterator, Protocol

from .types import LeaseId, Ref


class LeaseSession(Protocol):
    """一组租约的异步生命周期；读取和流传输期间须保持租约有效。"""

    async def __aenter__(self) -> "LeaseSession":
        """原子获取本 session 的全部 refs。

        任一 ref 不可用时全部回滚并抛 EvidenceUnavailable；不能返回部分成功。
        """
        raise NotImplementedError

    async def __aexit__(self, exc_type: type[BaseException] | None,
                        exc: BaseException | None, tb: Any) -> bool | None:
        """停止续租并幂等释放；包括异常退出路径。不得吞掉原始异常。"""
        raise NotImplementedError

    async def read(self, ref: Ref) -> bytes:
        """只读本 session 持有的 ref；读取前后检查 lease，有问题抛 LeaseLost。"""
        raise NotImplementedError

    def iter_bytes(self, ref: Ref, chunk_size: int = 262_144) -> AsyncIterator[bytes]:
        """有界内存分块读取；流期间续租失败须中止并抛 LeaseLost。"""
        raise NotImplementedError

    async def put_and_hold(self, content: bytes) -> Ref:
        """暂存、写入并校验内容寻址对象，再原子提升为 ready 并持有 lease。

        staged→ready 与 lease 创建必须在同一数据库事务中提交；失败不得留下
        可被 eviction 删除的未持锁 ready 对象。外部存储失败须显式报错。
        """
        raise NotImplementedError

    async def close(self) -> None:
        """停止续租并释放 session 的全部 lease；重复调用必须安全且只释放一次。"""
        raise NotImplementedError


class EvidenceLeases(Protocol):
    """批量租约服务；数据库行锁须与 eviction 共用同一 artifact 行。"""

    def hold(self, refs: list[Ref], *, holder: str, purpose: str,
             ttl_s: int | None = None) -> LeaseSession:
        """创建 session；refs 去重并排序，实际获取在 __aenter__ 中执行。"""
        raise NotImplementedError

    async def acquire_many_atomic(self, refs: list[Ref], *, holder: str,
                                  purpose: str, ttl_s: int) -> list[LeaseId]:
        """在单一事务中排序锁定 refs 并全有或全无地写入租约。"""
        raise NotImplementedError

    async def renew_many(self, lease_ids: list[LeaseId], *, holder: str) -> bool:
        """只续该 holder 的 active 且未过期租约；不得复活过期 lease。"""
        raise NotImplementedError

    async def release_many(self, lease_ids: list[LeaseId], *, holder: str) -> None:
        """幂等释放该 holder 的租约；不得释放其他 holder 的租约。"""
        raise NotImplementedError
