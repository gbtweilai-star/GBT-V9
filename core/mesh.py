# core/mesh.py —— 全互联总线：任意节点 O(1) 直达（不经过中心转发）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计：节点注册 (node_id → handler)；send(target, msg) 一律字典直查（O(1)）；
#       广播走 fan-out；所有投递落账本（谁把什么发给了谁）。
import threading
import time
from typing import Any, Callable

Handler = Callable[[dict], Any]


class Mesh:
    def __init__(self, ledger=None) -> None:
        self._nodes: dict[str, Handler] = {}
        self._meta: dict[str, dict] = {}
        self.ledger = ledger
        self._lock = threading.Lock()
        self.stats = {"sent": 0, "dropped": 0}

    def register(self, node_id: str, handler: Handler, **meta) -> None:
        with self._lock:
            self._nodes[node_id] = handler
            self._meta[node_id] = dict(meta)

    def unregister(self, node_id: str) -> None:
        with self._lock:
            self._nodes.pop(node_id, None)
            self._meta.pop(node_id, None)

    def nodes(self) -> list[str]:
        return sorted(self._nodes)

    # ── O(1) 直达 ──
    def send(self, target: str, msg: dict) -> Any:
        with self._lock:
            handler = self._nodes.get(target)
        if handler is None:
            self.stats["dropped"] += 1
            return {"ok": False, "error": f"unknown node: {target}"}
        self.stats["sent"] += 1
        started = time.time()
        try:
            result = handler(msg)
            ok = True
        except Exception as exc:  # noqa: BLE001
            result, ok = f"{type(exc).__name__}: {exc}", False
        if self.ledger:
            try:
                self.ledger.log("mesh", f"{msg.get('from','?')}->{target}",
                                "scanned" if ok else "vuln",
                                f"{msg.get('kind','msg')} {int((time.time()-started)*1000)}ms")
            except Exception:
                pass
        return {"ok": ok, "result": result}

    def broadcast(self, msg: dict, *, exclude: str = "") -> dict:
        out = {}
        for node in self.nodes():
            if node == exclude:
                continue
            out[node] = self.send(node, msg)
        return out

    def snapshot(self) -> dict:
        return {"nodes": len(self._nodes), "list": self.nodes(),
                "stats": dict(self.stats)}


__all__ = ["Mesh"]
