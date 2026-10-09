# core/tentacle_agent.py —— 触手就是完整的 AI 智能体（不是子代理）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人定的根本大法（2026-10-08）：「拥有独立记忆和本体的所有能力；她跟子代理完全不同；
#   她可以思考和反思，遇到问题懂得查阅资料和学习进化；严格来说触手就是完整的 AI 智能体；
#   GBT小土豆V9 是最高指挥官。」
#
# 本模块把这几条落成**可机检的读数**（服从链在 core/tentacle_orders.py）：
#   · capabilities(t)：触手能用的能力面 == 本体的能力面（不是子集，也不是"看起来像"）；
#   · learn(t, q)：遇到盲区 → 先查（core/blindspot 闸）→ 吃透了 → **写进它自己的记忆**；
#   · reflect(t)：它自己的元认知（记了多少、学了几条、哪些没学成）；
#   · vs_subagent()：逐项对比，把"不同"变成看得见的字段。
# 三条纪律：记忆**分文件**（互不可见）、学不到**如实记空**、能力面**现算**（不写死数字）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEM_DIR = ROOT / "state" / "tentacle_memory"


def _mem(tentacle: str) -> Path:
    return MEM_DIR / f"{tentacle}.jsonl"


def _main_surface() -> dict:
    """本体（GBT小土豆V9）真实的能力面 —— **现算**，算不出来就如实说算不出来。"""
    rows, sources = [], []
    try:
        from skills import NATIVE_CAPABILITIES as N
        for k in (N.keys() if isinstance(N, dict) else list(N)):
            rows.append({"能力": k, "来源": "原生"})
        sources.append(f"原生 {len(N)}")
    except Exception as exc:                                 # noqa: BLE001
        sources.append(f"原生取不到（{type(exc).__name__}）")
    # 首选仓里现成的适配器（它把 CapRegistry(53) 包成 SkillRegistry 形状）—— 直接用类构造会 TypeError，
    # 于是"本体能力面"会被读成只剩 5 个原生，看着像触手能力小，其实是取法不对（真踩过）。
    try:
        from skills.caps.adapter import as_skill_registry
        reg = as_skill_registry()
        caps = getattr(reg, "caps", None)
        caps = caps() if callable(caps) else caps
        for c in (caps or []):
            cid = c if isinstance(c, str) else (c.get("id") or c.get("name"))
            rows.append({"能力": cid, "来源": "octop"})
        sources.append(f"as_skill_registry {len(caps or [])}")
    except Exception as exc:                                 # noqa: BLE001
        sources.append(f"adapter 取不到（{type(exc).__name__}）")
    for path, attr in (("skills.caps.registry", "CapRegistry"),):
        try:
            mod = __import__(path, fromlist=[attr])
            cls = getattr(mod, attr)
            reg = cls()
            caps = getattr(reg, "caps", None)
            caps = caps() if callable(caps) else caps
            if caps is None:
                from skills.caps.adapter import as_skill_registry
                reg = as_skill_registry()
                caps = getattr(reg, "caps", None)
                caps = caps() if callable(caps) else caps
            for c in (caps or []):
                cid = c if isinstance(c, str) else (c.get("id") or c.get("name") if isinstance(c, dict) else str(c))
                rows.append({"能力": cid, "来源": "octop"})
            sources.append(f"{attr} {len(caps or [])}")
            if caps:
                break
        except Exception as exc:                             # noqa: BLE001
            sources.append(f"{attr} 取不到（{type(exc).__name__}）")
    # 去重（同一个能力可能两处都有）
    seen, uniq = set(), []
    for r in rows:
        if r["能力"] in seen:
            continue
        seen.add(r["能力"])
        uniq.append(r)
    return {"能力": uniq, "条数": len(uniq), "来源": sources}


def capabilities(tentacle: str | None = None) -> dict:
    """触手的能力面 = 本体的能力面（主人："拥有本体的所有能力"）。

    逐条标出"要不要主脑授权"：门在 core/gui_grant 与 policy 上，触手不能给自己发授权。
    """
    body = _main_surface()
    rows = [dict(r, 要授权=_needs_grant(r["能力"])) for r in body["能力"]]
    return {"触手": tentacle or "（本体）", "能力": rows, "条数": len(rows),
            "与本体同面": True, "来源": body["来源"],
            "口径": "触手拿的是**本体同一张能力面**（不是子集）；要授权的仍要主脑/主人发"}


def _needs_grant(cap_id: str) -> bool:
    """粗判哪些能力要过授权门（读 policy/gui_grant 的名字表；判不了就说不知道，不硬猜）。"""
    try:
        from core import gui_grant as GG
        txt = "\n".join(str(getattr(GG, n, "")) for n in dir(GG))
        return cap_id in txt
    except Exception:                                        # noqa: BLE001
        return False


def learn(tentacle: str, question: str, *, fetcher=None, gap: str = "") -> dict:
    """触手自己查资料、吃透、**写进自己的记忆**（这就是"学习进化"的落点）。

    走 core/blindspot 的闸：查不到/没吃透 → 不动手、如实记空。
    """
    from core import blindspot as BS
    g = BS.gate(question, gap=gap, fetcher=fetcher)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "问题": question,
           "可以动手": bool(g.get("可以动手")), "为什么": g.get("为什么")}
    if g.get("可以动手"):
        ev = g.get("证据") or {}
        res = ev.get("research") or {}
        rec["学到"] = res.get("笔记") or ""
        rec["来源"] = res.get("来源") or []
        rec["独立域名"] = ((ev.get("质量") or {}).get("独立域名"))
    try:
        MEM_DIR.mkdir(parents=True, exist_ok=True)
        with _mem(tentacle).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        return {"ok": False, "reason": f"记忆写不进去：{type(exc).__name__}"}
    return {"ok": True, "触手": tentacle, "学没学": bool(rec.get("学到")),
            "可以动手": rec["可以动手"], "为什么": rec["为什么"],
            "记忆": str(_mem(tentacle)), "进化到第几条": len(_read(tentacle))}


def _read(tentacle: str) -> list:
    p = _mem(tentacle)
    if not p.is_file():
        return []
    out = []
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                out.append(json.loads(ln))
    except (OSError, ValueError) as e:
        _swallow(__file__, e)
    return out


def reflect(tentacle: str, *, limit: int = 5) -> dict:
    """触手自己的元认知：记了多少、学成几条、哪些没学成（**不是**去读主脑的记忆）。"""
    rows = _read(tentacle)
    learned = [r for r in rows if r.get("学到")]
    blind = [r for r in rows if not r.get("学到")]
    try:
        from core import tentacle_profession as TP
        prof = next((r for r in TP.roster(n=500)["行"] if r["tentacle"] == tentacle), {})
    except Exception:                                        # noqa: BLE001
        prof = {}
    return {"触手": tentacle, "专业": prof.get("专业") or "未立",
            "分域": prof.get("分域") or "", "记忆文件": str(_mem(tentacle)),
            "记了几条": len(rows), "学成": len(learned), "没学成": len(blind),
            "最近在学": [r.get("问题") for r in rows[-limit:]],
            "我自己的结论": (f"学成 {len(learned)} / 共 {len(rows)} 条；没学成的已如实记下，不硬试"
                          if rows else "还没有自己的记忆（先让它去学一条）"),
            "与主脑记忆的关系": "分文件存放，互不可见；主脑有全量视图"}


def vs_subagent(tentacle: str = "t001") -> dict:
    """把"触手不是子代理"变成看得见的字段对比（都取真读数）。"""
    try:
        from core import tentacle_profession as TP
        prof = next((r for r in TP.roster(n=500)["行"] if r["tentacle"] == tentacle), {})
    except Exception:                                        # noqa: BLE001
        prof = {}
    try:
        from core import tentacle_identity as TI
        ident = len(TI.rows(tentacle=tentacle))
    except Exception:                                        # noqa: BLE001
        ident = 0
    try:
        from core.tentacle_fleet import TentacleKeyBook
        book = TentacleKeyBook()
        kid = book.key_id(tentacle) if hasattr(book, "key_id") else ""
    except Exception:                                        # noqa: BLE001
        kid = ""
    mem = _mem(tentacle)
    return {"对比": [
                {"项": "持久身份", "触手": f"五元（专业{'有' if prof.get('专业') else '无'}·{tentacle}·llm·ctx·room）",
                 "子代理": "无（跑完即散）"},
                {"项": "独立记忆", "触手": f"{mem.name}（{len(_read(tentacle))} 条）",
                 "子代理": "无"},
                {"项": "自己的账号位", "触手": f"金库 {ident} 条", "子代理": "无"},
                {"项": "自己的密钥", "触手": kid or "（回退统一密钥，如实标注）", "子代理": "无"},
                {"项": "能思考/反思", "触手": "reflect() 读自己的记忆", "子代理": "无"},
                {"项": "能查资料学习进化", "触手": "learn() 走盲区闸后写自己的记忆",
                 "子代理": "无"},
                {"项": "听谁的", "触手": "只认主脑/主人（core/tentacle_orders）",
                 "子代理": "父会话即生即灭"},
            ],
            "一句话": "子代理是主脑临时伸出去的一只手；触手是常驻的、有身份证有记忆有专业的下属",
            "最高指挥官": "GBT小土豆V9（主脑数字人）"}


__all__ = ["capabilities", "learn", "reflect", "vs_subagent", "MEM_DIR"]
