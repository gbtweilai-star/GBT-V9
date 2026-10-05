# parity/subsystems.py —— 子系统 probe 注册与调用
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 只用协议 parity_probe(ctx), 不猜模块内部签名;
#       缺函数/导入失败 = 失败证据(不默认通过); 不写业务表/不连生产

from __future__ import annotations
import importlib, os, sqlite3, traceback
from dataclasses import dataclass, field
from typing import Any

PROBE_FUNC = "parity_probe"


# ─────────────────────────────────────────────
# ctx：只注入隔离资源
# ─────────────────────────────────────────────
@dataclass
class SubsystemCtx:
    sandbox: str
    _ledger: Any = None

    def temp_ledger(self):
        """每个 probe 一个独立 SQLite（在 PARITY_TEMP_ROOT 内），用完即弃。"""
        if self._ledger is not None:
            return self._ledger
        root = os.environ.get("PARITY_TEMP_ROOT") or self.sandbox
        path = os.path.join(root, "subsys-probe.sqlite")
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        from audit.ledger import SQLiteLedger      # ← 对齐点：你的类名
        self._ledger = SQLiteLedger(path)
        return self._ledger


@dataclass(frozen=True)
class Entry:
    name: str
    module_path: str
    optional_reason: str | None = None      # 标了就是"已知未实现"，只警告不阻断


class SubsystemRegistry:
    def __init__(self):
        self._e: dict[str, Entry] = {}

    def register(self, name: str, module_path: str, *, optional_reason: str | None = None):
        if name in self._e:
            raise ValueError(f"重复登记子系统: {name}")
        self._e[name] = Entry(name, module_path, optional_reason)

    def get(self, name: str) -> Entry | None:
        return self._e.get(name)

    def names(self) -> list[str]:
        return sorted(self._e)

    async def run_probe(self, name: str, ctx: SubsystemCtx) -> dict:
        entry = self.get(name)
        if entry is None:
            raise LookupError(f"子系统未登记: {name}")

        # ① 真导入（模块被删/改名 → 失败证据，不静默）
        try:
            mod = importlib.import_module(entry.module_path)
        except Exception as e:
            return {"passed": False, "observed": {"module": entry.module_path},
                    "evidence": {"error": f"{type(e).__name__}: {e}",
                                 "traceback": traceback.format_exc(limit=3)},
                    "reason": "import_error"}

        # ② 协议检查：没有 parity_probe 就是未实现
        fn = getattr(mod, PROBE_FUNC, None)
        if not callable(fn):
            return {"passed": False, "observed": {"module": entry.module_path},
                    "evidence": {"error": f"缺少 {PROBE_FUNC}(ctx)"},
                    "reason": "probe_not_implemented"}

        # ③ 真调用
        try:
            res = fn(ctx)
            if hasattr(res, "__await__"):
                res = await res
        except Exception as e:
            return {"passed": False, "observed": {"module": entry.module_path},
                    "evidence": {"error": f"{type(e).__name__}: {e}",
                                 "traceback": traceback.format_exc(limit=3)},
                    "reason": "probe_error"}

        # ④ 契约校验：passed 必须有 evidence，封堵空壳
        if not isinstance(res, dict) or not {"passed", "observed", "evidence"} <= res.keys():
            return {"passed": False, "observed": {"module": entry.module_path},
                    "evidence": {"error": "probe 必须返回 passed/observed/evidence"},
                    "reason": "contract_violation"}
        if res["passed"] and not res["evidence"]:
            return {"passed": False, "observed": res.get("observed", {}),
                    "evidence": {"error": "passed=True 但 evidence 为空"},
                    "reason": "evidence_invalid"}
        return res


REGISTRY = SubsystemRegistry()


def register_subsystems() -> SubsystemRegistry:
    # 能力 id → 模块路径（★按你真实路径改）
    REGISTRY.register("brain.intent",            "brain.intent")
    REGISTRY.register("brain.decision_router",   "brain.decision_router")
    REGISTRY.register("brain.reflect",           "brain.reflect")
    REGISTRY.register("brain.prompt",            "brain.prompt")
    REGISTRY.register("brain.memory",            "brain.memory")
    REGISTRY.register("tentacle.contract",       "tentacle.contract")
    REGISTRY.register("tentacle.hook",           "tentacle.hook")
    REGISTRY.register("tentacle.mesh",           "tentacle.mesh")
    REGISTRY.register("tentacle.identity",       "tentacle.identity",
                      optional_reason="永久邮箱外部注册未实现；offline 只验本地身份分配")
    REGISTRY.register("workflows.engine",        "workflows.engine")
    REGISTRY.register("workflows.gate",          "workflows.gate")
    REGISTRY.register("audit.ledger",            "audit.ledger")
    REGISTRY.register("audit.pg_pool",           "audit.pg_pool")
    REGISTRY.register("audit.scale",             "audit.scale")
    REGISTRY.register("sched.queue",             "sched.queue")
    REGISTRY.register("panel.db",                "panel.db")
    REGISTRY.register("panel.server",            "panel.server")
    REGISTRY.register("auth.session",            "auth.session")
    REGISTRY.register("alerts.fsm",              "alerts.fsm")
    REGISTRY.register("skills.native",           "skills.native")
    return REGISTRY
