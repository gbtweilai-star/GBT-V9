# integrations/octop/bridge.py —— 把原生能力暴露成 Octop Agent 工具
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# Octop 的插件/工具扩展点以拉取后实际版本为准（MIT 仓库未承诺稳定插件 API），
# 因此这里走两条稳妥通道：① REST /api 挂工具调用 ② WebSocket 桥接聊天事件。
from core.swallow import swallow as _swallow
import os, json, httpx
from skills.native import SkillContext

OCTOP_BASE = os.environ.get("OCTOP_BASE", "http://127.0.0.1:8088")
OCTOP_TOKEN = os.environ.get("OCTOP_TOKEN", "")


class OctopBridge:
    def __init__(self, registry, ledger=None, brain=None):
        self.reg, self.led, self.brain = registry, ledger, brain
        # 无 token 时不带 Authorization（branding/health 是公开只读面）
        self.h = ({"Authorization": f"Bearer {OCTOP_TOKEN}"} if OCTOP_TOKEN else {})
        self.h["Content-Type"] = "application/json"

    # ── ① 工具调用：Octop 侧发起 → 落到我们的能力注册表（带审计）──
    async def invoke_tool(self, user, agent_id, session_id, name, args) -> dict:
        ctx = SkillContext(tentacle_id=f"octop:{agent_id}", task_id=session_id,
                           workspace=args.pop("__workspace", "."),
                           ledger=self.led, brain=self.brain)
        r = self.reg.call(name, args, ctx)
        return {"ok": r.ok, "output": r.output, "artifacts": r.artifacts,
                "warnings": r.warnings, "error": r.error,
                "usage": r.usage}

    # ── ② 能力清单推给 Octop（供其 Agent 发现可用工具）──
    def tool_manifest(self):
        """逐能力真自查后汇总成清单。**任一能力违反契约，也不许拖垮整条清单**。

        病因（2026-10-07 真机踩到）：本方法原先直接 `s.probe().__dict__`，只要注册表里
        有一项没实现 probe()（真机上是 `coder`/`voice`），**整条 manifest 直接抛**
        AttributeError，Octop 侧的能力发现面全灭（而 SkillRegistry 自己的 probe_all()
        是防御式的，两边口径不一致才露出这个洞）。
        改为：缺 probe / probe 抛异常的能力**如实标成不可用**，其余照常上报——降级不静默，
        也绝不因为一个坏苹果丢掉整筐。
        """
        out = []
        for n, s in self.reg.skills.items():
            try:
                av = s.probe()
                probe = (av.__dict__ if hasattr(av, "__dict__")
                         else {"ok": bool(getattr(av, "ok", False)), "detail": str(av)})
            except Exception as exc:                                  # noqa: BLE001
                probe = {"ok": False, "reason": f"probe 未实现/异常: {type(exc).__name__}: {exc}",
                         "detail": {}}
            out.append({"name": n, "version": getattr(s, "version", ""),
                        "kind": "native", "probe": probe})
        return out

    # ── ③ 聊天桥接：Octop WS 消息 → 主脑 → 回写（含语音回执）──
    async def relay_chat(self, agent_id, session_id, text):
        reply = self.brain.chat([{"role": "user", "content": text}], json_mode=False)
        if getattr(self.brain, "voice", None):
            try: self.brain.voice.enqueue(reply, event_id=f"octop-{session_id}")
            except Exception as e:
                _swallow(__file__, e)
        return reply

    def health(self):
        """Octop 存活探测：优先 openapi（开了 API docs 时），回退 /api/health（生产默认关闭 docs）。"""
        try:
            r = httpx.get(f"{OCTOP_BASE}/api/openapi.json", headers=self.h, timeout=5)
            if r.status_code == 200:
                return {"octop": True, "base": OCTOP_BASE, "probe": "openapi"}
            h = httpx.get(f"{OCTOP_BASE}/api/health", headers=self.h, timeout=5)
            ok = h.status_code == 200 and bool(h.json().get("ok", False))
            return {"octop": ok, "base": OCTOP_BASE, "probe": "health",
                    "detail": h.json() if h.status_code == 200 else h.text[:120]}
        except Exception as e:
            return {"octop": False, "error": str(e)}
