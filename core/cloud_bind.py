# core/cloud_bind.py —— 触手 ↔ 云插件「一人一个」排布（按云插件部署排布）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-09）：「按照云插件排布以及里面部署的东西，重启一次触手自己自动按照
#   各自一人一个来配置，看看她能否实现。」
#
# 与仓里既有设计的关系（必须分清，别混）：
#   · `CloudHub.abind_all()` = **全互通网格**：每根触手连全部 100 个插件（1 万对）——那是"任意可达"；
#   · 本模块 = **一人一个**：触手 i 独占插件 i ——那是"各自有自己那一格"。
#     主人要的是后者（独立通道），两者不冲突：网格管可达性，这里管归属。
#
# 🔴 硬约束（不许糊）：云插件 100 槽里**只有 48 个有真 @cf/ id**，另外 52 个是预留位（没有模型）。
#   触手有 100 根 ⇒ **纯一对一最多覆盖 48 根**。剩下的 52 根**必须如实标"无独立槽"**，
#   可选：共享某个真插件（并标注"共享"）或等官方补齐。**绝不把共享说成独立。**
#
# 出口位也 1:1：仓里 EGRESS_POOL 有 100 个出口位（eg-001..eg-100），与插件 1:1。
from __future__ import annotations
from core.swallow import swallow as _swallow

import asyncio
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "state" / "cloud_bind_report.json"


def plan(n: int = 100) -> dict:
    """**只设计不写盘**：谁拿哪一格、谁只能共享（可先看再执行）。"""
    from core.cloud_plugins import PLUGIN_IDS, registry
    reg = registry()
    real = [p["key"] for p in reg["plugins"] if p["cf_id"]]
    tentacles = ["t%03d" % i for i in range(1, int(n) + 1)]
    got, shared, none = [], [], []
    for i, t in enumerate(tentacles):
        if i < len(real):
            got.append({"tentacle": t, "plugin": real[i], "独享": True})
        else:
            shared.append({"tentacle": t, "共享": real[i % len(real)],
                           "为什么": "真插件只有 %d 个 < 触手 %d 根 ⇒ 拿不到独立槽" % (len(real), len(tentacles))})
    return {"ok": True, "触手": len(tentacles), "真插件": len(real),
            "独享": got, "只能共享": shared, "预留未用": len(PLUGIN_IDS) - len(real),
            "口径": "独享 = 真 @cf/ id 且只此一根触手；共享的**如实标注**，不冒充独享",
            "出口位": "EGRESS_POOL 100 个与插件 1:1（仓里既有）"}


def apply(n: int = 100, *, share: bool = True, led=None) -> dict:
    """执行排布：真插件一人一个（abind），拿不到的如实标共享/无独立槽，并落报告。"""
    p = plan(n)
    rows, failed = [], []
    try:
        from panel.deps import db as _db
        from core.cloud_plugins import CloudHub

        async def _go():
            hub = CloudHub(_db)
            await hub.ainit()
            out = []
            for item in p["独享"]:
                r = await hub.abind(item["tentacle"], item["plugin"])
                out.append({"tentacle": item["tentacle"], "plugin": item["plugin"],
                            "ok": bool(r.get("ok")), "why": r.get("reason")})
            if share:
                for item in p["只能共享"]:
                    r = await hub.abind(item["tentacle"], item["共享"], resource="shared")
                    out.append({"tentacle": item["tentacle"], "plugin": item["共享"],
                                "ok": bool(r.get("ok")), "共享": True, "why": r.get("reason")})
            return out

        rows = asyncio.run(_go())
    except Exception as exc:                                 # noqa: BLE001
        failed.append({"为什么": f"{type(exc).__name__}: {exc}"[:120]})
    ok_rows = [r for r in rows if r.get("ok")]
    uniq = {r["plugin"] for r in ok_rows if not r.get("共享")}
    rec = {"ok": bool(ok_rows) and not failed, "时间": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "触手": p["触手"], "真插件": p["真插件"],
           "绑成功": len(ok_rows), "独享绑成功": len([r for r in ok_rows if not r.get("共享")]),
           "共享绑成功": len([r for r in ok_rows if r.get("共享")]),
           "用到的插件数（去重）": len(uniq), "超时/T报错": failed,
           "没独立槽的触手": [i["tentacle"] for i in p["只能共享"]],
           "明细": rows[:120],
           "口径": "独享与共享分列；共享的**不冒充**独立通道",
           "为什么不是 100 独享": f"真插件 {p['真插件']} < 触手 {p['触手']}（其余是预留槽，没有模型 id）"}
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as e:
        _swallow(__file__, e)
    return rec


def status() -> dict:
    if not REPORT.is_file():
        return {"ok": False, "跑了没": False, "reason": "还没排布过"}
    try:
        d = json.loads(REPORT.read_text(encoding="utf-8"))
        d["跑了没"] = True
        return d
    except (OSError, ValueError) as exc:
        return {"ok": False, "跑了没": True, "reason": type(exc).__name__}



# ── 插件 ↔ 插件 双向绑定（主人 2026-10-09："插件之间必须要双向绑定，触手拥有数据传输的能力，
#    这样就可以把 100 个插件连接串联共享资源"）──────────────────────────────────────────
# 仓里既有的口径（core/cloud_plugins.py）：
#   · `mesh_view()`：100 插件全互通 = 4950 条无向边（采样画图，数字精确）；
#   · `CloudHub.ashare_bond(a,b)` / `ashare_all()` / `ashare_state()`：**双向**落 `cloud_share` 表；
#   · 方向对称性由 `ashare_state()` 复核（asymmetric 空 = 真双向）。
# 本模块只做"把它跑起来 + 复核 + 给触手做搬运"这层，不另造一套绑定表。


def share_all(*, n: int = 100) -> dict:
    """把 100 个插件两两**双向**绑起来（走仓里的 ashare_all），并复核对称性。"""
    import asyncio as _aio
    from panel.deps import db as _db
    from core.cloud_plugins import CloudHub

    async def _go():
        hub = CloudHub(_db)
        await hub.ainit()
        r = await hub.ashare_all()
        st = await hub.ashare_state()
        return r, st

    try:
        r, st = _aio.run(_go())
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:160]}
    pairs = st.get("pairs") or st.get("bonded") or st.get("edges") or 0
    sym = st.get("symmetric")
    return {"ok": bool(r) and sym is not False, "绑定结果": r, "复核": st,
            "双向对数": pairs, "对称": sym,
            "期望": "100 插件全互通 = 4950 对无向（9900 行有向）",
            "口径": "双向绑定落在 cloud_share；对称=真双向，asymmetric 非空就是没绑全"}


def share_state() -> dict:
    """只读复核：现在到底绑成什么样（不写）。"""
    import asyncio as _aio
    from panel.deps import db as _db
    from core.cloud_plugins import CloudHub
    try:
        st = _aio.run(CloudHub(_db).ashare_state())
        return {"ok": True, **st}
    except Exception as exc:                                # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def chain(plugins: list, prompt: str, *, tentacle: str = "t001", timeout: float = 60.0) -> dict:
    """**触手搬运的串联管线**：上一站的输出当下一站的输入，跑完整条链。

    这就是主人说的"把 100 个插件连接串联共享资源"的可执行形态：
    触手本身有数据传输能力（它的记忆/会话就是搬运通道），于是 A 的出产 → B 的原料。
    """
    from core import cloud_runner as CR
    steps, carry, ok = [], prompt, True
    for i, p in enumerate(plugins, 1):
        r = CR.run_plugin(str(p), carry, tentacle=tentacle, timeout=timeout)
        steps.append({"第几站": i, "插件": str(p), "ok": bool(r.get("ok")),
                      "http": r.get("http"), "ms": r.get("ms"),
                      "进料": carry[:120], "出产": (r.get("出字") or "")[:200],
                      "为什么": r.get("reason")})
        if not r.get("ok"):
            ok = False
            break
        carry = r.get("出字") or carry          # 触手把这一站的产出搬到下一站
    return {"ok": ok, "搬运者": tentacle, "站数": len(steps),
            "跑通": sum(1 for s in steps if s["ok"]), "步骤": steps,
            "终产出": carry[:300],
            "口径": "触手作搬运：上站出产即下站进料；中途失败就停在那站并如实报"}


__all__ = ["plan", "apply", "status", "REPORT", "share_all", "share_state", "chain"]
