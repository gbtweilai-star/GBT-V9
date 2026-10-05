"""时间线端口。
铁律：revision 必须持久化且并发安全，不能只在进程内递增。
"""
from typing import Protocol

from .types import Row


class Timeline(Protocol):
    async def revision(self, project_id: str) -> int:
        """返回项目当前持久化 revision；无法读取时须显式报错。"""
        raise NotImplementedError

    async def bump_revision(self, project_id: str) -> int:
        """原子递增项目 revision 并返回新值；并发调用不得丢失更新。"""
        raise NotImplementedError

    async def media_at(self, project_id: str, time_s: float) -> Row | None:
        """返回指定项目时间点对应的媒体/片段信息；不存在时返回 None。"""
        raise NotImplementedError
