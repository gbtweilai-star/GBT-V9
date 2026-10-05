"""大脑阻塞上报端口。
铁律：卡点必须带 trace 和证据回报；不得静默吞掉阻塞状态。
"""
from typing import Protocol

from .types import Row


class Brain(Protocol):
    async def report_blocked(self, *, trace_id: str, stage: str, reason: str,
                             details: Row | None = None) -> None:
        """记录可追踪的阻塞事件，供大脑决策或等待下一条指令。

        上报失败须显式抛错或由调用方记录；不得将任务伪装成成功。
        """
        raise NotImplementedError
