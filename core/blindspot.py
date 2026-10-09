# core/blindspot.py —— 盲区闭环：先判定"我不会" → 查资料 → 吃透了 → 才准动手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：「在遇到知识盲区的时候就要懂得查阅资料以及全网抓取核心资料，
#   给我读懂吃透了再开始动手，别每次都跟个二百五一样的打开网页就搜到的东西她就卡死在那里」。
#
# 现状（逐行复核）：`core/self_learn.py` 已经把「搜索 → 取正文 → 蒸馏 → 落盘 → 入脑」做完了，
#   但**全仓没有一处调它**（只有 persona 引用了它的 RULE 文本、一个测试 import 它）⇒
#   能力躺着，行为没接上。缺的正是两头：**① 什么时候算盲区 ② 什么算吃透了、没吃透不许动手**。
# 本模块补这两头，并复用 self_learn.write_note 落同一条笔记路径（不另写一份）。
#
# 纪律：
#   · 出网一律走 self_learn._http（它内部过 body.net_guard，拒回环/私有/保留地址、不跟随跨域跳转）；
#   · 查不到 → **不放行**，如实说"没查到、别硬试"（这就是本模块存在的意义，不是失败）；
#   · 吃透判据是**可判定的**：独立来源域名数 + 关键词覆盖率，不靠"感觉学到了"。
from __future__ import annotations

import re
from urllib.parse import urlparse

import os

MIN_DOMAINS = 2      # 至少两个**独立域名**才算交叉验证过（同一站转三页不算）
MIN_COVERAGE = 0.34  # 问题的关键词在正文里的覆盖率下限（保守，宁严不松）
# 判"懂没懂"用的两道信号（都可审计，不靠感觉）：
#   · 覆盖：问题的关键词在**答案**里出现过多少（答非所问 ⇒ 不算懂）
#   · 打分：回忆引擎给最高命中条的分（擦边命中分低）
# 只信 confident 是不够的 —— 真机踩过：模糊召回会把"共享几个词的新话题"认成知道。
MIN_KNOW_SCORE = float(os.environ.get("V9_BLINDSPOT_MIN_SCORE", "6.0"))
MIN_ANSWER_COVER = float(os.environ.get("V9_BLINDSPOT_MIN_COVER", "0.5"))


def _terms(question: str) -> list:
    """从问题里取关键词（够用即可：按空白/标点切，长度≥2）。"""
    ws = [w for w in re.split(r"[\s，,。·、/？?！!：:；;\"'（）()\[\]]+", str(question or "")) if len(w) >= 2]
    return ws[:12]


def detect(question: str, *, st=None) -> dict:
    """盲区判定：**先问自己**。答不上来（不 confident）就是盲区，并给出依据。"""
    q = str(question or "").strip()
    if not q:
        return {"ok": False, "reason": "空问题"}
    try:
        from core import memory as MEM
        r = MEM.brain.ask(q, st=st)
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": True, "盲区": True, "依据": {"记忆不可用": type(exc).__name__},
                "说明": "查不了自己的记忆 ⇒ 按盲区处理（宁可去学，不许硬猜）"}
    conf = bool(r.get("confident"))
    src = r.get("sources") or []
    ans = str(r.get("answer") or "")
    scores = [float(s.get("打分") or 0) for s in src]
    top = max(scores) if scores else 0.0
    terms = _terms(q)
    hay = ans + " " + " ".join(str(s.get("原文片段") or "") for s in src)
    hit = [t for t in terms if t in hay]
    cov = (len(hit) / len(terms)) if terms else 0.0
    why = []
    if not conf:
        why.append("回忆引擎自己说不确定")
    if cov < MIN_ANSWER_COVER:
        why.append(f"答非所问：问题关键词覆盖 {cov:.0%} < {MIN_ANSWER_COVER:.0%}")
    if src and top < MIN_KNOW_SCORE:
        why.append(f"最高命中分 {top:.2f} < 过线分 {MIN_KNOW_SCORE}")
    blind = bool(why)
    return {"ok": True, "盲区": blind, "已有答案": ans,
            "依据": {"confident": conf, "命中数": len(src), "最高分": round(top, 3),
                     "答案覆盖": round(cov, 3), "命中词": hit,
                     "路子": r.get("strategies"), "为什么判盲": "；".join(why),
                     "为什么答不上来": r.get("unknown_reason") or ""},
            "口径": "三道信号一起看：回忆引擎的 confident + 问题关键词覆盖 + 最高命中分"}


def _fetch_default(query: str, *, max_pages: int) -> dict:
    """默认真出网：复用 self_learn 的搜索+抓取（内含 net_guard 安全闸）。"""
    from core import self_learn as SL
    s = SL.search(query, limit=max_pages)
    if not s.get("ok"):
        return {"ok": False, "reason": s.get("reason"), "阶段": "搜索"}
    pages = []
    for item in s["结果"][:max_pages]:
        p = SL._http(item["网址"])
        if not p.get("ok"):
            continue
        body = SL._text_of(p.get("text") or "")
        if len(body) < 300:
            continue
        keys = _terms(query)
        paras = [x.strip() for x in body.split("\n") if len(x.strip()) >= 40]
        scored = sorted(paras, key=lambda x: -sum(x.count(k) for k in keys))
        pages.append({"网址": item["网址"], "段落": [x[:400] for x in scored[:6]],
                      "字数": len(body)})
    return {"ok": bool(pages), "资料": pages,
            "reason": "" if pages else "搜到了链接但没抓到可用正文"}


def research(question: str, *, gap: str = "", max_pages: int = 3, fetcher=None) -> dict:
    """查资料：默认真出网（self_learn + net_guard）；测试可注入 fetcher 做离线验收。

    fetcher(query, max_pages=...) -> {"ok":bool, "资料":[{"网址","段落"|"正文"}]}
    """
    q = str(question or "").strip()
    got = (fetcher or _fetch_default)(q, max_pages=max_pages) if fetcher else \
          _fetch_default(q, max_pages=max_pages)
    if not got.get("ok"):
        return {"ok": False, "主题": q, "卡点": gap, "reason": got.get("reason") or "没取到资料",
                "来源": [], "口径": "查不到就如实说，绝不放行硬试"}
    pages = []
    for item in got.get("资料") or []:
        paras = item.get("段落")
        if paras is None:
            from core import self_learn as SL
            body = item.get("正文") or ""
            keys = _terms(q)
            ps = [x.strip() for x in str(body).split("\n") if len(x.strip()) >= 40]
            paras = [x[:400] for x in sorted(ps, key=lambda x: -sum(x.count(k) for k in keys))[:6]]
        if paras:
            pages.append({"网址": item["网址"], "段落": paras,
                          "字数": int(item.get("字数") or sum(len(x) for x in paras))})
    if not pages:
        return {"ok": False, "主题": q, "卡点": gap, "reason": "资料里没有可用正文",
                "来源": [], "口径": "查不到就如实说"}
    from core import self_learn as SL
    w = SL.write_note(q, gap, pages)
    return {"ok": True, "主题": q, "卡点": gap, "取到页数": len(pages),
            "来源": w.get("来源") or [], "笔记": w.get("笔记"), "入脑": w.get("入脑"),
            "口径": "公开网页 + 本项目蒸馏；非权威原文，引用前回源核对"}


def digest_quality(question: str, note: str, sources: list) -> dict:
    """吃透判据（可判定，不靠感觉）：独立域名数 + 关键词覆盖率。"""
    domains = sorted({urlparse(str(u)).netloc.lower() for u in (sources or []) if u})
    terms = _terms(question)
    text = str(note or "")
    hit = [t for t in terms if t in text]
    cov = (len(hit) / len(terms)) if terms else 0.0
    ok = (len(domains) >= MIN_DOMAINS) and (cov >= MIN_COVERAGE) and len(text) >= 400
    why = []
    if len(domains) < MIN_DOMAINS:
        why.append(f"独立来源只有 {len(domains)} 个（要 ≥{MIN_DOMAINS}：同一站转三页不算交叉验证）")
    if cov < MIN_COVERAGE:
        why.append(f"关键词覆盖 {cov:.0%} < {MIN_COVERAGE:.0%}（没打中问题的要点）")
    if len(text) < 400:
        why.append("笔记太短（<400 字），不像吃透了")
    return {"达标": ok, "独立域名": len(domains), "域名": domains, "覆盖率": round(cov, 3),
            "命中词": hit, "为什么": "；".join(why) or "来源与要点都够"}


def gate(question: str, *, gap: str = "", max_pages: int = 3, fetcher=None,
         st=None) -> dict:
    """**动手前的闸**：不盲 → 直接放行；盲 → 去查 → 吃透判据 → 达标才放行。

    返回 {可以动手, 为什么, 证据}。查不到 / 没吃透 / 来源单一 ⇒ 可以动手=False（如实说原因）。
    """
    d = detect(question, st=st)
    if not d.get("ok"):
        return {"可以动手": False, "为什么": d.get("reason"), "证据": {"detect": d}}
    if not d["盲区"]:
        return {"可以动手": True, "为什么": "自己就知道，不用现学", "证据": {"detect": d}}
    r = research(question, gap=gap, max_pages=max_pages, fetcher=fetcher)
    if not r.get("ok"):
        return {"可以动手": False,
                "为什么": f"盲区，但没查到资料（{r.get('reason')}）⇒ 不许硬试",
                "证据": {"detect": d, "research": r}}
    note = ""
    try:
        from pathlib import Path
        note = Path(str(r.get("笔记"))).read_text(encoding="utf-8")
    except Exception:                                            # noqa: BLE001
        note = ""
    q = digest_quality(question, note, r.get("来源") or [])
    return {"可以动手": bool(q["达标"]),
            "为什么": ("查到了、也吃透了（" + q["为什么"] + "）⇒ 可以动手" if q["达标"]
                       else "查到了但没吃透：" + q["为什么"] + " ⇒ 先别动手"),
            "证据": {"detect": d, "research": r, "质量": q}}


def loop(question: str, *, gap: str = "", max_pages: int = 3, fetcher=None,
         st=None) -> dict:
    """完整闭环：判盲 → 学 → **再问一次自己**（学没学进去）→ 给闸门结论。"""
    g = gate(question, gap=gap, max_pages=max_pages, fetcher=fetcher, st=st)
    if g.get("可以动手"):
        after = detect(question, st=st)
        g["学后再问"] = {"还盲吗": after.get("盲区"), "依据": after.get("依据")}
    return g


__all__ = ["detect", "research", "digest_quality", "gate", "loop",
           "MIN_DOMAINS", "MIN_COVERAGE"]
