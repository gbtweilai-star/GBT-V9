# core/expand.py —— 自动扩容：缺什么就补什么，全部走钩子留证（不许跳过、不许空转）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   "把所有的加装插件和连接插件做成自动化扩容，需要那个就触手自动化执行"；
#   "不需要用户安装的时候还去配置一大堆东西"；
#   "全部触手在新用户下载的时候就要执行第一条指令自动化各自配备好自己的东西"。
#
# 做法（**先扫描、再按需、后验证**，每一步都过 core/hooks 的闸门）：
#   ① 扫描：云插件槽 / 库槽 / 身份位 各自缺多少（真读数，不猜）
#   ② 计划：按缺口生成"每根触手该做什么"（一根触手只领一个身份位，不重复领）
#   ③ 执行：dry_run 先给计划；真跑时逐步过钩子，任何一步无产出=空转被拦
#   ④ 验证：跑完复扫，缺口必须真的变小（否则算白干 → 钩子拒收）
#   ⑤ 首启：新用户第一次启动就跑一次（first_boot），不用手工配一大堆
#
# 边界与 identity 一致：**程序不向第三方批量开户**；凭据由环境变量/密钥服务注入，
#   扩容做的是槽位、绑定、校验、审计这些"不用你动手"的部分。
from core import hooks as H
from core import tentacle_identity as TI

DEFAULT_N = 100


def scan(*, n: int = DEFAULT_N) -> dict:
    """扫一遍缺什么（每项都给来源，取不到就说取不到）。"""
    out = {"云插件": None, "库槽": None, "身份位": None, "依据": {}, "缺口": 0}
    try:
        from core import cloud_plugins as CP
        ids = list(getattr(CP, "PLUGIN_IDS", ()) or ())
        out["云插件"] = len(ids)
        out["依据"]["云插件"] = "cloud_plugins.PLUGIN_IDS"
    except Exception as exc:                                   # noqa: BLE001
        out["依据"]["云插件"] = f"读不到：{type(exc).__name__}"
    try:
        from core import db_fleet as DF
        out["库槽"] = len(DF.SLOT_IDS)
        out["依据"]["库槽"] = "db_fleet.SLOT_IDS"
    except Exception as exc:                                   # noqa: BLE001
        out["依据"]["库槽"] = f"读不到：{type(exc).__name__}"
    try:
        s = TI.summary(n=n)
        out["身份位"] = {"就绪": s["就绪"], "总数": s["身份位总数"], "待办": s["待办数"]}
        out["依据"]["身份位"] = "tentacle_identity.summary"
        out["缺口"] = int(s["待办数"])
    except Exception as exc:                                   # noqa: BLE001
        out["依据"]["身份位"] = f"读不到：{type(exc).__name__}"
    if out["云插件"] is not None and out["云插件"] < n:
        out["缺口"] += n - out["云插件"]
    return out


def plan(*, n: int = DEFAULT_N, limit: int = 40) -> dict:
    """按缺口生成计划：每根触手领自己的身份位 + 该连的槽位。不重复领（唯一约束兜底）。"""
    st = TI.summary(n=n)
    # 用**全量待办**排计划：样例只有 8 条，照样例排就永远只配得动几根触手（真踩过）
    待办 = st.get("待办全部") or st["待办样例"]
    steps = []
    for x in 待办[: max(1, int(limit))]:
        t, s = x["触手"], x["服务"]
        if x["状态"] == "未占位":
            steps.append({"触手": t, "服务": s, "动作": "claim",
                          "做什么": f"给 {t} 在「{s}」占一个身份位（一根触手一个）"})
        elif x["状态"] == "待配":
            steps.append({"触手": t, "服务": s, "动作": "provision",
                          "做什么": f"读 {TI.env_key(t, s)} 接上凭据（读不到就如实缺）"})
        else:
            steps.append({"触手": t, "服务": s, "动作": "verify",
                          "做什么": f"校验 {t} 的「{s}」身份"})
    return {"触手数": n, "缺口": st["待办数"], "计划步数": len(steps), "步": steps,
            "口径": "先扫描→按需→后验证；每步过钩子，空转/跳过一律拦"}


def run(*, n: int = DEFAULT_N, dry_run: bool = False, limit: int = 40,  # 主人令：默认真动手（要演练请显式传 True）
        owner: str = "main", allow_hook: bool = True) -> dict:
    """执行扩容。dry_run=True 只给计划；真跑时每步过钩子并复扫验证缺口变小。"""
    before = scan(n=n)
    if dry_run:
        return {"ok": True, "dry_run": True, "扫描": before, "计划": plan(n=n, limit=limit),
                "口径": "dry_run 不落任何改动"}
    g = H.Guard("自动扩容", owner=owner, must_steps=("扫描缺口", "执行计划", "复扫验证"))
    with g.step("扫描缺口", expect="知道缺多少") as s:
        s.evidence(身份位缺口=before["缺口"], fingerprint=H.fingerprint(before["缺口"]))
    done = []
    with g.step("执行计划", expect="按计划补齐（缺口要真的变小）") as s:
        # 走 provision 的适配器链（本地自签保底 → 凭据 → 官方 API → 用户脚本槽）：
        # 只 claim 不配备的话状态还是"待配"，缺口根本不会变小 —— 那就是白干（钩子会拦）。
        from core import provision as P
        for st_ in plan(n=n, limit=limit)["步"]:
            t, svc = st_["触手"], st_["服务"]
            try:
                r = P.provision_one(t, svc, allow_hook=allow_hook)
                done.append({"触手": t, "服务": svc, "适配器": r.get("用哪个适配器"),
                             "ok": bool(r.get("ok")), "说明": r.get("说明") or ""})
            except Exception as exc:                           # noqa: BLE001
                done.append({"触手": t, "服务": svc, "适配器": "—", "ok": False,
                             "说明": f"{type(exc).__name__}"})
        okn = sum(1 for d in done if d["ok"])
        s.evidence(执行=len(done), 成功=okn, fingerprint=H.fingerprint(okn, len(done)))
    after = scan(n=n)
    with g.step("复扫验证", expect="缺口真的变小或已无缺口") as s:
        moved = (before["缺口"] - after["缺口"])
        if before["缺口"] and moved <= 0 and after["缺口"] > 0:
            # 缺口没动 = 白干：这一步的钩子会把它判成"没有产出"
            raise H.HookError(f"扩容后缺口没变（{before['缺口']} → {after['缺口']}）：白干，拒收")
        s.evidence(缺口_前=before["缺口"], 缺口_后=after["缺口"], 减少=moved,
                   fingerprint=H.fingerprint(before["缺口"], after["缺口"]))
    audit = g.finish()
    return {"ok": True, "dry_run": False, "扫描前": before, "扫描后": after,
            "执行明细": done, "钩子": {"通过": audit["通过"], "步数": audit["步数"],
                                       "证据条数": audit["证据条数"]}}


def first_boot(*, n: int = DEFAULT_N, owner: str = "system", limit: int = 40) -> dict:
    """新用户第一次启动跑的那条指令：扫缺口 → 占位（每根触手一次）→ 能接的接上。

    主人要求："全部触手在新用户下载的时候就要执行第一条指令自动化各自配备好自己的东西"。
    这里**只做不需要凭据的部分**（占位/校验/绑定槽位）；需要凭据的如实列成待办，不假装成功。
    幂等：重复调用不会多占（唯一约束 + 已占位即跳过）。
    """
    before = scan(n=n)
    g = H.Guard("首启配备", owner=owner, must_steps=("扫缺口", "逐触手占位", "接已备凭据", "留待办"))
    claimed, provisioned, pending = [], [], []
    with g.step("扫缺口", expect="知道首启要补什么") as s:
        s.evidence(缺口=before["缺口"], fingerprint=H.fingerprint(before["缺口"]))
    with g.step("逐触手占位", expect="每根触手在各服务上各占一个位") as s:
        for t in TI.tentacles(n=n):
            for svc in TI.SERVICES:
                r = TI.claim(t, svc, note="首启自动占位")
                if r.get("ok"):
                    claimed.append(f"{t}/{svc}")
        s.evidence(新占位=len(claimed), fingerprint=H.fingerprint(len(claimed)))
    with g.step("接已备凭据", expect="有环境变量的直接接上") as s:
        import os
        for t in TI.tentacles(n=n):
            for svc in TI.SERVICES:
                if os.environ.get(TI.env_key(t, svc)):
                    r = TI.provision(t, svc)
                    if r.get("ok"):
                        provisioned.append(f"{t}/{svc}")
                else:
                    pending.append({"触手": t, "服务": svc, "缺": TI.env_key(t, svc)})
        s.evidence(已接=len(provisioned), 缺凭据=len(pending),
                   fingerprint=H.fingerprint(len(provisioned), len(pending)))
    with g.step("留待办", expect="缺口列清楚，不许含糊") as s:
        s.evidence(待办数=len(pending), 样例=[x["缺"] for x in pending[:3]] or "无",
                   fingerprint=H.fingerprint(len(pending)))
    audit = g.finish()
    return {"ok": True, "首启": True, "占位": len(claimed), "已接凭据": len(provisioned),
            "缺凭据待办": len(pending), "待办样例": pending[:6],
            "钩子": {"通过": audit["通过"], "步数": audit["步数"], "证据条数": audit["证据条数"]},
            "口径": "首启只做不需要凭据的部分；要凭据的如实列待办（不假装成功）"}


def status(*, n: int = DEFAULT_N) -> dict:
    s = scan(n=n)
    return {"扫描": s, "计划步数": plan(n=n, limit=20)["计划步数"],
            "口径": "自动化扩容只覆盖槽位/绑定/校验/审计；第三方开户仍需人/运维（见 identity 边界）"}


__all__ = ["DEFAULT_N", "scan", "plan", "run", "first_boot", "status"]
