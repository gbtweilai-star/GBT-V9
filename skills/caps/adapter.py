# skills/caps/adapter.py —— 把 53 项离线能力（CapRegistry）适配成 SkillRegistry 的形状
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 逐行复核）：下游全部只认 SkillRegistry 的形状 ——
#   workflows/engine.py:38-40 校验写 n.skill in self.reg.skills · :156 调 self.reg.call；
#   workflows/catalog.py:41、skills/spec.py:75、core/commander.py:115-127、integrations/octop/bridge.py:42 同理。
#   而 server.py:1384 注入的是 CapRegistry（只有 .caps / .run）⇒ DAG 一校验就说“未注册能力”，
#   目录/指挥官/桥全看不见这 53 项 —— 能力串不起来不是引擎坏了，是两边形状不一样。
# 口径：**做一个适配器，不去改五个消费方**。适配后 53 项能力与原生技能走同一条链：
#   .skills（视图）· .caps（原件仍在）· .call() · .probe_all() · .get() · .manifest()
# 诚实边界：不替能力编 spec() —— 未声明 schema 就如实是未声明（catalog 会标 schema_declared=False）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from typing import Any

from skills.caps.base import CapRegistry
from skills.native import Availability, SkillResult
from senses.sqldialect import txn


class CapAsSkill:
    """单项能力的技能视图：让 cap 看起来像 skills/native.py 里的 NativeSkill。"""

    def __init__(self, cap, owner: "CapSkillRegistry"):
        self._cap = cap
        self._owner = owner
        self.name = cap.id
        self.version = getattr(cap, "version", "offline-1.0")

    # 只声明 probe/run，不声明 spec：schema 有没有是能力自己的事，适配器不替它编
    def probe(self) -> Availability:
        try:
            a = self._cap.probe()
            return Availability(bool(getattr(a, "done", False)),
                                str(getattr(a, "detail", "") or ""),
                                dict(getattr(a, "extra", {}) or {}))
        except Exception as exc:                              # noqa: BLE001
            return Availability(False, f"探测异常: {type(exc).__name__}: {exc}")

    def run(self, ctx=None, request=None) -> SkillResult:
        try:
            r = self._cap.run(request or {})
            return SkillResult(bool(r.ok), output=r.output,
                               artifacts=list(r.artifacts or []),
                               warnings=list(r.warnings or []),
                               usage=dict(r.usage or {}), error=r.error or "")
        except NotImplementedError as exc:
            return SkillResult(False, error=str(exc))
        except Exception as exc:                              # noqa: BLE001
            return SkillResult(False, error=f"{type(exc).__name__}: {exc}")

    def __getattr__(self, item):                              # equivalence/description 等原样透出
        return getattr(self._cap, item)


class CapSkillRegistry:
    """CapRegistry 的技能形状视图。原件不复制、不改写：caps 仍是那张 id→Capability 表。"""

    def __init__(self, caps: CapRegistry, ledger=None, brain=None):
        self.reg = caps
        self.caps: dict = caps.caps
        self.skills: dict = {cid: CapAsSkill(c, self) for cid, c in caps.caps.items()}
        self.led = ledger if ledger is not None else getattr(caps, "ledger", None)
        self.brain = brain
        self.panel = None
        self._health: dict = {}

    # ── 形状对齐 ──
    def register(self, cap):
        self.reg.register(cap)
        self.skills[cap.id] = CapAsSkill(cap, self)
        return self.skills[cap.id]

    def get(self, name):
        return self.skills.get(name)

    def probe_all(self) -> dict:
        for n, s in self.skills.items():
            self._health[n] = s.probe()
        return self._health

    def manifest(self) -> list:
        return self.reg.manifest()

    # ── 真调用（与 SkillRegistry.call 同语义：探测不过即拒 + 落账）──
    def call(self, name, request, ctx: Any = None) -> SkillResult:
        s = self.skills.get(name)
        if not s:
            return SkillResult(False, error=f"未注册能力: {name}")
        t0 = time.time()
        av = s.probe()
        r = (SkillResult(False, error=f"能力不可用: {av.reason}") if not av.ok
             else s.run(ctx, request))
        ms = int((time.time() - t0) * 1000)
        r.usage.setdefault("ms", ms)
        self._audit(name, request, r, ms, ctx)
        return r

    def run(self, cap_id: str, request: dict | None = None):
        """原始形状的入口（面板的 invoke 门用它，落账由调用方负责）。"""
        return self.reg.run(cap_id, request)

    def _audit(self, name, req, r, ms, ctx):
        if not self.led:
            return
        try:
            ph = "?" if getattr(self.led, "dialect", "sqlite") == "sqlite" else "%s"
            sql = ("INSERT INTO skill_calls(ts,skill,version,tentacle,task_id,ok,ms,engine,"
                   "request,result,artifacts,warnings,error) VALUES(" + ",".join([ph] * 13) + ")")
            with txn(self.led) as cur:
                cur.execute(sql, (
                    time.time(), name, self.skills[name].version,
                    getattr(ctx, "tentacle_id", "brain") or "brain",
                    getattr(ctx, "task_id", "") or "",
                    1 if r.ok else 0, ms, "octop-offline",
                    json.dumps(req or {}, ensure_ascii=False, default=str)[:4000],
                    json.dumps(r.output, ensure_ascii=False, default=str)[:4000],
                    json.dumps(r.artifacts, ensure_ascii=False)[:2000],
                    json.dumps(r.warnings, ensure_ascii=False)[:1000],
                    r.error or ""))
        except Exception as e:
            _swallow(__file__, e)

    def status(self) -> dict:
        return {n: {"version": s.version, "equivalence": getattr(s, "equivalence", "")}
                for n, s in self.skills.items()}


def as_skill_registry(caps: CapRegistry | None = None, *, ledger=None, brain=None) -> CapSkillRegistry:
    """把（或新建）一张离线能力表包成技能注册表。caps=None 时按本仓清单现建。"""
    if caps is None:
        from skills.caps.registry import build_caps_registry
        caps = build_caps_registry(ledger=ledger)
    return CapSkillRegistry(caps, ledger=ledger, brain=brain)


__all__ = ["CapSkillRegistry", "CapAsSkill", "as_skill_registry"]
