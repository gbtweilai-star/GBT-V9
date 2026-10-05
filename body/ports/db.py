"""数据库端口。
铁律：租约获取与 eviction 必须锁同一 artifact 行；时间判断使用数据库时钟。
"""
from typing import Any, AsyncContextManager, Protocol

from .types import ArtifactState, Epoch, LeaseId, Params, Ref, Row


class Db(Protocol):
    """双后端数据库契约；锁操作须在调用方事务内完成。"""

    def transaction(self, *, immediate: bool = False) -> AsyncContextManager[None]:
        """返回事务上下文：正常退出提交，异常退出回滚。

        SQLite 写事务须支持 BEGIN IMMEDIATE；PG 使用数据库事务。
        调用方负责确定事务边界，行锁和条件状态迁移须处在同一事务。
        """
        raise NotImplementedError

    async def execute(self, sql: str, params: Params = ()) -> Any:
        """执行参数化 SQL；值必须绑定，禁止拼接用户值。失败必须抛出异常。"""
        raise NotImplementedError

    async def fetch_one(self, sql: str, params: Params = ()) -> Row | None:
        """返回一行映射或 None；SQLite 与 PG 的行结构须一致。"""
        raise NotImplementedError

    async def fetch_all(self, sql: str, params: Params = ()) -> list[Row]:
        """返回行映射列表；分页调用方须使用稳定排序键。"""
        raise NotImplementedError

    async def db_now_epoch(self) -> Epoch:
        """返回数据库 UTC epoch 秒；租约、过期及回收判定不得依赖本地时钟。"""
        raise NotImplementedError

    def bind(self, position: int) -> str:
        """返回方言的位置占位符；参数值仍须单独绑定。"""
        raise NotImplementedError

    async def lock_artifact(self, ref: Ref) -> Row | None:
        """在当前事务内锁定 artifact 行。

        租约获取和 eviction 必须锁同一行，以串行化 ready→deleting 与 pin。
        PG 应使用行锁；SQLite 写路径应依靠写事务串行化。
        """
        raise NotImplementedError

    async def lock_lease(self, lease_id: LeaseId) -> Row | None:
        """在当前事务内锁定 lease 行；续租不得复活已过期租约。"""
        raise NotImplementedError

    async def lock_key(self, key: str) -> None:
        """在当前事务内按 key 串行化操作；PG 可使用事务级 advisory lock。"""
        raise NotImplementedError

    async def transition(self, ref: Ref, from_state: ArtifactState,
                         to_state: ArtifactState, *, guard: str = "",
                         params: Params = ()) -> bool:
        """条件迁移 artifact 状态。

        必须与 lock_artifact 位于同一事务；并发迁移只能有一个赢家。
        guard 只能来自可信的静态 SQL 片段，值仍须绑定。状态不匹配返回
        False；数据库错误不得伪装成 False。
        """
        raise NotImplementedError
