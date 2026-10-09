# core/memory/recall.py —— 提问（读取路径）：怎么问，就怎么找；找不到就说找不到
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 三条路一起看（对齐我们自己的可用件，不引新依赖）：
#   ① 按措辞 —— FTS5 + bm25（中文走二元组）
#   ② 按时间 —— 把"今天/上周/上个月/7 月"解析成窗口，落在 t_event 上
#   ③ 按联想 —— 从问句里的实体出发沿共现图走 N 跳（"它会来找我"那种感觉）
# 纪律：
#   · **只从真存过的记忆里回答**；每条答案带出处（记忆 id + 原文片段 + 时间 + 谁的）；
#   · 分数不过线就老实说"我不确定（没存过相关的）"，绝不编；
#   · 主脑可 all_owners=1 看全部（含 100 根触手的记忆）；普通调用只看自己的。
from core.swallow import swallow as _swallow
import re
import time

from core.memory import store as S

# 过线阈值：融合分低于它就不作答
MIN_SCORE = 0.18
REL_WINDOW = {"天": 1, "周": 7, "月": 30, "年": 365}

_TIME_WORDS = (
    (re.compile(r"今天"), 0, 1),
    (re.compile(r"昨天"), 1, 1),
    (re.compile(r"前天"), 2, 1),
    (re.compile(r"这周|本周|这个星期"), 0, 7),
    (re.compile(r"上周|上个星期"), 7, 7),
    (re.compile(r"这个月|本月"), 0, 30),
    (re.compile(r"上个月|上月"), 30, 30),
    (re.compile(r"今年"), 0, 365),
    (re.compile(r"去年"), 365, 365),
)
_ABS_MONTH = re.compile(r"(\d{1,2})\s*月")
_ABS_DAY = re.compile(r"(\d{1,2})\s*[日号]")


def parse_time_window(query: str, *, now: float | None = None) -> dict | None:
    """把问句里的时间词解析成时间窗（相对/绝对都支持）。解析不出就 None（不强猜）。"""
    now = time.time() if now is None else now
    t = str(query or "")
    for rx, back_days, span_days in _TIME_WORDS:
        if rx.search(t):
            end = now - back_days * 86400.0
            return {"from": end - span_days * 86400.0, "to": end + 86400.0,
                    "词": rx.pattern, "说明": f"解析为「{rx.pattern}」窗口"}
    mo = _ABS_MONTH.search(t)
    if mo:
        lt = time.localtime(now)
        year = lt.tm_year
        m = int(mo.group(1))
        if m > lt.tm_mon:
            year -= 1
        day = int(_ABS_DAY.search(t).group(1)) if _ABS_DAY.search(t) else None
        if day:
            start = time.mktime((year, m, day, 0, 0, 0, 0, 0, -1))
            return {"from": start, "to": start + 86400.0, "词": mo.group(0),
                    "说明": f"解析为 {year} 年 {m} 月 {day} 日"}
        start = time.mktime((year, m, 1, 0, 0, 0, 0, 0, -1))
        nxt = time.mktime((year + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1,
                           0, 0, 0, 0, 0, -1))
        return {"from": start, "to": nxt, "词": mo.group(0),
                "说明": f"解析为 {year} 年 {m} 月整月"}
    return None


def _score(rows: dict, mid: str, *, add: float) -> None:
    rows.setdefault(mid, {"分": 0.0, "路子": []})
    rows[mid]["分"] += float(add)
    rows[mid]["路子"].append("")


def _wording_ratio(qt: set, raw: str) -> float:
    """问句切词有多少比例真的出现在这条记忆里。

    为什么要它：bm25 是多词 OR 的联合分，命中词一多分数绝对值就变大，直接拿
    1/(1+bm25) 归一会出现"明明命中了却低于阈值"的假否定（真机上就答成了"我不确定"）。
    所以措辞分改成**命中比例**，bm25 只用来排序。
    """
    if not qt:
        return 0.0
    ts = set(S.tokens(raw or ""))
    if not ts:
        return 0.0
    return sum(1 for t in qt if t in ts) / float(len(qt))


def ask(query: str, *, owner: str = S.OWNER_DEFAULT, all_owners: bool = False,
        limit: int = 5, hops: int = 2, st=None, now: float | None = None) -> dict:
    """回答一个问题。返回 {answer, sources, strategies, confident, unknown_reason}。"""
    st = st or S.store()
    now = time.time() if now is None else now
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "reason": "空问题", "answer": "", "sources": []}

    qt = set(S.tokens(q))
    cand: dict = {}
    # ① 按措辞
    words = st.by_terms(q, owner=owner, all_owners=all_owners, limit=30)
    for w in words:
        cand.setdefault(w["id"], {"分": 0.0, "路子": []})
        cand[w["id"]]["分"] += 0.62 * float(w["分数"])
        cand[w["id"]]["路子"].append("措辞")
    # ② 按时间
    win = parse_time_window(q, now=now)
    if win:
        for m in st.in_window(win["from"], win["to"], owner=owner,
                              all_owners=all_owners, limit=30):
            cand.setdefault(m["id"], {"分": 0.0, "路子": []})
            cand[m["id"]]["分"] += 0.45
            cand[m["id"]]["路子"].append("时间")
    # ③ 按联想（两跳）
    from core.memory import encoder as E
    for name, _etype, _w in E.extract_entities(q)[:4]:
        for m in st.memories_of_entity(name, owner=owner, all_owners=all_owners, limit=10):
            cand.setdefault(m["id"], {"分": 0.0, "路子": []})
            cand[m["id"]]["分"] += 0.34
            cand[m["id"]]["路子"].append("联想")
        for nb in st.neighbours(name, hops=hops, limit=12):     # 二跳：间接相关
            for m in st.memories_of_entity(nb, owner=owner, all_owners=all_owners, limit=6):
                cand.setdefault(m["id"], {"分": 0.0, "路子": []})
                cand[m["id"]]["分"] += 0.18
                cand[m["id"]]["路子"].append("二跳")

    # 融合：热度当权重（常用的更容易被想起来），但不许把热度当"相关性"
    hits = []
    for mid, c in cand.items():
        m = st.row_of(mid)
        if not m or m.get("merged_into"):
            continue
        heat_w = 0.7 + 0.3 * float(m.get("heat") or 0.0)
        # 措辞路：按**命中比例**加分（bm25 只用于排序，不做归一，避免假否定）
        ratio = _wording_ratio(qt, m.get("raw") or "")
        if ratio > 0:
            c["分"] += 0.75 * ratio
        score = round(float(c["分"]) * heat_w, 4)
        hits.append({"id": mid, "分": score, "路子": sorted(set(c["路子"])),
                     "raw": m.get("raw"), "owner": m.get("owner"),
                     "kind": m.get("kind"), "category": m.get("category"),
                     "scope": m.get("scope"), "t_event": m.get("t_event"),
                     "heat": m.get("heat"), "tier": m.get("tier"),
                     "private": bool(m.get("private"))})
    hits.sort(key=lambda x: -x["分"])
    top = [h for h in hits if h["分"] >= MIN_SCORE][: max(1, int(limit))]

    strategies = {"措辞命中": len(words), "时间窗": (win or {}).get("说明", "无时间词"),
                  "联想命中": sum(1 for h in hits if "联想" in h["路子"] or "二跳" in h["路子"])}
    if not top:
        seen_any = len(hits)
        reason = ("压根没有存过相关的东西" if seen_any == 0
                  else f"有几条擦边的（最高 {hits[0]['分']}）但都不到过线分 {MIN_SCORE}")
        return {"ok": True, "confident": False, "answer": "我不确定 —— " + reason + "。",
                "sources": [], "strategies": strategies, "candidates": seen_any,
                "unknown_reason": reason, "threshold": MIN_SCORE}

    # 作答：把命中的原文拼出来（带出处），不加工、不演绎
    lines = []
    for i, h in enumerate(top, 1):
        when = time.strftime("%Y-%m-%d", time.localtime(h["t_event"] or 0))
        who = "我（主脑）" if h["owner"] == S.OWNER_DEFAULT else (
            f"触手 {h['owner']}" if str(h["owner"]).startswith("t") else str(h["owner"]))
        lines.append(f"{i}. {h['raw']}　〔{when} · {who} · {h['category'] or '未分类'} · "
                     f"命中：{'/'.join(h['路子'])}〕")
    for h in top:                       # 被用到 → 回暖
        try:
            st.touch(h["id"], owner=str(h["owner"]))
        except Exception as e:
            _swallow(__file__, e)
    conf = top[0]["分"] >= 0.45
    head = "" if conf else "（下面是我最接近的几条，但我不敢说就是你要的）\n"
    return {"ok": True, "confident": conf, "answer": head + "\n".join(lines),
            "sources": [{"id": h["id"], "原文片段": (h["raw"] or "")[:80], "谁": h["owner"],
                         "分类": h["category"], "记忆域": h["scope"], "热度": h["heat"],
                         "层": h["tier"], "时间": h["t_event"], "打分": h["分"],
                         "命中路子": h["路子"]} for h in top],
            "strategies": strategies,
            "unknown_reason": "" if conf else "只有擦边命中，建议换个说法或补一条记忆"}


def sources_of(query: str, **kw) -> list:
    return ask(query, **kw).get("sources") or []


__all__ = ["ask", "parse_time_window", "sources_of", "MIN_SCORE"]
