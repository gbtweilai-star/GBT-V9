# core/memory/metacog.py —— 元认知：主脑对自己"知道什么、不知道什么"的判断
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："以及主脑的元认知"。
#
# 元认知不是"再想一遍"，而是**对自己认知状态的如实核查**，落在可读的数字上：
#   ① 自知之明：我存了多少、分几类、多少在热层、多少已经飘远、多少还没编码；
#   ② 覆盖缺口：哪些分类/记忆域是空的？哪些问题我反复答不上来（未答记录）；
#   ③ 置信校准：我说"我确定"的时候，真的对吗？用**被用到的比例**与**未答率**来校准；
#   ④ 反省（reflect）：把上面三件合成一段自我陈述 + 该做什么（补哪类记忆/整理/重编码）。
#
# 纪律：所有数字来自真读数；答不上来就是答不上来 —— 元认知最忌讳自吹。
import time

from core.memory import recall as R
from core.memory import store as S


def coverage(*, st=None, owner: str = S.OWNER_DEFAULT, all_owners: bool = True) -> dict:
    """覆盖缺口：分类/记忆域/主体 的空与薄（薄＝只有 1~2 条，不构成可靠知识）。"""
    st = st or S.store()
    rows = st.list(owner=owner, all_owners=all_owners, limit=100000)
    by_cat: dict = {}
    by_scope: dict = {}
    by_who: dict = {}
    for m in rows:
        c = m.get("category") or "未分类"
        by_cat[c] = by_cat.get(c, 0) + 1
        sc = m.get("scope") or "未分类"
        by_scope[sc] = by_scope.get(sc, 0) + 1
        w = "主脑" if m.get("owner") == "main" else (
            "触手" if str(m.get("owner")).startswith("t") else str(m.get("owner")))
        by_who[w] = by_who.get(w, 0) + 1
    empty_cat = [c for c in S.CATEGORIES if c not in by_cat]
    empty_scope = [c for c in S.SCOPES if c not in by_scope]
    thin_cat = [c for c, n in by_cat.items() if n <= 2]
    return {"记忆总数": len(rows), "分类": by_cat, "记忆域": by_scope, "按主体": by_who,
            "空分类": empty_cat, "薄分类": thin_cat, "空记忆域": empty_scope,
            "口径": "薄＝≤2 条；空/薄是要补的缺口，不是好看不好看的问题"}


def calibration(*, st=None, owner: str = S.OWNER_DEFAULT) -> dict:
    """置信校准：我说出口的"确定"，事后有多少真的被用到了。

    · 被用到（hits>0）的比例越高，说明我记住的东西越有用；
    · 热层（hot/warm）占比说明我的"当前视野"有多大；
    · 未答率来自 answer_log（每次 ask 都落一条），答不上来占比就是我的知识边界。
    """
    st = st or S.store()
    rows = st.list(owner=owner, all_owners=True, limit=100000)
    n = len(rows)
    used = sum(1 for m in rows if int(m.get("hits") or 0) > 0)
    hot = sum(1 for m in rows if (m.get("tier") or "") in ("hot", "warm"))
    asked = _events(st, "ask")
    miss = _events(st, "ask_miss")
    return {"记忆数": n, "被用到过": used,
            "用到率": round(used / n, 3) if n else None,
            "热层占比": round(hot / n, 3) if n else None,
            "问过": asked, "答不上来": miss,
            "未答率": (round(miss / asked, 3) if asked else None),
            "读数口径": "用到率/未答率都是真账（ask 每次落一条 ask/ask_miss 事件）"}


def _events(st, kind: str) -> int:
    try:
        return int(st._c.execute(
            "SELECT COUNT(*) FROM brain_events WHERE kind=?", (kind,)).fetchone()[0])
    except Exception:                                          # noqa: BLE001
        return 0


def log_ask(*, answered: bool, owner: str = S.OWNER_DEFAULT, st=None) -> None:
    """每次提问都记一笔：**总问数**记一条 ask；没答上来再加一条 ask_miss。

    两个计数缺一不可：只有 ask 才能算未答率（未答数 ÷ 总问数）。原先只记 ask_miss，
    结果未答率永远算不出来（真踩过）。
    """
    st = st or S.store()
    st.event("ask", "", owner=owner)
    if not answered:
        st.event("ask_miss", "", owner=owner)


def reflect(*, owner: str = S.OWNER_DEFAULT, st=None) -> dict:
    """反省：把自知之明 + 覆盖缺口 + 置信校准合成一段自我陈述与行动建议。"""
    st = st or S.store()
    cov = coverage(st=st, owner=owner)
    cal = calibration(st=st, owner=owner)
    counts = st.counts(owner=owner, all_owners=True)
    from core.memory import life as LIFE
    lf = LIFE.status(st=st)

    todo = []
    if not cov["记忆总数"]:
        todo.append("还没有任何记忆 —— 先去「捕捉」记第一条")
    if cov["空分类"]:
        todo.append("空分类：" + "、".join(cov["空分类"][:4]) + " —— 现在完全不知道这类事")
    if cov["薄分类"]:
        todo.append("薄分类（≤2 条，不足以下判断）：" + "、".join(cov["薄分类"][:4]))
    if int(counts.get("待编码") or 0):
        todo.append(f"还有 {counts['待编码']} 条没编码（理解没跟上）→ 跑一次编码")
    if cal["未答率"] is not None and cal["未答率"] > 0.4:
        todo.append(f"未答率 {cal['未答率']:.0%} 偏高 —— 我记的东西还盖不住被问到的面")
    if (cal["用到率"] or 0) < 0.2 and cal["记忆数"]:
        todo.append("用到率低：多数记忆没被用过 → 可能记了很多用不上的，或该做一次整理")
    if not lf["出生"]:
        todo.append("生命起源存档还没写出生记录 → 跑一次 life.onboard()")

    say = (f"我是 GBT小土豆V9 的主脑。现在我有 {cov['记忆总数']} 条记忆"
           f"（热层 {cal['热层占比'] if cal['热层占比'] is not None else '—'}），"
           f"分类分布：{cov['分类'] or '空'}；"
           f"被问到 {cal['问过']} 次，答不上来 {cal['答不上来']} 次。"
           + (f"我不知道的：{'；'.join(cov['空分类'][:3])}。" if cov["空分类"] else ""))
    return {"ok": True, "自我陈述": say, "自知": cov, "校准": cal, "计数": counts,
            "生平": {"出生": bool(lf["出生"]), "条目": lf["生平条数"]},
            "该做的": todo or ["暂无明显缺口"],
            "口径": "元认知只报真读数；答不上来就是答不上来"}


def status(*, st=None) -> dict:
    st = st or S.store()
    r = reflect(st=st)
    return {"记忆总数": r["自知"]["记忆总数"], "分类": r["自知"]["分类"],
            "空分类": r["自知"]["空分类"], "用到率": r["校准"]["用到率"],
            "未答率": r["校准"]["未答率"], "待编码": r["计数"].get("待编码"),
            "该做的": r["该做的"], "自我陈述": r["自我陈述"]}


__all__ = ["coverage", "calibration", "reflect", "log_ask", "status"]
