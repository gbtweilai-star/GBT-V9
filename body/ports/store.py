"""工件存储端口。
铁律：外部删除只能在数据库已 claim 为 deleting 后执行。
"""
from typing import AsyncIterator, Protocol

from .types import Ref


class ArtifactStore(Protocol):
    """本地或对象存储适配器；不得绕开数据库状态与 lease 规则。"""

    async def put_verified(self, ref: Ref, content: bytes) -> None:
        """写入 staged 内容寻址对象并校验 SHA-256 与 ref 一致。

        此方法不得自行将数据库对象标为 ready；任何写入或校验失败都须抛错。
        """
        raise NotImplementedError

    async def get_bytes(self, ref: Ref) -> bytes:
        """读取对象内容；对象缺失、校验失败或不可读时须显式报错。"""
        raise NotImplementedError

    def iter_bytes(self, ref: Ref, chunk_size: int = 262_144) -> AsyncIterator[bytes]:
        """以有界内存分块读取对象；错误不得转换为空流。"""
        raise NotImplementedError

    async def delete_local_and_r2(self, ref: Ref) -> None:
        """删除本地与 R2 副本。

        调用方须先将数据库状态 claim 为 deleting；外部删除失败须抛错，
        由调用方恢复或对账，不能静默成功。
        """
        raise NotImplementedError
