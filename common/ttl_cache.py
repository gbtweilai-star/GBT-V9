# common/ttl_cache.py —— 带"过期先给旧值 + 后台刷新"的小缓存
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么不用"过期就同步重算"：面板上几件重活（清单规模 5.8s、蓝牙扫描 7.2s、
# 生产闸门 6s、闭环状态 8s）如果按 TTL 到期就同步重算，页面会**每个周期卡一次**
# ——老板看到的就是"一停一停的"。所以过期的瞬间先把旧值交出去，后台线程去刷新；
# 数据最多滞后 TTL + 一次刷新时间，而页面永远是快的。
import threading
import time


class TTLCache:
    def __init__(self, ttl: float = 60.0, name: str = "") -> None:
        self.ttl = float(ttl)
        self.name = name
        self._v: dict = {}
        self._lock = threading.Lock()

    def get(self, key, fn):
        now = time.time()
        with self._lock:
            e = self._v.get(key)
            if e is not None and (now - e["t"]) < self.ttl:
                return e["v"]
            if e is not None and not e["refreshing"]:          # 过期：先给旧值
                e["refreshing"] = True
                threading.Thread(target=self._refresh, args=(key, fn), daemon=True).start()
                return e["v"]
        v = fn()                                               # 冷启动：只能等这一次
        with self._lock:
            self._v[key] = {"t": time.time(), "v": v, "refreshing": False}
        return v

    def _refresh(self, key, fn) -> None:
        v = None
        try:
            v = fn()
        except Exception:                                      # noqa: BLE001
            v = None
        with self._lock:
            e = self._v.get(key)
            if v is not None:
                self._v[key] = {"t": time.time(), "v": v, "refreshing": False}
            elif e is not None:                                # 刷新失败：旧值继续用，不谎报新数据
                e["refreshing"] = False

    def drop(self, *keys: str) -> None:
        with self._lock:
            for k in keys:
                self._v.pop(k, None)

    def info(self) -> dict:
        with self._lock:
            return {"name": self.name, "ttl": self.ttl, "keys": sorted(self._v)}
