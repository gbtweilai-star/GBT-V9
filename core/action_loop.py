# core/action_loop.py —— 动手操控层总闭环（把术语/执行器/证据/回滚/播报串成一条链）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要有它：之前各层各自能跑（术语能译、GUI 能动、沙箱能回滚），但没有**一条链**：
#   主人一句话 → 译解 → 选执行器 → 动手 → 采证据 → 验收 → 不过就回滚 → 播报结果。
# 本模块就是那一条链，也是触手/指挥官唯一该调的动手入口。
#
# 路由表（按术语的 skill 决定谁来动手）：
#   exec.gui_agent → 桌面操控（core/gui_agent，默认 dry_run；要真动必须带授权令牌）
#   exec.sandbox   → 受限执行（core/sandbox_exec：写时复制 + 验收 + 回滚）
#   read_*         → 只读读数（body/tools，一个字节都不写）
# 纪律：听不懂不动手；高风险先要授权；失败的动手一定回滚并如实报；取不到数不编数。
from core.swallow import swallow as _swallow
import time
import uuid

from core.sandbox_exec import CowWorkspace, SandboxPolicy, run_loop
from skills.terminology import translate

# 术语的 intent/skill → 执行器
SANDBOX_INTENTS = ("apply_fix", "run_command", "run_tests", "patch_file")
GUI_INTENTS = ("click_element", "type_text", "observe_first")
READ_INTENTS = ("read_snapshot", "vault_recall", "biz_quote", "biz_invoice")


def resolve_order(text: str, *, target: str | None = None, steps=None,
                  mode: str = "cow") -> dict:
    """一句话（或直接给计划）→ 统一工单：谁动手、动什么、在哪个目录、怎么验收。"""
    t = translate(text) if text else {"matched": False, "reason": "empty"}
    if steps:                                     # 明确给了计划 → 直接当沙箱工单
        return {"ok": True, "executor": "exec.sandbox", "intent": "run_command",
                "target": target, "steps": list(steps), "mode": mode,
                "translation": t, "need_grant": False}
    if not t.get("matched"):
        return {"ok": False, "reason": "not_understood", "translation": t,
                "say": "没听懂，先别动手。", "suggestions": t.get("candidates")}
    intent, skill = t.get("intent", ""), t.get("skill", "")
    if intent in READ_INTENTS or skill in ("body.tools", "obsidian", "biz", "voice"):
        return {"ok": True, "executor": "read", "intent": intent, "skill": skill,
                "translation": t, "need_grant": False}
    if intent in GUI_INTENTS or skill == "exec.gui_agent":
        return {"ok": True, "executor": "exec.gui_agent", "intent": intent,
                "translation": t, "need_grant": True,          # 碰鼠标键盘必须有授权
                "params": t.get("params", {})}
    if intent in SANDBOX_INTENTS or skill == "exec.sandbox":
        if not target:
            return {"ok": False, "reason": "need_target",
                    "say": "要在哪个目录动手？（--target）", "translation": t}
        return {"ok": True, "executor": "exec.sandbox", "intent": intent,
                "target": target, "steps": steps or [],
                "mode": mode, "translation": t, "need_grant": False}
    return {"ok": False, "reason": "no_executor", "translation": t,
            "say": f"我听懂是「{t.get('term')}」，但它还不接执行器。"}


def run_order(order: dict, *, ledger=None, director=None, accept=None,
              grant=None, now_fn=time.time, **kw) -> dict:
    """执行统一工单。返回统一结构：{ok, stage, evidence, rollback?, say}。"""
    t0 = time.time()
    oid = "ord-" + uuid.uuid4().hex[:10]
    if not order.get("ok"):
        return {"ok": False, "order_id": oid, "stage": "resolve",
                "say": order.get("say") or "没听懂，先别动手。",
                "translation": order.get("translation")}

    ex = order["executor"]
    out: dict = {"order_id": oid, "executor": ex, "intent": order.get("intent")}

    # ── ① 只读：不写、不猜，取不到就说取不到 ──
    if ex == "read":
        out.update({"ok": True, "stage": "read",
                    "say": f"这是只读问询（{order.get('intent')}），交 /api/ai/ask 作答。"})
        _audit(ledger, oid, out)
        return out

    # ── ② 桌面操控：**有授权就真动手**；没授权只规划（授权是唯一闸门）──
    if ex == "exec.gui_agent":
        from core.gui_agent import GuiAgent, PlannerUnavailable
        # ★2026-10-08 拆中间商（主人：「手都在，却总要停下来报卡点」）：
        #   这里原先**写死** dry_run=True，于是就算主人签了授权令牌，手也永远只规划不动手 ——
        #   连嘴→手那条链（core/voice_control.order → AL.handle）也一起被吞掉。
        #   GuiAgent 自己的开关语义是「真动作需显式 allow_actions=True」(gui_agent.py:13)，
        #   所以改为「按有没有授权决定」。**没授权仍然只规划 —— 闸门一点没松。**
        allow = bool(grant)
        try:
            ag = GuiAgent(ledger=ledger, dry_run=True, allow_actions=allow)
        except PlannerUnavailable as exc:
            out.update({"ok": False, "stage": "gui", "reason": str(exc),
                        "say": "没有可用的规划器，这次不动手。"})
            _audit(ledger, oid, out)
            return out
        res = ag.run(order.get("task") or order["translation"]["raw"], grant=grant, **kw)
        out.update({"ok": bool(res.get("ok")), "stage": "gui",
                    "dry_run": res.get("dry_run"),
                    "mode": "real（已授权）" if allow else "plan（无授权，只规划）",
                    "reason": res.get("reason"),
                    "evidence": {"steps": res.get("steps", []),
                                 "task_id": res.get("task_id")},
                    "say": _gui_say(res)})
        _audit(ledger, oid, out)
        _notify(director, out)
        return out

    # ── ③ 受限执行：写时复制 + 验收 + 失败回滚 ──
    if ex == "exec.sandbox":
        if not order.get("target"):
            out.update({"ok": False, "stage": "sandbox", "reason": "need_target",
                        "say": "要在哪个目录动手？"})
            _audit(ledger, oid, out)
            return out
        ws = CowWorkspace(order["target"])
        pol = SandboxPolicy(mode=order.get("mode", "cow"))
        res = run_loop(order.get("steps") or [], ws, policy=pol, accept=accept)
        out.update({"ok": bool(res.get("ok")), "stage": "sandbox",
                    "reason": res.get("reason"),
                    "evidence": res.get("evidence") or {"steps": res.get("steps"),
                                                        "diff": res.get("diff")},
                    "rollback": res.get("rollback"),
                    "say": _sandbox_say(res)})
        out["ms"] = int((time.time() - t0) * 1000)
        _audit(ledger, oid, out)
        _notify(director, out)
        return out

    out.update({"ok": False, "stage": "dispatch", "reason": "unknown_executor",
                "say": "这条链不认识该执行器。"})
    _audit(ledger, oid, out)
    return out


# ── 播报文案：成/败都要说人话，失败必须说清"已回滚" ──
def _sandbox_say(res: dict) -> str:
    if res.get("ok"):
        n = len((res.get("diff") or {}).get("changes") or [])
        return f"动手完成，改了 {n} 个文件，证据已留。"
    if res.get("rollback"):
        return f"动手失败（{res.get('reason')}），已经回滚，原件没被动。"
    return f"动手没成功：{res.get('reason')}"


def _gui_say(res: dict) -> str:
    # 视觉钉死：动手前必须有新鲜取景（主人令：禁传统瞎子操作）
    from core.senses_gate import require_eye as _re
    _eye = _re()
    if not _eye.get("ok"):
        return {"ok": False, "拒动": True, "在哪一步": "①眼", "读数": _eye}
    if res.get("reason") == "needs_confirm":
        return "这一步要碰鼠标键盘：已按【危险类】停下并登记（等授权到位自动续跑），不打断你。"
    if res.get("dry_run"):
        return "我先只做了规划（没碰你的鼠标键盘），你确认后我再真动。"
    return "桌面操作完成。" if res.get("ok") else f"桌面操作没成功：{res.get('reason')}"


def _audit(ledger, oid, out) -> None:
    """每次动手落审计（有账本才落，落不了不假装）。"""
    if ledger is None:
        return
    try:
        import json
        rec = getattr(ledger, "record_alert", None)
        if callable(rec):
            kind = "action_loop_ok" if out.get("ok") else "action_loop_failed"
            # 这里用 blocked 类记录更贴切：只留痕，不惊动告警面
        blk = getattr(ledger, "record_blocked", None)
        if callable(blk):
            import asyncio
            r = blk("action_loop", {"order_id": oid, "executor": out.get("executor"),
                                    "stage": out.get("stage"), "ok": bool(out.get("ok")),
                                    "reason": out.get("reason")})
            if asyncio.iscoroutine(r):
                try:
                    asyncio.get_running_loop().create_task(r)
                except RuntimeError:
                    asyncio.run(r)
    except Exception as e:
        _swallow(__file__, e)


def _notify(director, out) -> None:
    """有语音导演就播报；没有就静默（不假装播过）。"""
    if director is None:
        return
    try:
        import asyncio
        say = out.get("say") or ""
        kind = "task_done" if out.get("ok") else "task_failed"
        sev = "info" if out.get("ok") else "warning"
        r = director.notify(kind, sev, say=say, purpose="report")
        if asyncio.iscoroutine(r):
            try:
                asyncio.get_running_loop().create_task(r)
            except RuntimeError:
                asyncio.run(r)
    except Exception as e:
        _swallow(__file__, e)


def handle(text: str = "", *, target: str | None = None, steps=None, mode: str = "cow",
           ledger=None, director=None, accept=None, grant=None, **kw) -> dict:
    """唯一动手入口：一句话或一份计划进来，闭环结果出去。"""
    order = resolve_order(text, target=target, steps=steps, mode=mode)
    return run_order(order, ledger=ledger, director=director, accept=accept,
                     grant=grant, **kw)


__all__ = ["resolve_order", "run_order", "handle", "SANDBOX_INTENTS", "GUI_INTENTS",
           "READ_INTENTS"]
