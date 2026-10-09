# mesh_bind.py —— 双向绑定 + 认领 + 超时回收（修掉截图里前两个真坑）
import time
from dataclasses import dataclass

@dataclass
class Slot:
    id: str
    owner: str | None = None
    claimed_at: float | None = None      # 坑2：没有它，超时回收判据永不触发
    ttl: float = 300.0

class SlotBoard:
    """D1..D100 槽位板：认领/释放/回收，全部状态可查"""
    def __init__(self, n=100, ttl=300.0):
        self.slots = {f"D{i}": Slot(f"D{i}", ttl=ttl) for i in range(1, n+1)}

    def claim(self, sid, owner):
        s = self.slots[sid]
        if s.owner and not self._expired(s):
            return None                       # 已被占，拒绝
        s.owner, s.claimed_at = owner, time.time()
        return s                              # 坑1：认领挂在路由入口，不是列表末尾

    def _expired(self, s):
        return s.claimed_at is not None and time.time() - s.claimed_at > s.ttl

    def reclaim(self):
        freed = []
        for s in self.slots.values():         # 回收只动真超时的，不误伤
            if s.owner and self._expired(s):
                freed.append(s.id); s.owner, s.claimed_at = None, None
        return freed
