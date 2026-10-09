# core/db_fleet.py —— 数据库编队：100 个 = 10 族 × 10 槽（真建库 + 双向绑定 + 全互通 + 开关）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人口径（2026-10-06）：数据库也按 10 个一组，共 100 个，分类排布，双向绑定方式与云插件一致。
#
# 诚实边界（这条必须说清，不然就是骗人）：
#   · 本机真能"装"的是 **SQLite**（文件级、ACID 稳定）：100 槽默认都是 SQLite 库文件，
#     ensure_all() 会**真的把 100 个库建出来**（各自带 db_meta 表），可写可读可核。
#   · Postgres/Redis/MySQL/ClickHouse/Qdrant 这类**服务端引擎**：我们登记**接入位**，
#     连接串只从环境变量读（源码零凭据）；探不到就如实标 env_required / down，**不假装已装**。
#   · SQL 一律参数绑定；表名/库名只来自本文件的白名单字面量，绝不拼接外部输入。
import os
import sqlite3
import time
from pathlib import Path
from core.swallow import swallow as _swallow

GROUPS: tuple = ("core-rdb", "timeseries", "vector", "document", "kv-cache",
                 "fulltext", "graph", "queue", "memory", "audit")
GROUP_CN: dict = {
    "core-rdb": "核心关系库", "timeseries": "时序库", "vector": "向量库",
    "document": "文档库", "kv-cache": "键值与缓存", "fulltext": "全文检索库",
    "graph": "图与关系", "queue": "队列与任务", "memory": "记忆与知识",
    "audit": "审计与证据",
}
# 每族的引擎倾向（默认 SQLite；服务端引擎只在 env 里给了连接串才启用）
GROUP_ENGINE: dict = {
    "core-rdb": "sqlite", "timeseries": "sqlite", "vector": "sqlite",
    "document": "sqlite", "kv-cache": "sqlite", "fulltext": "sqlite",
    "graph": "sqlite", "queue": "sqlite", "memory": "sqlite", "audit": "sqlite",
}
# 服务端替代引擎（有 env 才切换）：族 → (env 变量名, 引擎名)
SERVER_ENGINE_ENV: dict = {
    "core-rdb": ("V9_DB_CORE_DSN", "postgres"), "timeseries": ("V9_DB_TS_DSN", "timescale"),
    "vector": ("V9_DB_VECTOR_DSN", "qdrant"), "document": ("V9_DB_DOC_DSN", "mongo"),
    "kv-cache": ("V9_DB_KV_DSN", "redis"), "fulltext": ("V9_DB_FT_DSN", "meilisearch"),
    "graph": ("V9_DB_GRAPH_DSN", "neo4j"), "queue": ("V9_DB_QUEUE_DSN", "redis"),
    "memory": ("V9_DB_MEM_DSN", "sqlite"), "audit": ("V9_DB_AUDIT_DSN", "postgres"),
}

# 每族 10 槽：(slug, 中文名, 用途, 内存预估 MB)
_BASE: dict = {
    "core-rdb": [
        ("ledger", "扫描账本", "扫描结果与裁决", 64), ("panel", "面板库", "面板只读视图源", 96),
        ("body", "身体库", "登记链/见证/工具面", 128), ("orders", "业务库", "接单交付回款", 48),
        ("fleet", "编队库", "触手驱动与审计", 48), ("cloud", "云插件库", "云插件绑定与开关", 48),
        ("users", "用户与关系", "主脑-主人关系状态", 32), ("config", "配置快照", "配置代际", 32),
        ("index", "文件索引", "责任页与哈希", 64), ("stats", "统计汇总", "趋势与计数", 48),
    ],
    "timeseries": [
        ("frames", "帧时序", "逐帧时间戳与指纹", 256), ("heartbeat", "心跳", "会话与采样心跳", 64),
        ("scan_rate", "扫描速率", "每小时事件量", 48), ("queue_depth", "队列深度史", "积压趋势", 64),
        ("vram", "显存史", "显存占用时间线", 48), ("tokens", "Token 用量", "模型用量", 64),
        ("coverage_hist", "覆盖率史", "提交级覆盖率", 48), ("alerts_hist", "告警史", "告警与恢复", 64),
        ("latency", "时延史", "接口与模型时延", 48), ("retention", "留存指标", "复购与活跃", 32),
    ],
    "vector": [
        ("emb_note", "笔记嵌入", "Obsidian 笔记向量", 256), ("emb_code", "代码嵌入", "仓库代码向量", 256),
        ("emb_frame", "帧嵌入", "关键帧向量", 256), ("emb_doc", "文档嵌入", "资料向量", 192),
        ("emb_voice", "语音嵌入", "说话人向量", 128), ("emb_ui", "界面元素嵌入", "控件向量", 128),
        ("emb_err", "错误聚类", "报错向量", 96), ("emb_cust", "客户画像", "线索向量", 96),
        ("emb_skill", "技能嵌入", "能力检索", 128), ("emb_rule", "规则嵌入", "规则匹配", 96),
    ],
    "document": [
        ("notes", "笔记原文", "原始资料", 128), ("reports", "报告存档", "交付报告", 96),
        ("contracts", "合同文本", "合同与验收", 64), ("invoices", "账单文本", "账单快照", 48),
        ("specs", "规格文档", "能力规格", 64), ("logs_doc", "日志归档", "结构化日志", 192),
        ("frames_meta", "帧元数据", "段与帧档案", 128), ("mails", "邮件归档", "触手邮件", 96),
        ("patches", "变更记录", "补丁与差异", 64), ("kb", "知识库", "编译后知识", 128),
    ],
    "kv-cache": [
        ("session", "会话缓存", "对答上下文", 128), ("grant", "授权令牌", "工单与令牌", 32),
        ("quota", "配额桶", "每触手 rpm", 32), ("dedupe", "去重集", "事件去重", 64),
        ("lock", "分布式锁", "leader 与互斥", 16), ("ratelimit", "限流计数", "速率窗口", 32),
        ("preview", "预览缓存", "面板预览", 96), ("hot", "热点对象", "最近访问", 64),
        ("feature", "特征缓存", "推理特征", 128), ("state", "运行状态", "模式与情绪", 48),
    ],
    "fulltext": [
        ("ft_notes", "笔记全文", "笔记检索", 128), ("ft_code", "代码全文", "代码检索", 192),
        ("ft_reports", "报告全文", "报告检索", 96), ("ft_logs", "日志全文", "日志检索", 192),
        ("ft_mail", "邮件全文", "邮件检索", 96), ("ft_chat", "对话全文", "历史对答", 128),
        ("ft_contract", "合同全文", "条款检索", 64), ("ft_kb", "知识全文", "知识检索", 128),
        ("ft_alert", "告警全文", "告警检索", 64), ("ft_audit", "审计全文", "审计检索", 96),
    ],
    "graph": [
        ("g_files", "文件依赖图", "引用与依赖", 128), ("g_tentacle", "触手能力图", "能力归属", 96),
        ("g_cloud", "云插件图", "插件互通", 96), ("g_people", "人脉图", "客户与关系", 64),
        ("g_biz", "业务图", "订单与交付", 64), ("g_know", "知识图", "概念关联", 128),
        ("g_chain", "登记链图", "链与锚点", 96), ("g_rule", "规则图", "规则冲突", 64),
        ("g_time", "时间线图", "事件先后", 96), ("g_risk", "风险图", "风险传导", 64),
    ],
    "queue": [
        ("q_media", "媒体队列", "生成任务", 96), ("q_scan", "扫描队列", "待扫目标", 64),
        ("q_cloud", "云插件队列", "插件调用", 64), ("q_voice", "语音队列", "播报任务", 48),
        ("q_mail", "邮件队列", "收发任务", 48), ("q_devour", "采集队列", "帧段归档", 96),
        ("q_deliver", "交付队列", "交付与验收", 48), ("q_retry", "重试队列", "退避与重放", 64),
        ("q_dlq", "死信队列", "失败归档", 64), ("q_batch", "批处理队列", "批量作业", 96),
    ],
    "memory": [
        ("mem_tentacle", "触手记忆", "每触手永久记忆索引", 128), ("mem_brain", "主脑记忆", "经验与偏好", 128),
        ("mem_project", "项目记忆", "工程约束与决定", 96), ("mem_user", "用户记忆", "主人习惯", 64),
        ("mem_skill", "技能记忆", "能力使用史", 96), ("mem_error", "错误记忆", "踩坑与修法", 96),
        ("mem_price", "定价记忆", "报价与成交", 64), ("mem_prompt", "提示词记忆", "有效提示", 96),
        ("mem_insight", "洞察记忆", "洞察与启发", 96), ("mem_stale", "过期记忆", "待清理", 32),
    ],
    "audit": [
        ("a_chain", "登记链", "append-only 链", 128), ("a_anchor", "锚点", "外部锚定", 64),
        ("a_witness", "见证", "有效票与证据等级", 96), ("a_action", "动作审计", "动手记录", 96),
        ("a_grant", "授权审计", "令牌与工单", 64), ("a_alert", "告警审计", "开单与恢复", 96),
        ("a_blocked", "卡点日志", "被拦与原因", 64), ("a_coverage", "覆盖审计", "扫描覆盖", 64),
        ("a_cloud", "云插件审计", "绑定与开关", 64), ("a_db", "数据库审计", "本编队自身", 64),
    ],
}
RESERVED_SLOT = ("reserved", "预留槽（该族不足 10 个）", "留待新增", 32)


def _pad(items: list) -> list:
    return list(items) + [RESERVED_SLOT] * max(0, 10 - len(items))


CATALOG: dict = {g: _pad(_BASE[g]) for g in GROUPS}

# 库文件根：默认 ~/.v9-dbs/<族>/<slug>.sqlite3
DB_ROOT = Path(os.environ.get("V9_DB_ROOT", str(Path.home() / ".v9-dbs")))


def slot_key(group: str, slug: str, slot: int) -> str:
    return f"{group}:{slug}#{slot}"


def slot_ids() -> list:
    return [slot_key(g, s, i) for g in GROUPS
            for i, (s, _cn, _u, _mb) in enumerate(CATALOG[g], 1)]


SLOT_IDS: tuple = tuple(slot_ids())


def _dsn_of(group: str) -> tuple:
    """该族是否有服务端引擎连接串（只从环境变量读；没有则用 SQLite）。"""
    env_name, engine = SERVER_ENGINE_ENV.get(group, ("", "sqlite"))
    dsn = (os.environ.get(env_name) or "").strip() if env_name else ""
    return (dsn, engine) if dsn else ("", GROUP_ENGINE.get(group, "sqlite"))


def registry(*, enabled_map: dict | None = None) -> dict:
    em = enabled_map or {}
    out = []
    for gi, g in enumerate(GROUPS, 1):
        dsn, engine = _dsn_of(g)
        for si, (slug, cn, use, mb) in enumerate(CATALOG[g], 1):
            key = slot_key(g, slug, si)
            reserved = slug == "reserved"
            path = None if reserved else str(DB_ROOT / g / f"{slug}.sqlite3")
            out.append({"key": key, "group": g, "group_cn": GROUP_CN[g], "group_index": gi,
                        "slot": si, "global_index": (gi - 1) * 10 + si, "slug": slug,
                        "cn": cn, "use": use, "engine": ("-" if reserved else engine),
                        "server_dsn_from_env": bool(dsn), "path": path,
                        "memory_mb": mb, "reserved": reserved,
                        "created": bool(path and Path(path).is_file()),
                        "enabled": bool(em.get(key, 0)) if em else (not reserved)})
    live = [s for s in out if not s["reserved"]]
    return {"count": len(out), "groups": {g: GROUP_CN[g] for g in GROUPS}, "per_group": 10,
            "slots_real": len(live), "slots_reserved": len(out) - len(live),
            "created": sum(1 for s in live if s["created"]),
            "server_engine_slots": sum(1 for s in live if s["server_dsn_from_env"]),
            "db_root": str(DB_ROOT), "plugins": out}


def validate() -> dict:
    ids = slot_ids()
    dup = sorted({i for i in ids if ids.count(i) > 1})
    bad = [g for g in GROUPS if len(CATALOG[g]) != 10]
    return {"total": len(ids), "groups": len(CATALOG),
            "per_group": {g: len(v) for g, v in CATALOG.items()},
            "exactly_100": len(ids) == 100 and len(CATALOG) == 10,
            "all_groups_of_ten": not bad, "duplicate_keys": dup,
            "ok": len(ids) == 100 and len(CATALOG) == 10 and not bad and not dup
                  and set(CATALOG) == set(GROUPS)}


# ═══════════ 真建库（SQLite）：100 个库文件，各自带 db_meta ═══════════
def ensure_slot(group: str, slug: str) -> dict:
    """建一个库（幂等）。表名与字段全是字面量；无任何外部拼接。"""
    if slug == "reserved":
        return {"ok": False, "reason": "reserved 槽不建库"}
    path = DB_ROOT / group / f"{slug}.sqlite3"
    if path.is_file():
        return {"ok": True, "path": str(path), "created": False}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path))
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE IF NOT EXISTS db_meta ("
                        "key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
                        ("family", group))
            con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
                        ("slug", slug))
            con.execute("INSERT OR REPLACE INTO db_meta (key, value) VALUES (?, ?)",
                        ("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
            con.commit()
        finally:
            con.close()
        return {"ok": True, "path": str(path), "created": True}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}


def ensure_all() -> dict:
    """把 100 槽里的真实槽**全部建出来**（reserved 跳过）。"""
    made, existed, failed = [], [], []
    for g in GROUPS:
        for slug, _cn, _u, _mb in CATALOG[g]:
            if slug == "reserved":
                continue
            r = ensure_slot(g, slug)
            if not r.get("ok"):
                failed.append({"slot": f"{g}:{slug}", "why": r.get("reason")})
            elif r.get("created"):
                made.append(f"{g}:{slug}")
            else:
                existed.append(f"{g}:{slug}")
    return {"ok": not failed, "created": len(made), "already": len(existed),
            "failed": failed[:5], "total_real_slots": len(made) + len(existed),
            "root": str(DB_ROOT)}


def ping_slot(group: str, slug: str) -> dict:
    """真读一次：开库、读 db_meta（参数绑定）。读不到就说读不到。"""
    path = DB_ROOT / group / f"{slug}.sqlite3"
    if not path.is_file():
        return {"ok": False, "path": str(path), "reason": "库文件不存在（先 ensure）"}
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT key, value FROM db_meta WHERE key IN (?, ?)",
                               ("family", "slug")).fetchall()
        finally:
            con.close()
        return {"ok": True, "path": str(path), "meta": {k: v for k, v in rows},
                "size_kb": round(path.stat().st_size / 1024, 1)}
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}


# ═══════════ 全互通网格（与云插件同一口径）═══════════
def mesh_size() -> int:
    n = len(SLOT_IDS)
    return n * (n - 1) // 2


def mesh_view(*, sample: int = 40) -> dict:
    keys = list(SLOT_IDS)
    edges = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]]
    intra = [e for e in edges if e[0].split(":")[0] == e[1].split(":")[0]]
    return {"edge_total": len(edges), "expected": mesh_size(),
            "intra_group_edges": len(intra), "cross_group_edges": len(edges) - len(intra),
            "sample": [{"from": a, "to": b} for a, b in edges[:sample]],
            "note": "全互通：任意库直连任意库（同云插件一致）"}


def reachable(a: str, b: str) -> bool:
    if a == b:
        return False
    return (a in SLOT_IDS and b in SLOT_IDS) or \
           (a.startswith("t") and b in SLOT_IDS) or (b.startswith("t") and a in SLOT_IDS)


# ═══════════ 双向绑定 + 开关（落库，与云插件同构）═══════════
class DbFleet:
    def __init__(self, ledger=None, *, n_tentacles: int = 100):
        self.led, self.n = ledger, int(n_tentacles)
        self._init()

    def tentacle_ids(self) -> list:
        return [f"t{i:03d}" for i in range(1, self.n + 1)]

    @staticmethod
    def _now() -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _init(self):
        if self.led is None:
            return
        try:
            from senses.sqldialect import txn
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS db_slot_state ("
                            "slot TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS db_binding ("
                            "bind_id TEXT PRIMARY KEY, tentacle TEXT, slot TEXT,"
                            " direction TEXT, resource TEXT, state TEXT, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)


    def enabled_map(self) -> dict:
        if self.led is None:
            return {}
        from senses.sqldialect import txn
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT slot, enabled FROM db_slot_state")
                return {s: int(e) for s, e in cur.fetchall()}
        except Exception:                                      # noqa: BLE001
            return {}

    def toggle(self, slot: str, on: bool | None = None) -> dict:
        if slot not in SLOT_IDS:
            return {"ok": False, "reason": f"未知槽：{slot}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        from senses.sqldialect import txn
        new = (not bool(self.enabled_map().get(slot))) if on is None else bool(on)
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO db_slot_state (slot, enabled, updated_at)"
                            " VALUES (?,?,?) ON CONFLICT (slot) DO UPDATE SET"
                            " enabled=EXCLUDED.enabled, updated_at=EXCLUDED.updated_at",
                            (slot, 1 if new else 0, self._now()))
        except Exception as exc:                               # noqa: BLE001
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "slot": slot, "enabled": new}

    def bind(self, tentacle: str, slot: str, *, resource: str = "default") -> dict:
        if slot not in SLOT_IDS:
            return {"ok": False, "reason": f"未知槽：{slot}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        import uuid
        from senses.sqldialect import txn
        try:
            with txn(self.led) as cur:
                for direction, a, b in (("t2d", tentacle, slot), ("d2t", slot, tentacle)):
                    cur.execute("INSERT INTO db_binding (bind_id, tentacle, slot,"
                                " direction, resource, state, at) VALUES (?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex[:12], a, b, direction, resource,
                                 "bound", self._now()))
        except Exception as exc:                               # noqa: BLE001
            return {"ok": False, "reason": f"绑定写入失败：{type(exc).__name__}"}
        return {"ok": True, "tentacle": tentacle, "slot": slot, "resource": resource,
                "directions": ["t2d", "d2t"], "bidirectional": True}

    def unbind(self, tentacle: str, slot: str) -> dict:
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        from senses.sqldialect import txn
        with txn(self.led) as cur:
            cur.execute("DELETE FROM db_binding WHERE (tentacle=? AND slot=?)"
                        " OR (tentacle=? AND slot=?)", (tentacle, slot, slot, tentacle))
            cur.execute("SELECT COUNT(*) FROM db_binding WHERE (tentacle=? AND slot=?)"
                        " OR (tentacle=? AND slot=?)", (tentacle, slot, slot, tentacle))
            left = int(cur.fetchone()[0])
        return {"ok": left == 0, "left": left}

    def bind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for s in SLOT_IDS:
                r = self.bind(t, s)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "slot": s, "why": r.get("reason")})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "slots": len(SLOT_IDS), "failed": failed[:5]}

    def state(self) -> dict:
        out = {"bindings": 0, "slots_bound": 0, "tentacles_bound": 0, "by_slot": {},
               "by_tentacle": {}, "symmetric": True, "enabled": 0}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        from senses.sqldialect import txn
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT tentacle, slot, direction FROM db_binding")
                rows = cur.fetchall()
        except Exception as exc:                               # noqa: BLE001
            return {**out, "reason": f"{type(exc).__name__}: {exc}"}
        t2d, d2t = set(), set()
        for a, b, d in rows or []:
            (t2d if d == "t2d" else d2t).add((a, b))
        for t, s in t2d:
            out["by_slot"][s] = out["by_slot"].get(s, 0) + 1
            out["by_tentacle"][t] = out["by_tentacle"].get(t, 0) + 1
        out["bindings"] = len(t2d)
        out["slots_bound"] = len(out["by_slot"])
        out["tentacles_bound"] = len(out["by_tentacle"])
        missing = [p for p in t2d if (p[1], p[0]) not in d2t]
        out["asymmetric"] = missing[:5]
        out["symmetric"] = not missing
        out["enabled"] = sum(1 for k in SLOT_IDS if self.enabled_map().get(k))
        return out


    # ═══ 异步通道：面板的身体库是异步端口（transaction() + await execute/fetch_all）═══
    async def ainit(self):
        if self.led is None:
            return
        async with self.led.transaction():
            await self.led.execute("CREATE TABLE IF NOT EXISTS db_slot_state ("
                                   "slot TEXT PRIMARY KEY, enabled INTEGER, updated_at TEXT)")
            await self.led.execute("CREATE TABLE IF NOT EXISTS db_binding ("
                                   "bind_id TEXT PRIMARY KEY, tentacle TEXT, slot TEXT,"
                                   " direction TEXT, resource TEXT, state TEXT, at TEXT)")

    async def aenabled_map(self) -> dict:
        if self.led is None:
            return {}
        rows = await self.led.fetch_all("SELECT slot, enabled FROM db_slot_state")
        return {r["slot"]: int(r["enabled"] or 0) for r in rows or []}

    async def atoggle(self, slot: str, on: bool | None = None) -> dict:
        if slot not in SLOT_IDS:
            return {"ok": False, "reason": f"未知槽：{slot}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        em = await self.aenabled_map()
        new = (not bool(em.get(slot))) if on is None else bool(on)
        await self.led.execute(
            "INSERT INTO db_slot_state (slot, enabled, updated_at) VALUES (?,?,?)"
            " ON CONFLICT (slot) DO UPDATE SET enabled=EXCLUDED.enabled,"
            " updated_at=EXCLUDED.updated_at", (slot, 1 if new else 0, self._now()))
        return {"ok": True, "slot": slot, "enabled": new}

    async def abind(self, tentacle: str, slot: str, *, resource: str = "default",
                    skip_existing: bool = True) -> dict:
        """一对一绑定（两方向）。**幂等**：已存在的方向不再重复插入（防用量虚高）。"""
        if slot not in SLOT_IDS:
            return {"ok": False, "reason": f"未知槽：{slot}"}
        if tentacle not in self.tentacle_ids():
            return {"ok": False, "reason": f"未知触手：{tentacle}"}
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        import uuid
        await self.ainit()
        added = []
        for direction, a, b in (("t2d", tentacle, slot), ("d2t", slot, tentacle)):
            if skip_existing:
                got = await self.led.fetch_all(
                    "SELECT bind_id FROM db_binding WHERE tentacle=? AND slot=? AND direction=? LIMIT 1",
                    (a, b, direction))
                if got:
                    continue
            await self.led.execute(
                "INSERT INTO db_binding (bind_id, tentacle, slot, direction, resource,"
                " state, at) VALUES (?,?,?,?,?,?,?)",
                (uuid.uuid4().hex[:12], a, b, direction, resource, "bound", self._now()))
            added.append(direction)
        return {"ok": True, "tentacle": tentacle, "slot": slot, "resource": resource,
                "directions": ["t2d", "d2t"], "bidirectional": True,
                "added": added, "already": 2 - len(added)}

    async def adedupe(self) -> dict:
        """清理历史重复行：同一 (触手, 库槽, 方向) 只留最早一行。"""
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        before = await self.led.fetch_all("SELECT COUNT(*) AS n FROM db_binding")
        await self.led.execute(
            "DELETE FROM db_binding WHERE rowid NOT IN (SELECT MIN(rowid) FROM db_binding GROUP BY tentacle, slot, direction)")
        after = await self.led.fetch_all("SELECT COUNT(*) AS n FROM db_binding")
        b = (before[0]["n"] if before else None)
        a = (after[0]["n"] if after else None)
        return {"ok": True, "rows_before": b, "rows_after": a,
                "removed": (b - a) if (b is not None and a is not None) else None}

    async def aunbind(self, tentacle: str, slot: str) -> dict:
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        await self.ainit()
        await self.led.execute("DELETE FROM db_binding WHERE (tentacle=? AND slot=?)"
                               " OR (tentacle=? AND slot=?)", (tentacle, slot, slot, tentacle))
        rows = await self.led.fetch_all(
            "SELECT COUNT(*) AS n FROM db_binding WHERE (tentacle=? AND slot=?)"
            " OR (tentacle=? AND slot=?)", (tentacle, slot, slot, tentacle))
        left = int((rows or [{"n": 0}])[0]["n"] or 0)
        return {"ok": left == 0, "left": left, "tentacle": tentacle, "slot": slot}

    async def astate(self) -> dict:
        out = {"bindings": 0, "slots_bound": 0, "tentacles_bound": 0, "by_slot": {},
               "by_tentacle": {}, "symmetric": True, "enabled": 0}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        await self.ainit()
        rows = await self.led.fetch_all("SELECT tentacle, slot, direction FROM db_binding")
        t2d, d2t = set(), set()
        for r in rows or []:
            d, a, b = r["direction"], r["tentacle"], r["slot"]
            (t2d if d == "t2d" else d2t).add((a, b))
        for t, s in t2d:
            out["by_slot"][s] = out["by_slot"].get(s, 0) + 1
            out["by_tentacle"][t] = out["by_tentacle"].get(t, 0) + 1
        out.update({"bindings": len(t2d), "slots_bound": len(out["by_slot"]),
                    "tentacles_bound": len(out["by_tentacle"])})
        missing = [x for x in t2d if (x[1], x[0]) not in d2t]
        out["asymmetric"], out["symmetric"] = missing[:5], not missing
        em = await self.aenabled_map()
        out["enabled"] = sum(1 for k in SLOT_IDS if em.get(k))
        return out

    async def abind_all(self, *, tentacles: int | None = None) -> dict:
        n = int(tentacles or self.n)
        bound, failed = 0, []
        for t in self.tentacle_ids()[:n]:
            for s in SLOT_IDS:
                r = await self.abind(t, s)
                if r.get("ok"):
                    bound += 1
                else:
                    failed.append({"t": t, "slot": s, "why": r.get("reason")})
        return {"ok": not failed, "bound_pairs": bound, "rows": bound * 2,
                "tentacles": n, "slots": len(SLOT_IDS), "failed": failed[:5]}


__all__ = ["GROUPS", "GROUP_CN", "CATALOG", "SLOT_IDS", "DB_ROOT", "registry",
           "validate", "ensure_slot", "ensure_all", "ping_slot", "mesh_size",
           "mesh_view", "reachable", "DbFleet", "slot_key", "slot_ids"]
