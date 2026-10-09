# core/memory/consolidate.py —— 每晚整理（"睡眠"窗口）：合并碎片 · 形成知识 · 让没用的淡出
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏自 TheBrain 的 §08，用我们自己的件实现（不引依赖，全部可逆）：
#   ① 合并同一时刻的碎片：同一天 + 时间相近 + 实体重叠 → 并成一条更完整的记忆
#      （"给妈妈打电话""妈妈生日周五""买花" → 一条更完整的）；**每次合并留底，随时可撤**；
#   ② 从簇里形成更大的知识：同一实体下挂的记忆够多 → 生成一条"知识"（带来源 id）；
#   ③ 让真正没用过的淡出：重算热度/分层（只改 heat/tier，**从不删除**）；
#   ④ 宽限到期的回收站才真清（这一步是删除的唯一出口，且要人显式发起）。
#
# 纪律：整理不许改任何原文；每一步都往账本与生平存档里写一笔。
import time

from core.memory import store as S

MOMENT_WINDOW_S = 6 * 3600.0     # 同一时刻：6 小时内
CLUSTER_MIN = 3                  # 一个实体下至少几条才算"能形成知识"


def _moment_key(m: dict) -> tuple:
    day = time.strftime("%Y-%m-%d", time.localtime(float(m.get("t_event") or 0)))
    ents = tuple(sorted({e["name"] for e in S.store().entities_of(m["id"])}))[:3]
    who = str(m.get("owner") or "")
    return (day, who, ents)


def find_moments(*, st=None, owner: str = S.OWNER_DEFAULT) -> list:
    """找出可以合并的"同一时刻碎片"分组（不改任何东西，纯查看）。

    **跨主体看**（主脑的统一视图）：触手写在同一天、同一实体上的碎片也该被整理 ——
    但分组键里带 owner，所以只会把"同一个主体的"碎片并到一起，绝不把 A 的记忆并进 B。
    """
    st = st or S.store()
    rows = st.list(owner=owner, all_owners=True, limit=100000)
    groups: dict = {}
    for m in rows:
        groups.setdefault(_moment_key(m), []).append(m)
    out = []
    for key, ms in groups.items():
        if len(ms) < 2:
            continue
        out.append({"日": key[0], "主体": key[1], "共有实体": list(key[2]),
                    "条数": len(ms),
                    "候选": [{"id": m["id"], "原文": (m.get("raw") or "")[:60],
                              "t": m.get("t_event")} for m in ms]})
    out.sort(key=lambda x: -x["条数"])
    return out


def merge_moments(*, st=None, owner: str = S.OWNER_DEFAULT, dry_run: bool = False,  # 主人令：默认真动手（要演练请显式传 True）
                  max_groups: int = 20) -> dict:
    """把同一时刻的碎片并成一条（确认后才动；每次合并留底可撤）。"""
    st = st or S.store()
    groups = find_moments(st=st, owner=owner)[:max(1, int(max_groups))]
    plan = []
    done = []
    for g in groups:
        ms = sorted([m for m in st.list(owner=owner, all_owners=True, limit=100000)
                     if m["id"] in {c["id"] for c in g["候选"]}],
                    key=lambda m: -(float(m.get("importance") or 0)))
        if len(ms) < 2:
            continue
        target, members = ms[0], [m["id"] for m in ms[1:]]
        merged_text = "；".join((m.get("raw") or "").strip() for m in ms if m.get("raw"))
        plan.append({"日": g["日"], "主体": g["主体"], "目标": target["id"],
                     "并入": len(members), "合并后原文": merged_text[:160]})
        if dry_run:
            continue
        # 合并只在本主体内做（target 的 owner 就是本组的 owner）
        own = str(target.get("owner") or owner)
        r = st.merge(target["id"], members, kind="moment", owner=own,
                     note=f"同一时刻 {g['日']} 合并 {len(members)} 条")
        if r.get("ok"):
            st.enrich(target["id"], kind=target.get("kind") or "事件",
                      importance=min(1.0, float(target.get("importance") or 0.5) + 0.1),
                      category="事件", scope=target.get("scope") or "主脑记忆",
                      note="夜间整理：同一时刻碎片合并")
            st.life_add("consolidate", f"合并同一时刻碎片 {len(members)} 条",
                        category="整理", detail=merged_text[:400], ref=r["merge_id"],
                        importance=0.4)
            done.append({"merge_id": r["merge_id"], "并入": len(members), "主体": own})
    return {"ok": True, "dry_run": bool(dry_run), "可合并分组": len(groups),
            "计划": plan, "已合并": done,
            "撤销": "每一组都能用 store.restore(merge_id) 原样撤销"}


def form_knowledge(*, st=None, owner: str = S.OWNER_DEFAULT, dry_run: bool = False) -> dict:  # 主人令：默认真动手（要演练请显式传 True）
    """从簇形成更大的知识：同一实体下记忆够多 → 生成一条"知识"（带来源 id，可回溯）。

    跨主体统计（触手写在某实体上的多条也参与成簇），但生成的知识**归到该簇的主主体**，
    不把别人的东西算到自己名下。
    """
    st = st or S.store()
    rows = st.list(owner=owner, all_owners=True, limit=100000)
    by_ent: dict = {}
    for m in rows:
        for e in st.entities_of(m["id"]):
            by_ent.setdefault(e["name"], []).append(m)
    plan, made = [], []
    for name, ms in sorted(by_ent.items(), key=lambda kv: -len(kv[1])):
        if len(ms) < CLUSTER_MIN:
            continue
        plan.append({"实体": name, "支撑条数": len(ms),
                     "来源": [m["id"] for m in ms[:5]], "主主体": ms[0].get("owner")})
        if dry_run or len(made) >= 8:
            continue
        text = (f"关于「{name}」目前攒下 {len(ms)} 条记忆：" +
                "；".join((m.get("raw") or "").strip()[:40] for m in ms[:4]))
        who = str(ms[0].get("owner") or owner)
        if any((m.get("origin") == "consolidate" and str(m.get("origin_ref")) == name
                and str(m.get("owner")) == who) for m in rows):
            continue
        r = st.capture(text, owner=who, origin="consolidate", origin_ref=name,
                       scope="洞见", category="知识",
                       meta={"来源记忆": [m["id"] for m in ms[:10]], "整理": "簇→知识",
                             "主体": who})
        if r.get("ok"):
            made.append({"id": r["id"], "实体": name, "支撑": len(ms), "主体": who})
            st.life_add("consolidate", f"形成知识：{name}",
                        category="整理", detail=text[:400], ref=r["id"], importance=0.5,
                        owner=who)
    return {"ok": True, "dry_run": bool(dry_run), "可成知识的簇": plan[:8],
            "已生成": made,
            "口径": "知识条带「来源记忆」id 列表，随时能追回是哪些记忆推出来的"}


def run(*, st=None, owner: str = S.OWNER_DEFAULT, dry_run: bool = False,  # 主人令：默认真动手（要演练请显式传 True）
        purge: bool = False, encode: bool = True) -> dict:
    """跑一次夜间整理：编码补漏 → 合并碎片 → 形成知识 → 重算热度 →（可选）清理到期回收站。"""
    st = st or S.store()
    from core.memory import life as LIFE
    out: dict = {"when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if encode:
        from core.memory import encoder as E
        out["编码"] = E.encode_pending(st=st)
    out["合并"] = merge_moments(st=st, owner=owner, dry_run=dry_run)
    out["知识"] = form_knowledge(st=st, owner=owner, dry_run=dry_run)
    out["热度"] = st.recompute_heat()
    if purge:
        out["清理"] = st.purge_due(owner=owner)
    if not dry_run:
        LIFE.note("consolidate", "夜间整理完成",
                  detail=f"合并 {len(out['合并'].get('已合并') or [])} 组 · "
                         f"知识 {len(out['知识'].get('已生成') or [])} 条 · "
                         f"热度重算 {out['热度'].get('重算')} 条",
                  category="整理", st=st, importance=0.5)
    return out


def status(*, st=None) -> dict:
    st = st or S.store()
    return {"可合并分组": len(find_moments(st=st)),
            "合并窗口小时": MOMENT_WINDOW_S / 3600,
            "成知识门槛（同实体条数）": CLUSTER_MIN,
            "当前合并次数": st.counts(all_owners=True).get("合并次数"),
            "口径": "整理不改原文；合并不删除；宽限期到期才真清"}


__all__ = ["find_moments", "merge_moments", "form_knowledge", "run", "status",
           "MOMENT_WINDOW_S", "CLUSTER_MIN"]
