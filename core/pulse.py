# core/pulse.py —— 万能插脉冲 · dev: 自由的风 · 万物皆可插，万物皆可控
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计：脉冲 = 触手末梢的万能插头。任何外部对象（进程/文件/API/网页）插一个
#       "标准插座描述符" 就变成一根可扫可控的触手分支；优先结构化插座（秒级、零幻觉），
#       插不上再走坐标兜底（视觉路径）。
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

SOCKET_KINDS = ("process", "file", "api", "web")


@dataclass
class Socket:
    kind: str
    target: str
    options: dict = field(default_factory=dict)
    plugged_at: float = 0.0
    state: str = "unplugged"          # unplugged | plugged | failed
    detail: str = ""


class Pulse:
    def __init__(self, ledger=None, brain=None) -> None:
        self.ledger, self.brain = ledger, brain
        self.plugged: dict[str, Socket] = {}

    # ── 插入：结构化插座优先 ──
    def plug(self, socket: Socket) -> Socket:
        if socket.kind not in SOCKET_KINDS:
            socket.state, socket.detail = "failed", f"不支持的插座类型 {socket.kind}"
            return socket
        try:
            if socket.kind == "file":
                p = Path(socket.target)
                socket.state = "plugged" if p.exists() else "failed"
                socket.detail = f"exists={p.exists()} size={p.stat().st_size if p.exists() else 0}"
            elif socket.kind == "process":
                socket.state = "plugged"
                socket.detail = "process socket ready (dispatch via adapter)"
            else:                                    # api / web：有 URL 描述即算插上（真请求由适配器做）
                scheme = socket.target.split(":", 1)[0].lower()
                socket.state = "plugged" if scheme in ("http", "https") else "failed"
                socket.detail = f"scheme={scheme}"
        except Exception as exc:  # noqa: BLE001
            socket.state, socket.detail = "failed", f"{type(exc).__name__}: {exc}"
        socket.plugged_at = time.time()
        self.plugged[socket.target] = socket
        if self.ledger:
            try:
                self.ledger.log("pulse", f"plug:{socket.target}", "scanned",
                                f"{socket.kind} {socket.state}")
            except Exception:
                pass
        return socket

    # ── 分发：插上的走结构化通道 ──
    def dispatch(self, target: str, payload: dict) -> dict:
        sock = self.plugged.get(target)
        if sock is None or sock.state != "plugged":
            return {"ok": False, "error": "未插上（回退坐标兜底路径）"}
        return {"ok": True, "socket": sock.kind, "handled": True,
                "payload_keys": sorted(payload)}

    def unplug(self, target: str) -> bool:
        sock = self.plugged.pop(target, None)
        if sock:
            sock.state = "unplugged"
            return True
        return False

    def snapshot(self) -> dict:
        return {"plugged": {k: {"kind": v.kind, "state": v.state, "detail": v.detail}
                            for k, v in self.plugged.items()},
                "count": len(self.plugged)}


__all__ = ["Pulse", "Socket", "SOCKET_KINDS"]
