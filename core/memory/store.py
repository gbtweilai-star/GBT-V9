# core/memory/store.py —— 主脑记忆的统一存储层（落在**我们自己的库槽**上）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 落点：db_fleet 的 memory 族槽 `mem_brain`（主脑记忆）→ ~/.v9-dbs/memory/mem_brain.sqlite3
#   —— 不新造存储，直接用编队里已规划好的那个库（面板"数据库编队"里看得见它）。
#
# 「统一」的口径：**主脑 + 100 根触手 + 用户 + 系统**的记忆都进同一张表，
#   靠 owner / owner_kind / category / scope 四个维度分门别类：
#     owner       谁的记忆（main / t001…t100 / user / system）
#     owner_kind  主体类型（主脑 / 触手 / 用户 / 系统）
#     category    记忆分类（事实 / 事件 / 待办 / 感受 / 做法 / 知识 / 里程碑 / 教训）
#     scope       记忆域（主脑记忆 / 触手记忆 / 项目 / 技能 / 错误 / 价格 / 提示词 / 洞见）
#   隔离与统一并不矛盾：读的时候按 owner 过滤（别人看不到别人的），
#   而主脑的 `inspect_all` 是**全量统一视图** —— 这就是"主脑记忆全部统一"。
#
# SQL 纪律（本文件一律如此）：每条语句都是写在 execute() 里的**完整字面量**，
#   取值一律走占位符绑定；不拼接、不把 SQL 存变量再执行、不动态拼 ORDER BY。
from core.swallow import swallow as _swallow
import hashlib
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

SLOT_GROUP = "memory"
SLOT_SLUG = "mem_brain"
OWNER_DEFAULT = "main"                # 主脑
OWNER_KIND = {"main": "主脑", "user": "用户", "system": "系统"}

CATEGORIES = ("事实", "事件", "待办", "感受", "做法", "知识", "里程碑", "教训")
SCOPES = ("主脑记忆", "触手记忆", "项目", "技能", "错误", "价格", "提示词", "洞见")

_CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]+")
_WORD = re.compile(r"[A-Za-z0-9_+#.]{2,}")
_STOP = ("的", "了", "是", "在", "我", "你", "他", "她", "它", "和", "与", "就",
         "都", "也", "很", "有", "没有", "这个", "那个", "一个", "什么", "怎么")


def owner_kind_of(owner: str) -> str:
    o = str(owner or OWNER_DEFAULT)
    if o in OWNER_KIND:
        return OWNER_KIND[o]
    if re.fullmatch(r"t\d{3}", o):
        return "触手"
    return "其他"


def tokens(text: str) -> list:
    """切词：ASCII 词整取；中文取**二元组** —— 不引第三方分词器也能真检索中文。"""
    t = str(text or "")
    out = [w.lower() for w in _WORD.findall(t)]
    for run in _CJK.findall(t):
        if len(run) == 1:
            out.append(run)
            continue
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if bg not in _STOP:
                out.append(bg)
    return out


def fts_query(text: str) -> str:
    """问句 → FTS5 查询：二元组之间 OR（任一片段命中都算候选）。"""
    ts = [x for x in dict.fromkeys(tokens(text)) if x]
    if not ts:
        return ""
    return " OR ".join('"' + x.replace('"', "") + '"' for x in ts[:24])


def _path() -> Path:
    try:
        from core import db_fleet
        db_fleet.ensure_slot(SLOT_GROUP, SLOT_SLUG)
        return Path(db_fleet.DB_ROOT) / SLOT_GROUP / f"{SLOT_SLUG}.sqlite3"
    except Exception:                                          # noqa: BLE001
        root = Path(__file__).resolve().parent.parent.parent
        p = root.joinpath("data", "brain_mem_brain.sqlite3")
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


class Store:
    def __init__(self, *, path: Path | None = None) -> None:
        self.path = Path(path) if path else _path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._c = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA journal_mode=WAL")
        self._c.execute("PRAGMA busy_timeout=5000")
        self.ensure()

    # ── 建表（幂等；每条 DDL 都是字面量） ──
    def ensure(self) -> None:
        with self._c:
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_memories("
                "id TEXT PRIMARY KEY, owner TEXT NOT NULL DEFAULT 'main',"
                "owner_kind TEXT DEFAULT '主脑', category TEXT DEFAULT '', scope TEXT DEFAULT '',"
                "origin TEXT DEFAULT 'capture', origin_ref TEXT DEFAULT '',"
                "raw TEXT NOT NULL, kind TEXT DEFAULT '', importance REAL DEFAULT 0.5,"
                "t_created REAL NOT NULL, t_event REAL, t_updated REAL,"
                "private INTEGER DEFAULT 0, heat REAL DEFAULT 1.0, tier TEXT DEFAULT 'hot',"
                "hits INTEGER DEFAULT 0, last_used REAL,"
                "encode_state TEXT DEFAULT 'pending', encode_note TEXT DEFAULT '',"
                "embed_state TEXT DEFAULT 'none', embed_model TEXT DEFAULT '', embed_vec BLOB,"
                "merged_into TEXT DEFAULT '', meta TEXT DEFAULT '{}')")
            self._c.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS brain_terms USING fts5("
                "memory_id UNINDEXED, text, tokenize='unicode61 remove_diacritics 2')")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_entities("
                "memory_id TEXT NOT NULL, name TEXT NOT NULL, etype TEXT DEFAULT 'thing',"
                "weight REAL DEFAULT 1.0, PRIMARY KEY(memory_id, name))")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_edges("
                "a TEXT NOT NULL, b TEXT NOT NULL, kind TEXT DEFAULT 'co',"
                "weight REAL DEFAULT 1.0, t REAL, PRIMARY KEY(a, b, kind))")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_merges("
                "merge_id TEXT PRIMARY KEY, ts REAL, owner TEXT, target_id TEXT,"
                "member_ids TEXT, kind TEXT DEFAULT 'moment', note TEXT DEFAULT '',"
                "undone INTEGER DEFAULT 0)")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_tombstones("
                "memory_id TEXT PRIMARY KEY, ts REAL, purge_after REAL, owner TEXT,"
                "reason TEXT DEFAULT '', restored INTEGER DEFAULT 0)")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_nudges("
                "nudge_id TEXT PRIMARY KEY, ts REAL, owner TEXT, kind TEXT, score REAL,"
                "text TEXT, memory_id TEXT DEFAULT '', state TEXT DEFAULT 'pending',"
                "acted_at REAL, why TEXT DEFAULT '')")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_dismissals("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, owner TEXT, kind TEXT)")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_events("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, owner TEXT,"
                "kind TEXT, detail TEXT DEFAULT '')")
            self._c.execute(
                "CREATE TABLE IF NOT EXISTS brain_life("
                "life_id TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,"
                "category TEXT DEFAULT '', title TEXT NOT NULL, detail TEXT DEFAULT '',"
                "owner TEXT DEFAULT 'main', ref TEXT DEFAULT '', importance REAL DEFAULT 0.5,"
                "meta TEXT DEFAULT '{}')")
            self._c.execute(
                "CREATE INDEX IF NOT EXISTS ix_mem_owner ON brain_memories(owner, tier)")
            self._c.execute(
                "CREATE INDEX IF NOT EXISTS ix_mem_scope ON brain_memories(scope, category)")
            self._c.execute("CREATE INDEX IF NOT EXISTS ix_life_ts ON brain_life(ts)")

    def event(self, kind: str, detail: str = "", *, owner: str = OWNER_DEFAULT) -> None:
        try:
            with self._c:
                self._c.execute(
                    "INSERT INTO brain_events(ts,owner,kind,detail) VALUES(?,?,?,?)",
                    (time.time(), owner, str(kind)[:40], str(detail)[:400]))
        except sqlite3.Error as e:
            _swallow(__file__, e)

    # ── 写：捕捉（原文立即落库；解析字段等编码器） ──
    def capture(self, raw: str, *, owner: str = OWNER_DEFAULT, private: bool = False,
                origin: str = "capture", origin_ref: str = "", scope: str = "",
                category: str = "", t_event: float | None = None,
                meta: dict | None = None) -> dict:
        text = str(raw or "").strip()
        if not text:
            return {"ok": False, "reason": "空内容不记"}
        now = time.time()
        mid = "m" + uuid.uuid4().hex[:14]
        # 记忆域默认跟着主体走：触手写的就是触手记忆，别一律记到主脑名下
        sc = str(scope).strip() or ("触手记忆" if str(owner).startswith("t") else "主脑记忆")
        with self._c:
            self._c.execute(
                "INSERT INTO brain_memories(id,owner,owner_kind,category,scope,origin,"
                "origin_ref,raw,kind,importance,t_created,t_event,t_updated,private,heat,tier,"
                "hits,last_used,encode_state,embed_state,merged_into,meta)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mid, owner, owner_kind_of(owner), str(category)[:24], sc[:24],
                 str(origin)[:24], str(origin_ref)[:120], text, "", 0.5, now, t_event or now,
                 now, 1 if private else 0, 1.0, "hot", 0, now, "pending", "none", "",
                 json.dumps(meta or {}, ensure_ascii=False)))
        self.index_terms(mid, text)          # 原文立刻可按措辞检索：写完就完了
        return {"ok": True, "id": mid, "t_created": now, "private": bool(private),
                "owner": owner, "owner_kind": owner_kind_of(owner),
                "scope": sc, "category": str(category or ""),
                "原文": text}

    def index_terms(self, memory_id: str, text: str) -> None:
        q = fts_query(text) or str(memory_id)
        with self._c:
            self._c.execute("DELETE FROM brain_terms WHERE memory_id=?", (memory_id,))
            self._c.execute("INSERT INTO brain_terms(memory_id,text) VALUES(?,?)",
                            (memory_id, q))

    def enrich(self, memory_id: str, *, kind: str, importance: float,
               entities: list | None = None, t_event: float | None = None,
               category: str = "", scope: str = "", embed_state: str = "none",
               embed_model: str = "", embed_vec: bytes | None = None, note: str = "") -> dict:
        """编码器写回判定（不改 raw；只补分类与重要度）。"""
        now = time.time()
        with self._c:
            self._c.execute(
                "UPDATE brain_memories SET kind=?,importance=?,t_event=COALESCE(?,t_event),"
                "t_updated=?,encode_state='done',encode_note=?,embed_state=?,embed_model=?,"
                "embed_vec=COALESCE(?,embed_vec),"
                "category=(CASE WHEN ?='' THEN category ELSE ? END),"
                "scope=(CASE WHEN ?='' THEN scope ELSE ? END) WHERE id=?",
                (str(kind)[:24], float(importance), t_event, now, str(note)[:200],
                 str(embed_state)[:20], str(embed_model)[:60], embed_vec,
                 str(category)[:24], str(category)[:24], str(scope)[:24], str(scope)[:24],
                 memory_id))
        names = []
        for e in entities or []:
            name, etype, w = ((tuple(e) + ("thing", 1.0))[:3] if isinstance(e, (tuple, list))
                              else (str(e), "thing", 1.0))
            nm = str(name).strip()[:60]
            if not nm:
                continue
            names.append(nm)
            with self._c:
                self._c.execute(
                    "INSERT OR REPLACE INTO brain_entities(memory_id,name,etype,weight)"
                    " VALUES(?,?,?,?)", (memory_id, nm, str(etype)[:24], float(w)))
        for i, a in enumerate(names):        # 同段记忆里的实体两两相连（联想就靠它）
            for b in names[i + 1:]:
                self.add_edge(a, b, "co", 0.5)
        return {"ok": True, "id": memory_id, "实体数": len(names)}

    def add_edge(self, a: str, b: str, kind: str = "co", weight: float = 0.5) -> None:
        if not a or not b or a == b:
            return
        lo, hi = sorted([str(a)[:60], str(b)[:60]])
        with self._c:
            self._c.execute("INSERT OR REPLACE INTO brain_edges(a,b,kind,weight,t)"
                            " VALUES(?,?,?,?,?)",
                            (lo, hi, str(kind)[:16], float(weight), time.time()))

    # ── 读 ──
    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        d["private"] = bool(d.get("private"))
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        d.pop("embed_vec", None)
        return d

    def get(self, memory_id: str, *, owner: str = OWNER_DEFAULT,
            include_merged: bool = False) -> dict | None:
        r = self._c.execute(
            "SELECT * FROM brain_memories WHERE id=? AND owner=?"
            " AND (merged_into='' OR ?=1)",
            (memory_id, owner, 1 if include_merged else 0)).fetchone()
        return self._row(r) if r else None

    def row_of(self, memory_id: str) -> dict | None:
        """按 id 直取（不问 owner）—— 召回融合时用；合并过的行也返回（调用方自行判断）。"""
        r = self._c.execute("SELECT * FROM brain_memories WHERE id=?",
                            (str(memory_id),)).fetchone()
        return self._row(r) if r else None

    def list(self, *, owner: str = OWNER_DEFAULT, tier: str = "", category: str = "",
             scope: str = "", all_owners: bool = False, limit: int = 50,
             include_merged: bool = False) -> list:
        """一条完整字面量 SQL，所有筛选走绑定（空串＝不筛；all_owners=1 给主脑全量视图）。"""
        rows = self._c.execute(
            "SELECT * FROM brain_memories"
            " WHERE (owner=? OR ?=1)"
            " AND (merged_into='' OR ?=1)"
            " AND (tier=? OR ?='') AND (category=? OR ?='') AND (scope=? OR ?='')"
            " ORDER BY heat DESC, t_created DESC",
            (owner, 1 if all_owners else 0, 1 if include_merged else 0,
             str(tier or ""), str(tier or ""), str(category or ""), str(category or ""),
             str(scope or ""), str(scope or ""))).fetchall()
        return [self._row(r) for r in rows[: max(1, int(limit))]]

    def by_terms(self, query: str, *, owner: str = OWNER_DEFAULT, all_owners: bool = False,
                 limit: int = 20) -> list:
        """按措辞找：FTS5 + bm25（越小越相关）→ 归一成分数。"""
        q = fts_query(query)
        if not q:
            return []
        try:
            rows = self._c.execute(
                "SELECT t.memory_id AS mid, bm25(brain_terms) AS score, m.owner AS owner"
                " FROM brain_terms t JOIN brain_memories m ON m.id=t.memory_id"
                " WHERE brain_terms MATCH ? AND (m.owner=? OR ?=1) AND m.merged_into=''"
                " ORDER BY score",
                (q, owner, 1 if all_owners else 0)).fetchall()
        except sqlite3.Error:
            return []
        out = []
        for r in rows[: max(1, int(limit))]:
            bm = abs(float(r["score"] or 0.0))
            out.append({"id": r["mid"], "owner": r["owner"],
                        "bm25": round(float(r["score"] or 0.0), 4),
                        "分数": round(1.0 / (1.0 + bm), 4)})
        return out

    def in_window(self, t_from: float, t_to: float, *, owner: str = OWNER_DEFAULT,
                  all_owners: bool = False, limit: int = 20) -> list:
        rows = self._c.execute(
            "SELECT * FROM brain_memories WHERE (owner=? OR ?=1) AND merged_into=''"
            " AND t_event>=? AND t_event<=? ORDER BY t_event DESC",
            (owner, 1 if all_owners else 0, float(t_from), float(t_to))).fetchall()
        return [self._row(r) for r in rows[: max(1, int(limit))]]

    def entities_of(self, memory_id: str) -> list:
        return [dict(r) for r in self._c.execute(
            "SELECT name,etype,weight FROM brain_entities WHERE memory_id=?",
            (memory_id,)).fetchall()]

    def memories_of_entity(self, name: str, *, owner: str = OWNER_DEFAULT,
                           all_owners: bool = False, limit: int = 20) -> list:
        rows = self._c.execute(
            "SELECT m.* FROM brain_entities e JOIN brain_memories m ON m.id=e.memory_id"
            " WHERE e.name=? AND (m.owner=? OR ?=1) AND m.merged_into=''"
            " ORDER BY m.heat DESC",
            (str(name), owner, 1 if all_owners else 0)).fetchall()
        return [self._row(r) for r in rows[: max(1, int(limit))]]

    def neighbours(self, name: str, *, hops: int = 2, limit: int = 24) -> list:
        """"它会来找我"：从实体出发沿共现边走 N 跳，找到间接相关的实体。"""
        seen, frontier, out = {str(name)}, {str(name)}, []
        for _h in range(max(1, int(hops))):
            nxt = set()
            for nm in frontier:
                for r in self._c.execute(
                        "SELECT a,b FROM brain_edges WHERE a=? OR b=? ORDER BY weight DESC",
                        (nm, nm)).fetchall():
                    other = r["b"] if r["a"] == nm else r["a"]
                    if other not in seen:
                        nxt.add(other)
                        out.append(other)
            frontier = nxt
            if not frontier or len(out) >= limit:
                break
        return out[: max(1, int(limit))]

    def touch(self, memory_id: str, *, owner: str = OWNER_DEFAULT) -> dict:
        """被用到：命中 +1、回暖（热度的唯一来源）。"""
        from core.memory import heat as H
        m = self.get(memory_id, owner=owner)
        if m is None:
            return {"ok": False, "reason": "记忆不存在或已被合并"}
        up = H.used(m)
        with self._c:
            self._c.execute("UPDATE brain_memories SET hits=?,last_used=?,heat=?,tier=?"
                            " WHERE id=?",
                            (up["hits"], up["last_used"], up["heat"], up["tier"], memory_id))
        return {"ok": True, "id": memory_id, **up}

    def recompute_heat(self) -> dict:
        """全量重算热度/分层。只改 heat/tier，**不动 last_used/hits、不动原文**。

        注意走 H.recompute（而不是 H.used）：used 会把 last_used 刷成"现在"，
        那样放着不用热度也永远回满，分层等于没开（真踩过）。
        """
        from core.memory import heat as H
        rows = self.list(all_owners=True, limit=100000)
        moved = 0
        for m in rows:
            up = H.recompute(m)
            if abs(float(up["heat"]) - float(m.get("heat") or 0)) > 1e-6 or \
                    up["tier"] != (m.get("tier") or ""):
                moved += 1
                with self._c:
                    self._c.execute("UPDATE brain_memories SET heat=?,tier=? WHERE id=?",
                                    (up["heat"], up["tier"], m["id"]))
        return {"重算": len(rows), "层有变动": moved}

    # ── 合并 / 回收（都可逆） ──
    def merge(self, target_id: str, member_ids: list, *, kind: str = "moment",
              note: str = "", owner: str = OWNER_DEFAULT) -> dict:
        mem = [str(x) for x in (member_ids or []) if str(x) and str(x) != target_id]
        if not mem:
            return {"ok": False, "reason": "没有可并入的成员"}
        mid = "mg" + uuid.uuid4().hex[:12]
        with self._c:
            for m in mem:
                self._c.execute(
                    "UPDATE brain_memories SET merged_into=? WHERE id=? AND owner=?",
                    (target_id, m, owner))
            self._c.execute(
                "INSERT INTO brain_merges(merge_id,ts,owner,target_id,member_ids,kind,note,"
                "undone) VALUES(?,?,?,?,?,?,?,0)",
                (mid, time.time(), owner, target_id, json.dumps(mem), str(kind)[:16],
                 str(note)[:200]))
        self.event("merge", f"{mid} → 并入 {len(mem)} 条", owner=owner)
        return {"ok": True, "merge_id": mid, "并入": len(mem), "target": target_id}

    def restore(self, merge_id: str, *, owner: str = OWNER_DEFAULT) -> dict:
        r = self._c.execute("SELECT * FROM brain_merges WHERE merge_id=? AND owner=?",
                            (merge_id, owner)).fetchone()
        if r is None:
            return {"ok": False, "reason": "没有这次合并记录"}
        if int(r["undone"] or 0):
            return {"ok": False, "reason": "这次合并已经撤过了"}
        mem = json.loads(r["member_ids"] or "[]")
        with self._c:
            for m in mem:
                self._c.execute("UPDATE brain_memories SET merged_into='' WHERE id=?", (m,))
            self._c.execute("UPDATE brain_merges SET undone=1 WHERE merge_id=?", (merge_id,))
        self.event("merge_undo", f"{merge_id} 撤销，恢复 {len(mem)} 条", owner=owner)
        return {"ok": True, "merge_id": merge_id, "恢复": len(mem)}

    def merges(self, *, owner: str = OWNER_DEFAULT, limit: int = 20) -> list:
        rows = self._c.execute("SELECT * FROM brain_merges WHERE owner=? ORDER BY ts DESC",
                               (owner,)).fetchall()
        return [{**dict(r), "member_ids": json.loads(r["member_ids"] or "[]")}
                for r in rows[: max(1, int(limit))]]

    def tombstone(self, memory_id: str, *, owner: str = OWNER_DEFAULT,
                  grace_days: float = 7.0, reason: str = "") -> dict:
        """删除 = 进宽限期回收站（不立即粉碎）；期间随时可复原。"""
        m = self.get(memory_id, owner=owner)
        if m is None:
            return {"ok": False, "reason": "记忆不存在"}
        now = time.time()
        with self._c:
            self._c.execute(
                "INSERT OR REPLACE INTO brain_tombstones"
                "(memory_id,ts,purge_after,owner,reason,restored) VALUES(?,?,?,?,?,0)",
                (memory_id, now, now + float(grace_days) * 86400.0, owner, str(reason)[:200]))
            self._c.execute("UPDATE brain_memories SET tier='recycled' WHERE id=?",
                            (memory_id,))
        self.event("recycle", f"{memory_id} 进回收站（宽限 {grace_days:g} 天）", owner=owner)
        return {"ok": True, "id": memory_id, "宽限到": now + float(grace_days) * 86400.0}

    def untombstone(self, memory_id: str, *, owner: str = OWNER_DEFAULT) -> dict:
        r = self._c.execute("SELECT * FROM brain_tombstones WHERE memory_id=? AND owner=?",
                            (memory_id, owner)).fetchone()
        if r is None:
            return {"ok": False, "reason": "不在回收站里"}
        with self._c:
            self._c.execute("UPDATE brain_tombstones SET restored=1 WHERE memory_id=?",
                            (memory_id,))
            self._c.execute("UPDATE brain_memories SET tier='cold' WHERE id=?", (memory_id,))
        self.event("recycle_undo", f"{memory_id} 已复原", owner=owner)
        return {"ok": True, "id": memory_id}

    def recycle_bin(self, *, owner: str = OWNER_DEFAULT, limit: int = 50) -> list:
        rows = self._c.execute(
            "SELECT t.memory_id, t.ts, t.purge_after, t.reason, t.restored, m.raw, m.kind"
            " FROM brain_tombstones t JOIN brain_memories m ON m.id=t.memory_id"
            " WHERE t.owner=? AND t.restored=0 ORDER BY t.ts DESC", (owner,)).fetchall()
        return [dict(r) for r in rows[: max(1, int(limit))]]

    def purge_due(self, *, owner: str = OWNER_DEFAULT) -> dict:
        """宽限到期才真清（只有这一步会删原文）。"""
        rows = self._c.execute(
            "SELECT memory_id FROM brain_tombstones WHERE owner=? AND restored=0"
            " AND purge_after<=?", (owner, time.time())).fetchall()
        ids = [r["memory_id"] for r in rows]
        for mid in ids:
            with self._c:
                self._c.execute("DELETE FROM brain_terms WHERE memory_id=?", (mid,))
                self._c.execute("DELETE FROM brain_entities WHERE memory_id=?", (mid,))
                self._c.execute("DELETE FROM brain_memories WHERE id=?", (mid,))
                self._c.execute("DELETE FROM brain_tombstones WHERE memory_id=?", (mid,))
        if ids:
            self.event("purge", f"宽限到期清理 {len(ids)} 条", owner=owner)
        return {"清理": len(ids), "ids": ids}

    # ── 生命起源存档（主脑生平；只增不改） ──
    def life_add(self, kind: str, title: str, *, detail: str = "", category: str = "",
                 owner: str = OWNER_DEFAULT, ref: str = "", importance: float = 0.5,
                 ts: float | None = None, meta: dict | None = None,
                 life_id: str | None = None) -> dict:
        lid = life_id or ("L" + uuid.uuid4().hex[:12])
        with self._c:
            self._c.execute(
                "INSERT OR REPLACE INTO brain_life(life_id,ts,kind,category,title,detail,"
                "owner,ref,importance,meta) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (lid, float(ts if ts is not None else time.time()), str(kind)[:32],
                 str(category)[:24], str(title)[:200], str(detail)[:1000], owner,
                 str(ref)[:200], float(importance),
                 json.dumps(meta or {}, ensure_ascii=False)))
        return {"ok": True, "life_id": lid}

    def life_list(self, *, kind: str = "", category: str = "", limit: int = 100,
                  newest_first: bool = True) -> list:
        # 两条**各自完整**的字面量语句（不拼 ORDER BY）
        if newest_first:
            rows = self._c.execute(
                "SELECT * FROM brain_life WHERE (kind=? OR ?='') AND (category=? OR ?='')"
                " ORDER BY ts DESC",
                (str(kind or ""), str(kind or ""), str(category or ""),
                 str(category or ""))).fetchall()
        else:
            rows = self._c.execute(
                "SELECT * FROM brain_life WHERE (kind=? OR ?='') AND (category=? OR ?='')"
                " ORDER BY ts ASC",
                (str(kind or ""), str(kind or ""), str(category or ""),
                 str(category or ""))).fetchall()
        out = []
        for r in rows[: max(1, int(limit))]:
            d = dict(r)
            try:
                d["meta"] = json.loads(d.get("meta") or "{}")
            except json.JSONDecodeError:
                d["meta"] = {}
            out.append(d)
        return out

    def genesis(self) -> dict | None:
        r = self._c.execute(
            "SELECT * FROM brain_life WHERE kind='genesis' ORDER BY ts ASC").fetchone()
        if r is None:
            return None
        d = dict(r)
        try:
            d["meta"] = json.loads(d.get("meta") or "{}")
        except json.JSONDecodeError:
            d["meta"] = {}
        return d

    # ── 与既有 MemoryStore 同形：core/brain.py 的 Brain 直接就能用我们这个库 ──
    def remember(self, tid: str, key: str, value) -> None:
        self.capture(f"{key}: {value}", owner=str(tid or OWNER_DEFAULT), origin="remember",
                     scope="触手记忆" if str(tid).startswith("t") else "主脑记忆")

    def recall(self, tid: str, key: str | None = None) -> dict:
        rows = self.list(owner=str(tid or OWNER_DEFAULT), limit=200)
        if key is None:
            return {r["id"]: {"value": r["raw"], "ts": r["t_created"]} for r in rows}
        for r in rows:
            if r["raw"].startswith(str(key) + ":"):
                return {"value": r["raw"].split(":", 1)[1].strip(), "ts": r["t_created"]}
        return None

    def inspect_all(self) -> dict:
        """⚠ 仅主脑可调：**统一视图**（跨主体全量读）。别人的记忆彼此看不到，主脑看得到全部。"""
        os_ = self.owners() or [OWNER_DEFAULT]
        return {o: {r["id"]: {"value": r["raw"], "ts": r["t_created"],
                              "category": r.get("category"), "scope": r.get("scope")}
                    for r in self.list(owner=o, limit=1000)} for o in os_}

    # ── 统计（每条 SQL 都是完整字面量） ──
    def counts(self, *, owner: str = OWNER_DEFAULT, all_owners: bool = False) -> dict:
        ov = (owner, 1 if all_owners else 0)
        return {
            "记忆": self._c.execute(
                "SELECT COUNT(*) FROM brain_memories WHERE (owner=? OR ?=1)"
                " AND merged_into=''", ov).fetchone()[0],
            "被并入的": self._c.execute(
                "SELECT COUNT(*) FROM brain_memories WHERE (owner=? OR ?=1)"
                " AND merged_into<>''", ov).fetchone()[0],
            "实体": self._c.execute(
                "SELECT COUNT(DISTINCT name) FROM brain_entities").fetchone()[0],
            "边": self._c.execute("SELECT COUNT(*) FROM brain_edges").fetchone()[0],
            "合并次数": self._c.execute(
                "SELECT COUNT(*) FROM brain_merges WHERE (owner=? OR ?=1)",
                ov).fetchone()[0],
            "待编码": self._c.execute(
                "SELECT COUNT(*) FROM brain_memories WHERE (owner=? OR ?=1)"
                " AND encode_state='pending'", ov).fetchone()[0],
            "回收站": self._c.execute(
                "SELECT COUNT(*) FROM brain_tombstones WHERE (owner=? OR ?=1)"
                " AND restored=0", ov).fetchone()[0],
            "生平条目": self._c.execute(
                "SELECT COUNT(*) FROM brain_life").fetchone()[0],
        }

    def owners(self) -> list:
        return [r["owner"] for r in self._c.execute(
            "SELECT DISTINCT owner FROM brain_memories ORDER BY owner").fetchall()]

    def by_owner_kind(self) -> dict:
        out: dict = {}
        for r in self._c.execute(
                "SELECT owner_kind, COUNT(*) AS n FROM brain_memories"
                " WHERE merged_into='' GROUP BY owner_kind").fetchall():
            out[r["owner_kind"]] = r["n"]
        return out

    def by_scope(self) -> dict:
        out: dict = {}
        for r in self._c.execute(
                "SELECT scope, COUNT(*) AS n FROM brain_memories"
                " WHERE merged_into='' GROUP BY scope").fetchall():
            out[r["scope"] or "未分类"] = r["n"]
        return out

    def by_category(self) -> dict:
        out: dict = {}
        for r in self._c.execute(
                "SELECT category, COUNT(*) AS n FROM brain_memories"
                " WHERE merged_into='' GROUP BY category").fetchall():
            out[r["category"] or "未分类"] = r["n"]
        return out

    def by_tier(self) -> dict:
        out: dict = {}
        for r in self._c.execute(
                "SELECT tier, COUNT(*) AS n FROM brain_memories"
                " WHERE merged_into='' GROUP BY tier").fetchall():
            out[r["tier"]] = r["n"]
        return out

    def by_embed(self) -> dict:
        """向量通道分布：多少条走了云 embedding、多少条只能本地哈希（非语义）。

        这条要**看得见**：路由说"云主管道"不等于真的调通了 —— 网关没 /embeddings
        路由时就会落回本地哈希，如实计数比藏在日志里强。
        """
        out: dict = {}
        for r in self._c.execute(
                "SELECT embed_state, COUNT(*) AS n FROM brain_memories"
                " WHERE merged_into='' AND encode_state='done' GROUP BY embed_state").fetchall():
            out[r["embed_state"] or "未编码"] = r["n"]
        return out


_STORE: Store | None = None


def store() -> Store:
    global _STORE
    if _STORE is None:
        _STORE = Store()
    return _STORE


def digest(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:16]


__all__ = ["Store", "store", "tokens", "fts_query", "digest", "owner_kind_of",
           "SLOT_GROUP", "SLOT_SLUG", "OWNER_DEFAULT", "CATEGORIES", "SCOPES"]
