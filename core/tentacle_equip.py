# core/tentacle_equip.py —— 触手自主配置自己的装备（工具）与账号
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：「给触手设立自己的专业，**然后**再让触手自己配置自己的工具和账号」。
# 所以本模块**不抢专业的活**：专业由 core/tentacle_profession.py 立；这里只做第二步 ——
#   按专业算出「这根触手需要什么服务位 + 什么工具」→ 走**仓里已有的两道门**把它配上：
#     · 账号 → core/tentacle_identity.py（独立金库 data/tentacle_identity.sqlite3）
#     · 装备 → core/tool_bay.py（白名单目录 + 必须带 core/gui_grant 的授权令牌 + 落审计）
# 纪律（照两个门自己的口径，不新造旁路）：
#   · 不发明第三个账号库、不往 db_fleet/cloud_plugins 里塞凭据字段（它们的 schema 没有，recon 已确认）；
#   · 凭据只从环境变量读；读不到就**本地自签**（金库 attach 的本意），并如实标「本地自签」；
#   · 装工具必须有令牌；没令牌就如实报 needs_grant —— **绝不假装装上了**；
#   · 不向任何第三方平台批量开户（那既违条款也不是能力，金库头注已写死这条边界）。
from __future__ import annotations

from dataclasses import dataclass, field

# ── 专业分域 → 需要的服务位与工具 ──
# ⚠ 本表是**本仓自定口径**（不是 agency-agents 原文给的）：上游职业定义里没有「需要哪个账号」这一栏。
#   依据 = 分域 + 职业名/职责里的关键词；改之前先看 core/tentacle_profession.py 的 catalog 真读数。
DIVISION_NEEDS: dict = {
    "engineering": {"services": ("开源仓库", "数据库"), "tools": ("git", "sqlite", "json_tool")},
    "testing": {"services": ("开源仓库",), "tools": ("screen", "http", "json_tool")},
    "security": {"services": ("开源仓库", "邮箱"), "tools": ("http", "json_tool")},
    "gis": {"services": ("数据库", "云插件"), "tools": ("image_ops", "http")},
    "design": {"services": ("云插件", "邮箱"), "tools": ("image_ops", "http")},
    "marketing": {"services": ("邮箱", "云插件"), "tools": ("http", "json_tool")},
    "paid-media": {"services": ("邮箱", "云插件"), "tools": ("http", "json_tool")},
    "sales": {"services": ("邮箱",), "tools": ("json_tool",)},
    "support": {"services": ("邮箱",), "tools": ("json_tool",)},
    "product": {"services": ("数据库",), "tools": ("sqlite", "json_tool")},
    "project-management": {"services": ("邮箱", "数据库"), "tools": ("sqlite", "json_tool")},
    "finance": {"services": ("数据库",), "tools": ("sqlite", "json_tool")},
    "game-development": {"services": ("开源仓库", "云插件"), "tools": ("git", "image_ops")},
    "spatial-computing": {"services": ("云插件",), "tools": ("image_ops", "http")},
    "academic": {"services": ("数据库",), "tools": ("sqlite", "http")},
    "specialized": {"services": ("数据库",), "tools": ("json_tool",)},
    "supply-chain": {"services": ("数据库",), "tools": ("sqlite",)},
    "hr": {"services": ("邮箱",), "tools": ("json_tool",)},
    "legal": {"services": ("邮箱", "数据库"), "tools": ("sqlite", "json_tool")},
}

# 关键词 → 额外工具（从职业名/职责里现算，不写死到某个角色）
KEYWORD_TOOLS = (("ocr", "ocr"), ("识别", "ocr"), ("截图", "screen"), ("视觉", "screen"),
                 ("视频", "ffmpeg"), ("影像", "ffmpeg"), ("剪辑", "ffmpeg"),
                 ("爬", "http"), ("抓取", "http"), ("网络", "http"), ("检索", "http"),
                 ("压缩", "sevenzip"), ("打包", "sevenzip"),
                 ("无障碍", "ui_tree"), ("界面", "ui_tree"), ("GUI", "ui_tree"),
                 ("图", "image_ops"))


@dataclass
class Need:
    tentacle: str
    profession: str
    division: str
    services: tuple = ()
    tools: tuple = ()
    reason: dict = field(default_factory=dict)


def grant_token(grant):
    """把授权归一成**门认的形状**：core/gui_grant.Grant.verify 只认 dict 或 token 串，
    直接传 Grant 对象会被判 grant_missing_signature（真踩过）。这里顺手归一，不改门的口径。"""
    if grant is None:
        return None
    if hasattr(grant, "token") and callable(getattr(grant, "token")):
        try:
            return grant.token()
        except Exception:                                      # noqa: BLE001
            return getattr(grant, "data", None)
    return grant


def needs_for(profession_row: dict) -> Need:
    """按专业行（金库读出的那条）算出装备与账号需求。没立专业的触手 → 空需求（不瞎配）。"""
    t = str(profession_row.get("tentacle") or "")
    name = str(profession_row.get("name") or "")
    div = str(profession_row.get("division") or "")
    if not name:
        return Need(t, "", "", (), (), {"说明": "未立专业：先 assign 再接装备"})
    base = DIVISION_NEEDS.get(div, {"services": ("数据库",), "tools": ("json_tool",)})
    services = list(base["services"])
    tools = list(base["tools"])
    text = f"{name} {profession_row.get('description') or ''}"
    kw_hit = {}
    for kw, tool in KEYWORD_TOOLS:
        if kw in text and tool not in tools:
            tools.append(tool)
            kw_hit[tool] = kw
    return Need(t, name, div, tuple(services), tuple(tools),
                {"分域口径": div, "关键词补齐": kw_hit,
                 "口径来源": "本仓自定（上游职业定义无账号栏）"})


def _prof(tentacle: str) -> dict:
    from core import tentacle_profession as TP
    TP.ensure()
    with TP._conn() as c:
        r = c.execute("SELECT * FROM tentacle_profession WHERE tentacle=?",
                      (tentacle,)).fetchone()
    return dict(r) if r else {}


def _account_state(tentacle: str) -> dict:
    from core import tentacle_identity as TI
    return {r["service"]: r for r in TI.rows(tentacle=tentacle)}


def _tool_state() -> dict:
    """工具坞已装清单（读 tool_bay 表；读不到给空，不编）。"""
    try:
        from core.tool_bay import CATALOG, ToolBay
        from audit.ledger_factory import make_ledger
        tb = ToolBay(make_ledger(), grant_required=False)
        got = {}
        if tb.led is not None:
            from senses.sqldialect import txn
            with txn(tb.led) as cur:
                cur.execute("SELECT name, state FROM tool_bay")
                got = {r[0]: r[1] for r in cur.fetchall()}
        avail = {}
        for name, spec in CATALOG.items():
            if spec.kind == "builtin":
                avail[name] = "自带"                      # stdlib：真自带
            elif spec.kind == "cmd":
                # 原来一律写"随系统"= 谎报（没装也说有）；改成真检测
                avail[name] = "随系统" if tb._exists(spec.entry) else "未装"
            avail.setdefault(name, got.get(name, "未装"))
        return avail
    except Exception:                                         # noqa: BLE001
        return {}


def plan(tentacle: str) -> dict:
    """这根触手该配什么、现在缺什么（只读，不动手）。"""
    p = _prof(tentacle)
    need = needs_for(p)
    acc = _account_state(tentacle)
    tools = _tool_state()
    账号缺口 = [s for s in need.services
                if (acc.get(s, {}).get("state") not in ("已配", "已验证", "已绑"))]
    装备缺口 = [t for t in need.tools if tools.get(t) in ("未装", None)]
    return {"tentacle": tentacle, "专业": p.get("name") or "未立",
            "分域": p.get("division") or "",
            "需要服务位": list(need.services), "需要工具": list(need.tools),
            "账号现状": {s: (acc.get(s, {}) or {}).get("state", "未占位") for s in need.services},
            "装备现状": {t: tools.get(t, "未知") for t in need.tools},
            "账号缺口": 账号缺口, "装备缺口": 装备缺口,
            "口径": need.reason,
            "账号库": "data/tentacle_identity.sqlite3（独立库，与账本库分开）",
            "装备库": "tools/_bay（编队自留地，整目录删掉即回滚）"}


def equip(tentacle: str, *, accounts: bool = True, tools: bool = True,
          grant=None, dry_run: bool = False, tool_bay=None) -> dict:
    """触手给自己配装备与账号。

    🔴 2026-10-09 修：默认原为 **dry_run=True** ⇒ 全编队"永远干跑"、装备卡在 51/100，
       主人问「为什么触手你没让她自己配置好装备？」——答案就是这个默认值。
    现改为**默认真动手**：每一步仍走装备坞的门与审计（白名单内 safe 工具免主人令牌、
    risk=write 与系统级仍拦），要只看不动请显式传 dry_run=True。
    """
    from core import tentacle_identity as TI
    pl = plan(tentacle)
    if pl["专业"] == "未立":
        return {"ok": False, "reason": "这根触手还没立专业：先 core.tentacle_profession.assign",
                "plan": pl}
    acts = []
    if accounts:
        for s in pl["账号缺口"]:
            if dry_run:
                acts.append({"部件": "账号", "服务": s, "动作": "claim+provision（干跑）",
                             "凭据": TI.env_key(tentacle, s)})
                continue
            c1 = TI.claim(tentacle, s)
            step = {"部件": "账号", "服务": s, "claim": c1}
            # 已经有身份位（本仓历史上 100×4 已占满）→ 不算失败，继续把它配上
            occupied = bool(c1.get("ok")) or "已有身份位" in str(c1.get("reason", ""))
            if occupied:
                pr = TI.provision(tentacle, s)                 # 有环境变量才算真配
                if pr.get("ok"):
                    step["结果"] = "已配（凭据来自环境变量）"
                else:
                    # 本地自签：金库 attach 的本意（不需要外部凭据的身份），如实标出来
                    at = TI.attach(tentacle, s, handle=f"{tentacle}.{s}@local",
                                   note="本地自签（非第三方开户）")
                    step["结果"] = "本地自签" if at.get("ok") else "未配"
                    step["attach"] = at
                    step["为什么没真凭据"] = pr.get("reason", "")[:80]
            else:
                step["结果"] = "占位失败"
            acts.append(step)
    if tools:
        tb = tool_bay
        if tb is None and not dry_run:
            try:
                from audit.ledger_factory import make_ledger
                from core.tool_bay import ToolBay
                tb = ToolBay(make_ledger())
            except Exception:                                  # noqa: BLE001
                tb = None
        for t in pl["装备缺口"]:
            if dry_run or tb is None:
                acts.append({"部件": "装备", "工具": t, "动作": "install（干跑）",
                             "门": "需 core/gui_grant 的 tools 授权令牌"})
                continue
            r = tb.install(tentacle, t, grant=grant_token(grant))
            acts.append({"部件": "装备", "工具": t, "结果": r})
    ok_acc = all(a.get("结果") in ("已配（凭据来自环境变量）", "本地自签", "claim+provision（干跑）")
                 for a in acts if a["部件"] == "账号")
    tools_ok = all((a.get("结果", {}) or {}).get("ok", True) if isinstance(a.get("结果"), dict)
                   else True for a in acts if a["部件"] == "装备")
    needs_grant = [a["工具"] for a in acts if a["部件"] == "装备"
                   and isinstance(a.get("结果"), dict) and not a["结果"].get("ok")
                   and "授权" in str(a["结果"].get("reason", ""))]
    return {"ok": bool(ok_acc and tools_ok), "tentacle": tentacle, "干跑": dry_run,
            "专业": pl["专业"], "动作": acts,
            "已齐": not acts,
            "说明": ("所需账号与装备都已就位，本次无需动作" if not acts
                     else f"本次处理 {len(acts)} 项"),
            "需要授权的装备": needs_grant,
            "口径": "账号走独立金库（凭据只从环境变量读，读不到就本地自签并如实标注）；"
                    "装备走工具坞白名单 + 授权令牌，没令牌报 needs_grant，绝不假装装上"}


def report(*, n: int = 100) -> dict:
    """全编队一屏：专业立了多少、装备账号各到位多少、缺什么（主脑视图）。"""
    rows = []
    立 = 0
    账号齐 = 装备齐 = 0
    for i in range(1, max(1, int(n)) + 1):
        tid = "t%03d" % i
        pl = plan(tid)
        if pl["专业"] != "未立":
            立 += 1
            if not pl["账号缺口"]:
                账号齐 += 1
            if not pl["装备缺口"]:
                装备齐 += 1
        rows.append({"tentacle": tid, "专业": pl["专业"],
                     "账号缺口": len(pl["账号缺口"]), "装备缺口": len(pl["装备缺口"])})
    return {"n": int(n), "已立专业": 立, "账号已齐": 账号齐, "装备已齐": 装备齐,
            "未立专业": int(n) - 立, "行": rows,
            "口径": "账号齐=所需服务位都在已配/已验证/已绑；装备齐=所需工具都已就位"}


def run(op: str, **kw) -> dict:
    ops = {"plan": plan, "equip": equip, "report": report}
    fn = ops.get(str(op or "").strip())
    if not fn:
        from core import tentacle_profession as _TP
        return {"ok": False, "error": f"unknown operation: {op}",
                "known": sorted(ops) + ["profession(见 core.tentacle_profession.status)"]}
    return fn(**kw) if kw else {"ok": False, "error": "缺参数"}


__all__ = ["needs_for", "plan", "equip", "report", "run", "Need", "DIVISION_NEEDS"]
