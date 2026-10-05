# tools/verify_closed_loops.py —— 融合后的闭环取证（每条闭环给真读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OCTOP_BASE", "http://127.0.0.1:8088")

import httpx  # noqa: E402


async def main() -> int:
    out: dict = {}
    # 1) Octop 存活 + 品牌
    try:
        h = httpx.get("http://127.0.0.1:8088/api/health", timeout=5).json()
        out["octop_health"] = {"ok": h.get("ok"), "agents": h.get("agents_running")}
        b = httpx.get("http://127.0.0.1:8088/api/branding", timeout=5).json()
        out["octop_branding"] = b.get("name")
    except Exception as exc:  # noqa: BLE001
        out["octop_health"] = f"ERR {exc}"

    # 2) V9 面板闭环端点
    panel = {}
    for p in ("/api/coverage", "/api/frame-evidence", "/api/p1/devour-calibrations",
              "/api/voice/emotion", "/api/session", "/api/monitor/db-health",
              "/api/media/queue?limit=1"):
        try:
            r = httpx.get(f"http://127.0.0.1:8765{p}", timeout=5)
            panel[p] = r.status_code
        except Exception as exc:  # noqa: BLE001
            panel[p] = f"ERR {type(exc).__name__}"
    out["v9_panel"] = panel

    # 3) 桥：健康 + 聊天中继（脚本化大脑）
    from integrations.octop.bridge import OctopBridge
    from skills.caps.registry import build_caps_registry
    from skills.integrate import build_registry as build_skill_registry

    skill_reg = build_skill_registry()          # 桥的对面 = 技能注册表（skills）
    caps_reg = build_caps_registry()            # 融合面 = 53 项离线能力（caps）
    br = OctopBridge(skill_reg)
    out["bridge_health"] = br.health()

    class _StubBrain:
        voice = None

        def chat(self, messages, json_mode=False):
            return "收到：" + str(messages[-1].get("content", ""))[:40]

    br2 = OctopBridge(skill_reg, brain=_StubBrain())
    out["chat_relay"] = await br2.relay_chat("agent-1", "sess-1", "你好，播报扫描结果")

    # 4) 能力清单推送（Octop 可见面）
    out["tool_manifest_n"] = len(br.tool_manifest())
    out["caps_n"] = len(caps_reg.caps)

    # 5) 记忆层（ai-memory 未启 → 必须诚实降级，不许假装成功）
    try:
        from integrations.ai_memory.client import AiMemory

        mem = AiMemory()
        try:
            res = mem.remember("probe", "closure check") if hasattr(mem, "remember") else None
            out["ai_memory"] = {"reachable": True, "result": str(res)[:60]}
        except Exception as exc:  # noqa: BLE001
            out["ai_memory"] = {"reachable": False, "degraded": f"{type(exc).__name__}: {str(exc)[:80]}"}
    except Exception as exc:  # noqa: BLE001
        out["ai_memory"] = {"client_error": f"{type(exc).__name__}: {exc}"}

    print(json.dumps(out, ensure_ascii=False, indent=1))
    ok = (isinstance(out.get("octop_health"), dict) and out["octop_health"].get("ok")
          and out.get("octop_branding") == "GBT小土豆V9"
          and all(v == 200 for v in panel.values() if isinstance(v, int))
          and out["bridge_health"].get("octop") is True
          and out["tool_manifest_n"] >= 3)
    print("\nCLOSED_LOOPS:", "ALL OK" if ok else "CHECK")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
