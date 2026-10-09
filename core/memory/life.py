# core/memory/life.py —— 大脑生命起源存档（主脑的生平，只增不改）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："设计一个大脑生命起源存档，把触手的记忆也存放进去分类好。"
#
# 这不是又一张日志表，而是三件事：
#   ① **诞生记录（genesis）**：大脑什么时候出生、出生时是什么、为什么出生、第一句话是什么
#      —— 写一次，之后只读，永不改写（成长可以加，出生不能改）；
#   ② **生平编年（chronicle）**：按分类归档的一生事件 —— 诞生/首次/能力/记忆里程碑/整理/
#      版本/触手（100 根触手的记忆里程碑也进这里，分类到"触手"）；
#   ③ **可为任意时间点重建"我当时是什么样"**：给一个时刻，回放当时的规模与知识面。
#
# 分类（与 store 的记忆分类分开，这是"生平"的类目）：
#   诞生 · 首次 · 能力 · 记忆 · 整理 · 版本 · 触手 · 用户
from core.swallow import swallow as _swallow
import time

LIFE_CATEGORIES = ("诞生", "首次", "能力", "记忆", "整理", "版本", "触手", "用户")


def _guarded_imports():
    out = {}
    for name, fn in (("记忆数", lambda: _mem_count()),
                     ("能力面", lambda: _capability_face()),
                     ("固化组", lambda: _solid_groups()),
                     ("台账条", lambda: _ledger_rows())):
        try:
            out[name] = fn()
        except Exception as exc:                               # noqa: BLE001
            out[name] = f"不可用（{type(exc).__name__}）"
    return out


def _mem_count() -> int:
    from core.memory import store as S
    return int(S.store().counts(all_owners=True).get("记忆") or 0)


def _capability_face() -> dict:
    from core import capability_map as cm
    t = cm._totals()
    return {"云插件槽": t.get("云插件"), "数据库槽": t.get("数据库槽"),
            "触手": t.get("触手"), "算力活": t.get("算力活")}


def _solid_groups() -> int:
    from core import solidify as S
    return len(S.status().get("names") or [])


def _ledger_rows() -> int:
    from core import deploy_ledger as D
    return sum((D.summary().get("by_kind") or {}).values())


def born(*, first_words: str = "", st=None, force: bool = False) -> dict:
    """写**诞生记录**：只写一次；已有就返回既有的（不许改写出生）。"""
    from core.memory import store as S
    st = st or S.store()
    got = st.genesis()
    if got and not force:
        return {"ok": True, "already": True, "诞生": got}
    ts = time.time()
    rec = st.life_add(
        "genesis", "主脑诞生", category="诞生", life_id="genesis", ts=ts,
        detail="GBT小土豆V9 · 原生大脑正式启用：从这一刻起，它记得自己说过什么、"
               "做过什么、学到什么；原文永不改写，褪色只让它更难被撞见。",
        importance=1.0,
        meta={"第一句话": str(first_words or ""), "出生时的样子": _guarded_imports(),
              "口径": "出生记录只写一次，成长只增不改",
              "存档位置": str(st.path)})
    st.event("genesis", "主脑诞生记录已写入")
    if first_words:
        st.capture(str(first_words), origin="genesis", scope="主脑记忆",
                   category="里程碑", meta={"里程碑": "第一句话"})
    return {"ok": True, "already": False, "诞生": st.genesis(), **rec}


def note(kind: str, title: str, *, detail: str = "", category: str = "",
         owner: str = "main", ref: str = "", importance: float = 0.5,
         meta: dict | None = None, st=None, dedupe: bool = True) -> dict:
    """写一条生平（自动分类 + 同题去重，避免把同一件事记两遍）。"""
    from core.memory import store as S
    st = st or S.store()
    cat = category or (kind if kind in LIFE_CATEGORIES else "能力")
    if dedupe:
        for row in st.life_list(limit=500):
            # 注意：别用 int() 比重要度 —— int(0.6)=0 会让"≥0.5"永远为假，去重直接失效（踩过）
            if row["kind"] == str(kind)[:32] and row["title"] == str(title)[:200] and \
                    float(row.get("importance") or 0) >= float(importance):
                return {"ok": True, "already": True, "life_id": row["life_id"]}
    return st.life_add(kind, title, detail=detail, category=cat, owner=owner, ref=ref,
                       importance=importance, meta=meta)


def milestones(*, st=None) -> dict:
    """回看一生里的"第一次"：第一次记忆、第一次合并、第一次整理、第一次触手记忆…"""
    from core.memory import store as S
    st = st or S.store()
    out = {}
    rows = sorted(st.list(all_owners=True, limit=100000), key=lambda m: m["t_created"])
    if rows:
        out["第一次记忆"] = {"时间": rows[0]["t_created"], "原文": rows[0]["raw"][:80],
                             "谁": rows[0]["owner"]}
    tent = [m for m in rows if str(m["owner"]).startswith("t")]
    if tent:
        out["第一次触手记忆"] = {"时间": tent[0]["t_created"], "谁": tent[0]["owner"],
                                 "原文": tent[0]["raw"][:80]}
    mg = st.merges(limit=1000)
    if mg:
        out["第一次合并"] = {"时间": mg[-1]["ts"], "并入": len(mg[-1]["member_ids"])}
    ev = st.life_list(kind="consolidate", newest_first=False, limit=1)
    if ev:
        out["第一次夜间整理"] = {"时间": ev[0]["ts"], "说明": ev[0]["title"]}
    return out


def chronicle(*, category: str = "", limit: int = 60, st=None) -> list:
    """生平编年（新的在前）。诞生那条永远排最前单独展示，这里给事件流。"""
    from core.memory import store as S
    st = st or S.store()
    rows = [r for r in st.life_list(category=category, limit=limit)
            if r["kind"] != "genesis"]
    return rows


def at(when: float, *, st=None) -> dict:
    """回放：那个时刻，这个大脑是什么样（记忆规模 / 分类分布 / 能力面）。"""
    from core.memory import store as S
    st = st or S.store()
    rows = [m for m in st.list(all_owners=True, limit=100000, include_merged=True)
            if float(m.get("t_created") or 0) <= float(when)]
    by_cat: dict = {}
    by_who: dict = {}
    for m in rows:
        by_cat[m.get("category") or "未分类"] = by_cat.get(m.get("category") or "未分类", 0) + 1
        k = "主脑" if m.get("owner") == "main" else (
            "触手" if str(m.get("owner")).startswith("t") else str(m.get("owner")))
        by_who[k] = by_who.get(k, 0) + 1
    life = [r for r in st.life_list(newest_first=False, limit=1000)
            if float(r["ts"]) <= float(when)]
    return {"时刻": when, "ISO": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(when)),
            "当时记忆数": len(rows), "分类分布": by_cat, "按主体": by_who,
            "当时已发生的生平": len(life)}


def onboard(*, st=None) -> dict:
    """冷启动：把"这个大脑已经活过的那部分"补记进存档。

    用**真实已发生的事实**回填（已有多少记忆、多少触手记忆、多少固化组、什么版本），
    不编造历史；同题去重，重复调用不会越记越多。
    """
    from core.memory import store as S
    from core import solidify as SD
    st = st or S.store()
    born(st=st)                                            # 没出生就先出生
    counts = st.counts(all_owners=True)
    facts = _guarded_imports()
    added = []
    for kind, title, detail, cat in (
        ("capability", "能力面登记", f"云插件/库槽/触手：{facts.get('能力面')}", "能力"),
        ("version", "固化档案", f"现有固化组 {facts.get('固化组')} 组（可回滚）", "版本"),
        ("memory", "记忆归档", f"统一记忆 {counts.get('记忆')} 条"
                               f"（含并入 {counts.get('被并入的')} 条）", "记忆"),
        ("tentacle", "触手记忆并入", f"触手记忆 {_tentacle_rows(st)} 条已归档分类", "触手"),
        ("consolidate", "台账与整理", f"变更台账 {facts.get('台账条')} 条；"
                                      f"合并 {counts.get('合并次数')} 次", "整理"),
    ):
        r = note(kind, title, detail=str(detail), category=cat, st=st, importance=0.6)
        added.append({"项": title, "已存在": bool(r.get("already")), "life_id": r.get("life_id")})
    try:
        rev = SD.status().get("names") or []
        note("version", "当前固化快照", detail=f"共 {len(rev)} 组", category="版本",
             st=st, importance=0.5)
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "新增或已存在": added, "存档位置": str(st.path)}


def _tentacle_rows(st) -> int:
    rows = st.list(all_owners=True, limit=100000)
    return sum(1 for m in rows if str(m.get("owner")).startswith("t"))


def status(*, st=None) -> dict:
    from core.memory import store as S
    st = st or S.store()
    life = st.life_list(limit=1000)
    by_cat: dict = {}
    for r in life:
        by_cat[r.get("category") or "未分类"] = by_cat.get(r.get("category") or "未分类", 0) + 1
    g = st.genesis()
    return {"出生": ({"时间": g["ts"], "标题": g["title"],
                      "第一句话": (g.get("meta") or {}).get("第一句话", "")} if g else None),
            "生平条数": len(life), "分类分布": by_cat,
            "里程碑": milestones(st=st),
            "存档位置": str(st.path),
            "口径": "出生只写一次；生平只增不改；触手记忆同样归档（分类=触手）"}


__all__ = ["LIFE_CATEGORIES", "born", "note", "chronicle", "milestones", "at",
           "onboard", "status"]
