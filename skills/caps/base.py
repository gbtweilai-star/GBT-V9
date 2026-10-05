# skills/caps/base.py —— 离线能力基座：Capability / Availability / 注册表
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计：probe() 是真自查（离线可跑），run() 是真操作（去服务化，全部本地可复现）；
#       每个能力按 native-capabilities/octop.offline.manifest.json 的 id 注册。
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Availability:
    done: bool
    detail: str = ""
    extra: dict = field(default_factory=dict)

    def __dict__(self):  # type: ignore[override]
        return {"done": self.done, "detail": self.detail, "extra": self.extra}


@dataclass
class CapResult:
    ok: bool
    output: Any = None
    artifacts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    error: str | None = None
    usage: dict = field(default_factory=dict)


class Capability:
    """一个离线能力。子类实现 probe()/run()。"""

    id: str = ""
    version: str = "offline-1.0"
    equivalence: str = "full"
    description: str = ""

    def __init__(self, ledger: Any = None, workdir: str = "."):
        self.ledger = ledger
        self.workdir = workdir

    # ── 自查（必须真跑，不许返回静态 True）──
    def probe(self) -> Availability:
        raise NotImplementedError

    # ── 真操作（默认不支持 → 显式失败，不静默）──
    def run(self, request: dict | None = None) -> CapResult:
        raise NotImplementedError(f"capability {self.id} has no offline run()")

    # ── 账本小工具 ──
    def _log(self, table: str, columns: tuple[str, ...], values: tuple) -> None:
        led = self.ledger
        if led is None:
            return
        try:
            marks = ",".join("?" for _ in columns)
            sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})"
            with led._tx(write=True) as conn:
                conn.execute(sql, values)
                conn.commit()
        except Exception:
            # 账本不可用不应吞掉能力结果，但要在 warnings 里体现（由调用方处理）
            raise


class CapRegistry:
    """离线能力注册表：id → Capability 实例。"""

    def __init__(self, ledger: Any = None, workdir: str = "."):
        self.ledger = ledger
        self.workdir = workdir
        self.caps: dict[str, Capability] = {}

    def register(self, cap: Capability):
        cap.ledger = self.ledger
        if not getattr(cap, "workdir", None) or cap.workdir == ".":
            cap.workdir = self.workdir
        self.caps[cap.id] = cap
        return cap

    def get(self, cap_id: str) -> Capability | None:
        return self.caps.get(cap_id)

    def probe_all(self) -> dict:
        out = {}
        for cid, cap in self.caps.items():
            try:
                a = cap.probe()
                out[cid] = {"done": bool(a.done), "detail": a.detail}
            except Exception as exc:  # noqa: BLE001
                out[cid] = {"done": False, "detail": f"{type(exc).__name__}: {exc}"}
        return out

    def run(self, cap_id: str, request: dict | None = None) -> CapResult:
        cap = self.caps.get(cap_id)
        if cap is None:
            return CapResult(ok=False, error=f"unknown capability: {cap_id}")
        if not getattr(cap, "RUNNABLE", True):
            return CapResult(ok=False, error=f"capability not runnable: {cap_id}")
        started = time.time()
        try:
            r = cap.run(request or {})
            r.usage.setdefault("ms", int((time.time() - started) * 1000))
            return r
        except NotImplementedError as exc:
            return CapResult(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            return CapResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    def manifest(self) -> list[dict]:
        return [
            {"id": c.id, "version": c.version, "kind": "native",
             "description": c.description, "equivalence": c.equivalence}
            for c in self.caps.values()
        ]


def ok(output: Any = None, **kw) -> CapResult:
    return CapResult(ok=True, output=output, **kw)
