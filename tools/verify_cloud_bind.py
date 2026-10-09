# tools/verify_cloud_bind.py —— 触手 ↔ 云插件「一人一个」排布验收
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据（主人 2026-10-09：按云插件排布，重启后各自一人一个）：
#   ① 设计上独享数 = min(触手, 真插件)，且独享插件**互不重复**；
#   ② 拿不到独立槽的如实进"只能共享"（**不冒充独享**）；
#   ③ 执行后真写进仓里的 cloud_binding 表（用 /api 或 astate 复核）；
#   ④ 启动自举报告里含这一步（重启即自动配）。
# 用法：python tools/verify_cloud_bind.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations

import asyncio
import sys
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
    print("== 云插件「一人一个」排布验收 @", ROOT, "==")
    from core import cloud_bind as CB
    from core.cloud_plugins import PLUGIN_IDS

    p = CB.plan(100)
    want = min(p["触手"], p["真插件"])
    check("设计：独享数 = min(触手, 真插件)", len(p["独享"]) == want,
          "触手 %d · 真插件 %d · 独享 %d · 共享 %d" % (p["触手"], p["真插件"],
                                                     len(p["独享"]), len(p["只能共享"])))
    uniq = {x["plugin"] for x in p["独享"]}
    check("设计：独享插件互不重复（真·一人一格）", len(uniq) == len(p["独享"]),
          "独享 %d 个 · 去重后 %d 个" % (len(p["独享"]), len(uniq)))
    check("设计：拿不到独立槽的如实进「只能共享」且写明原因",
          all("为什么" in x for x in p["只能共享"]),
          (p["只能共享"][0]["为什么"] if p["只能共享"] else "（全都能独享）"))
    check("设计：预留槽没被硬塞给触手（不编造 id）",
          all(x["plugin"] in set(PLUGIN_IDS) for x in p["独享"]),
          "独享插件全部来自注册表")

    r = CB.apply(100)
    check("执行：所有触手都绑上了（独享 + 共享）",
          r["绑成功"] == p["触手"], "绑成功 %d / %d" % (r["绑成功"], p["触手"]))
    check("执行：独享数 = 真插件数，且无重复占用",
          r["独享绑成功"] == p["真插件"] and r["用到的插件数（去重）"] == p["真插件"],
          "独享 %d · 去重插件 %d" % (r["独享绑成功"], r["用到的插件数（去重）"]))
    check("执行：共享的如实计数（不混进独享）",
          r["共享绑成功"] == p["触手"] - p["真插件"],
          "共享 %d" % r["共享绑成功"])
    check("报告落盘、可查、口径写明为什么不是 100 独享",
          CB.status().get("跑了没") and "为什么不是 100 独享" in CB.status(),
          CB.status().get("为什么不是 100 独享"))

    try:
        from panel.deps import db as _db
        from core.cloud_plugins import CloudHub
        st = asyncio.run(CloudHub(_db).astate())
        check("真写进仓里的 cloud_binding 表（复核读数）",
              st["tentacles_bound"] >= 100, "触手已绑 %d · 插件已绑 %d · 对称 %s" % (
                  st["tentacles_bound"], st["plugins_bound"], st.get("symmetric")))
    except Exception as exc:                                 # noqa: BLE001
        check("真写进仓里的 cloud_binding 表（复核读数）", False, f"读不到：{type(exc).__name__}")

    from core import tentacle_bootstrap as TB
    rep = TB.status()
    check("启动自举报告里含这一步（重启即自动配）",
          "云插件一人一个" in rep, str(rep.get("云插件一人一个"))[:80] or "（还没跑过自举）")

    print("\n口径：独享 = 真 @cf/ id 且只此一根触手；共享的**如实标注**，绝不冒充独立通道。")
    print("结论：" + ("✅ 一人一个排布已通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
