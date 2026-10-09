# core/memory/__init__.py —— 原生大脑（统一记忆 · 生命起源存档 · 元认知）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 一次拿全的入口：捕捉 → 理解 → 记忆 → 热度/褪色 → 提问 → 提醒 → 夜间整理 → 隐私
# 全部落在**我们自己的件**上：db_fleet 的 memory 槽、compute_router 的 embed 路由、
# media.queue 的队列、solidify/deploy_ledger 的存档与台账。
#
# 用法（也是 longterm 契约的底座）：
#   from core.memory import brain
#   brain.remember("下周五是妈妈生日，记得买花")     # 立即返回
#   brain.ask("妈妈生日要买什么")                    # 带出处
#   brain.reflect()                                  # 元认知
#   brain.status()                                   # 面板一屏
from core.swallow import swallow as _swallow
import time

from core.memory import consolidate, encoder, heat, inform, life, metacog, recall, store, unify

OWNER_DEFAULT = store.OWNER_DEFAULT
OWNER_MAIN = store.OWNER_DEFAULT


class _Brain:
    """原生大脑的门面。所有方法都能单独调用，也能串成一条链。"""

    # ── 捕捉（即时） ──
    def remember(self, text: str, *, owner: str = OWNER_MAIN, private: bool = False,
                 origin: str = "capture", scope: str = "", category: str = "",
                 meta: dict | None = None, queue=None, led=None, st=None) -> dict:
        st = st or store.store()
        r = store.Store.capture(st, text, owner=owner, private=private, origin=origin,
                               scope=scope, category=category, meta=meta)
        if r.get("ok"):
            from core.memory import worker as W
            r["编码排队"] = W.enqueue(r["id"], queue=queue, led=led)
        return r

    # ── 提问（只从存过的里答，带出处；答不上来就说） ──
    def ask(self, query: str, *, owner: str = OWNER_MAIN, all_owners: bool = False,
            limit: int = 5, st=None) -> dict:
        st = st or store.store()
        r = recall.ask(query, owner=owner, all_owners=all_owners, limit=limit, st=st)
        try:
            metacog.log_ask(answered=bool(r.get("confident")), owner=owner, st=st)
        except Exception as e:
            _swallow(__file__, e)
        return r

    # ── 元认知（对自己认知状态的核查 + 反省） ──
    def reflect(self, *, owner: str = OWNER_MAIN, st=None) -> dict:
        return metacog.reflect(owner=owner, st=st or store.store())

    def coverage(self, *, st=None) -> dict:
        return metacog.coverage(st=st or store.store())

    def calibration(self, *, st=None) -> dict:
        return metacog.calibration(st=st or store.store())

    # ── 统一（把各处记忆收进主脑，分类好；幂等） ──
    def unify(self, *, encode: bool = True, limit: int = 500, st=None) -> dict:
        return unify.unify_all(st=st or store.store(), encode=encode, limit=limit)

    # ── 生命起源存档 ──
    def born(self, *, first_words: str = "", st=None) -> dict:
        return life.born(first_words=first_words, st=st or store.store())

    def life(self, *, category: str = "", limit: int = 60, st=None) -> dict:
        st = st or store.store()
        g = st.genesis()
        return {"出生": ({"时间": g["ts"], "标题": g["title"], "编号": g["life_id"],
                          "说明": g["detail"],
                          "第一句话": (g.get("meta") or {}).get("第一句话", "")} if g else None),
                "编年": life.chronicle(category=category, limit=limit, st=st),
                "里程碑": life.milestones(st=st)}

    def life_at(self, when: float, *, st=None) -> dict:
        return life.at(when, st=st or store.store())

    # ── 热度 / 整理 / 提醒 ──
    def heat(self, memory_id: str, *, st=None) -> dict:
        st = st or store.store()
        m = st.row_of(memory_id)
        return heat.explain(m, now=time.time()) if m else {"ok": False, "reason": "找不到"}

    def consolidate(self, *, dry_run: bool = False, purge: bool = False, st=None) -> dict:  # 主人令：默认真动手（要演练请显式传 True）
        return consolidate.run(dry_run=dry_run, purge=purge, st=st or store.store())

    def nudges(self, *, st=None) -> dict:
        st = st or store.store()
        return inform.inbox(st=st)

    def nudge_act(self, nudge_id: str, *, action: str = "done", st=None) -> dict:
        return inform.act(nudge_id, action=action, st=st or store.store())

    # ── 隐私：回收站（宽限期，可复原） ──
    def recycle(self, memory_id: str, *, grace_days: float = 7.0, reason: str = "",
                st=None) -> dict:
        return (st or store.store()).tombstone(memory_id, grace_days=grace_days, reason=reason)

    def restore(self, memory_id: str, *, st=None) -> dict:
        return (st or store.store()).untombstone(memory_id)

    def bin(self, *, st=None) -> list:
        return (st or store.store()).recycle_bin()

    def forget(self, *, st=None) -> dict:
        """把宽限期到期的真正清掉（删除的唯一出口；要人显式调）。"""
        return (st or store.store()).purge_due()

    # ── 一屏状态 ──
    def status(self, *, st=None, led=None) -> dict:
        from core.memory import worker as W
        st = st or store.store()
        return {"统一记忆": st.counts(all_owners=True),
                "主体": st.by_owner_kind(), "记忆域": st.by_scope(),
                "分类": st.by_category(), "分层": st.by_tier(),
                "向量通道": st.by_embed(),
                "编码": W.status(led=led, st=st),
                "热度口径": {"半衰期天": heat.HALF_LIFE_DAYS, "层": list(heat.TIERS),
                             "褪色": "只改热度/分层，从不删除"},
                "通知": inform.status(st=st),
                "整理": consolidate.status(st=st),
                "生命起源存档": life.status(st=st),
                "元认知": metacog.status(st=st),
                "库位置": str(st.path),
                "口径": "主脑 + 100 触手 + 用户 + 系统 同库统一；读时按 owner 隔离，"
                        "主脑可见全量"}

    # ── 与既有接口同形（直接替掉进程内/文件式老存储） ──
    def remember_kv(self, tid: str, key: str, value) -> None:
        (store.store()).remember(tid, key, value)

    def recall_kv(self, tid: str, key: str | None = None):
        return (store.store()).recall(tid, key)

    def inspect_all(self) -> dict:
        return (store.store()).inspect_all()


brain = _Brain()


# ── longterm 契约（memory.longterm@1：retain / recall / reflect）的实现 ──
class LongTermMemory:
    """core/memory/longterm.py 声明的契约，这里给出**真实现**。

    retain  落一条记忆（返回 facts = 它的 id/分类/实体）
    recall  按问句召回（返回 answer = 拼好的答案；sources = 出处清单）
    reflect 元认知反省（返回 facts = 缺口/校准，answer = 自我陈述）
    """

    def __init__(self, owner: str = OWNER_MAIN, **kw) -> None:
        self.owner = owner

    def run(self, action, content=None, query=None, document_id=None, metadata=None):
        a = str(action or "")
        if a == "retain":
            r = brain.remember(str(content or ""), owner=self.owner,
                               origin="longterm",
                               scope=(metadata or {}).get("scope", "") if isinstance(metadata, dict) else "")
            if not r.get("ok"):
                return {"facts": [], "answer": str(r.get("reason") or "没记住"), "sources": []}
            return {"facts": [{"id": r["id"], "scope": r.get("scope"),
                               "document_id": document_id}],
                    "answer": "已记住（理解在后台进行）", "sources": []}
        if a == "recall":
            r = brain.ask(str(query or content or ""), owner=self.owner)
            return {"facts": [s.get("id") for s in (r.get("sources") or [])],
                    "answer": r.get("answer") or "", "sources": r.get("sources") or []}
        if a == "reflect":
            r = brain.reflect(owner=self.owner)
            return {"facts": r.get("该做的") or [], "answer": r.get("自我陈述") or "",
                    "sources": []}
        return {"facts": [], "answer": f"不认识这个动作：{a}（只认 retain/recall/reflect）",
                "sources": []}

    def spec(self) -> dict:
        return {"inputs": {"action": {"type": "enum", "required": True,
                                      "values": ["retain", "recall", "reflect"]},
                           "content": {"type": "any"}, "query": {"type": "string"},
                           "document_id": {"type": "string"},
                           "metadata": {"type": "object"}},
                "outputs": {"facts": {"type": "array"}, "answer": {"type": "string"},
                            "sources": {"type": "array"}},
                "idempotent": False, "risk": "low",
                "backend": "core.memory（统一记忆库 + 生命起源存档 + 元认知）"}


__all__ = ["brain", "LongTermMemory", "store", "heat", "encoder", "recall", "inform",
           "consolidate", "life", "metacog", "unify", "OWNER_MAIN", "OWNER_DEFAULT"]
