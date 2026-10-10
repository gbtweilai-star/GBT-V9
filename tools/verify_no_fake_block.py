# tools/verify_no_fake_block.py —— 「拆中间商 / 修假卡点」验收（行为级，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据（对应主人 2026-10-08：「手都在，却总要停下来报卡点」）：
#   ① 桌面操控不再写死 dry_run —— **有授权就真动手，没授权仍只规划**（闸门一点没松）；
#   ② 预检失败不再被永久记住 —— 过期重探，通道恢复后能自己走出来（不是假卡点）。
# 用法：python tools/verify_no_fake_block.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAILS: list = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("== 拆中间商 / 修假卡点 验收 @", ROOT, "==")
    from core import action_loop as AL
    from core import gui_agent as GA
    from core.gui_grant import Grant

    seen: dict = {}

    class FakeAgent:
        def __init__(self, **kw):
            seen.clear()
            seen.update(kw)
        def run(self, task, **kw):
            return {"ok": True, "dry_run": not seen.get("allow_actions", False),
                    "steps": [], "task_id": "verify"}

    GA.GuiAgent = FakeAgent                     # 只换构造器，链路其余部分仍是真代码
    order = {"ok": True, "executor": "exec.gui_agent", "intent": "click_element",
             "translation": {"raw": "点一下那个按钮"}}

    r_plan = AL.run_order(order, grant=None)
    check("没授权：仍然只规划（闸门没松）",
          seen.get("allow_actions") is False and r_plan.get("dry_run") is True
          and str(r_plan.get("mode", "")).startswith("plan"),
          "allow_actions=%s dry_run=%s mode=%s" % (seen.get("allow_actions"),
                                                    r_plan.get("dry_run"), r_plan.get("mode")))

    r_real = AL.run_order(order, grant=Grant(primitives=["gui"], note="验收"))
    check("有授权：真动手（不再被写死 dry_run 吞掉）",
          seen.get("allow_actions") is True and r_real.get("dry_run") is False
          and str(r_real.get("mode", "")).startswith("real"),
          "allow_actions=%s dry_run=%s mode=%s" % (seen.get("allow_actions"),
                                                    r_real.get("dry_run"), r_real.get("mode")))

    # ── ② 假卡点永久化 ──
    from core.tentacle_fleet import TentacleFleet, UnifiedKey
    f = TentacleFleet(ledger=None, n=2, model="m", rpm=5,
                      key=UnifiedKey(key="unit-key", base_url="http://unit.invalid/v1"),
                      client_factory=lambda t: object())      # 快速装配，避免真探通道
    f.client_factory = None                                   # 打开预检这条路
    calls = {"n": 0}

    def pf_bad():
        calls["n"] += 1
        return {"可驱动": False, "主通道原因": "unit_no_key", "通道说明": ""}

    f.preflight = pf_bad
    r1 = f.drive("t001", "任务", auto_order=False)
    check("真不可用时如实拦（并给出重探秒数）",
          str(r1.get("reason", "")).startswith("preflight_blocked") and "重探秒数" in r1,
          "%s · 重探=%ss" % (r1.get("reason"), r1.get("重探秒数")))

    def pf_ok():
        calls["n"] += 1
        return {"可驱动": True}

    f.preflight = pf_ok
    r2 = f.drive("t001", "任务", auto_order=False)
    check("TTL 内不重探（省事、也不抖动）",
          str(r2.get("reason", "")).startswith("preflight_blocked") and calls["n"] == 1,
          "探测次数=%d" % calls["n"])
    f._pf_at = time.time() - 9999                             # 让 TTL 过期
    r3 = f.drive("t001", "任务", auto_order=False)
    check("TTL 过期后重探并走出来（不再是假卡点）",
          not str(r3.get("reason", "")).startswith("preflight_blocked")
          and calls["n"] == 2,
          "reason=%s · 探测次数=%d" % (r3.get("reason"), calls["n"]))

    print("\n结论：" + ("✅ 中间商已拆、假卡点已修" if not FAILS
                     else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
