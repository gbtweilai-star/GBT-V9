"""互斥队列端口。
铁律：同一资源的独占区间必须跨 worker 生效，不能只依赖进程内锁。
"""
from typing import AsyncContextManager, Protocol


class ExclusiveQueue(Protocol):
    def exclusive(self, name: str, owner: str) -> AsyncContextManager[None]:
        """返回独占执行上下文。

        同一 name 同时只能由一个 owner 持有；退出时释放，异常不得吞掉。
        后端须提供跨进程/worker 的互斥保证；获取失败或 lease 丢失须抛错。
        """
        raise NotImplementedError
