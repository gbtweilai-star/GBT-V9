# tools/trace_chains.py —— 链路追踪视图（原 main.py 的追踪模块，保留不动）
# dev: 自由的风 · 本署名不可删除、不可篡改归属

# panel/pipelines.py —— 流水线统一视图：多源归一到步骤链（防御式）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计: gap 是一等 failed 步骤, 链不会全绿; 缺表/缺列 → source 标 unavailable,
#       绝不返回假空列表; 表名全部来自下方白名单, 绝不拼请求参数。
import time, json, os
from panel.trace import (DEVOUR_SEG_CANDIDATES, DEVOUR_GAP_CANDIDATES, detect)

# ══ 数据源白名单配置 ══
#   shape: square|pill|diamond|double  (前端节点形状)
SOURCES = [
    {"key": "scan",   "label": "扫描",     "shape": "square",  "color": "#39d0ff",
     "tables": ("ledger",),              "time": "ts",
     "ref": ("target",),                 "map": "map_scan"},
    {"key": "cross",  "label": "交叉互扫", "shape": "double",  "color": "#9b7bff",
     "tables": ("cross_tasks",),         "time": "updated_at",
     "ref": ("target",),                 "map": "map_cross"},
    {"key": "arbitration", "label": "仲裁", "shape": "double", "color": "#9b7bff",
     "tables": ("cross_arbitration",),   "time": "ts",
     "ref": ("target",),                 "map": "map_arb"},
    {"key": "devour", "label": "吞噬帧段", "shape": "pill",    "color": "#35d39a",
     "tables": DEVOUR_SEG_CANDIDATES,    "time": "ts",
     "ref": ("seg_id", "segment_id", "id"), "map": "map_seg"},
    {"key": "gap",    "label": "丢帧",     "shape": "diamond", "color": "#ff5d73",
     "tables": DEVOUR_GAP_CANDIDATES,    "time": "ts",
     "ref": ("gap_id", "id"),            "map": "map_gap"},
    {"key": "brain",  "label": "主脑卡点",  "shape": "square",  "color": "#ffbd59",
     "tables": ("skill_calls",),         "time": "ts",
     "ref": ("task_id",),                "map": "map_brain"},
]


# ══ 列自省工具 ══
def _cols(led, table):
    d = led.dialect
    with led._tx() as c, c.cursor() as cur:
        if d == "sqlite":
            cur.execute(f"PRAGMA table_info({table})")
            return [r[1] for r in cur.fetchall()]
        cur.execute("SELECT column_name FROM information_schema.columns "
                    "WHERE table_name=%s", (table,))
        return [r[0] for r in cur.fetchall()]


def _pick(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def _q(led, sql, args=()):
    ph = "?" if led.dialect == "sqlite" else "%s"
    sql = sql.replace("{}", ph)
    with led._tx() as c, c.cursor() as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ══ 数据源可用性探测（启动时一次，带缓存）══
_STATUS = {}
def source_status(led):
    if _STATUS.get("led") is led:
        return _STATUS["data"]
    out = {}
    for s in SOURCES:
        t = detect(led, s["tables"])
        if not t:
            out[s["key"]] = {"available": False, "table": None,
                             "reason": "表不存在或未启用"}
            continue
        cols = _cols(led, t)
        if "trace_id" not in cols:
            out[s["key"]] = {"available": False, "table": t,
                             "reason": "缺少 trace_id 列（未迁移）"}
            continue
        out[s["key"]] = {"available": True, "table": t, "reason": ""}
    _STATUS["led"], _STATUS["data"] = led, out
    return out


# ══ 各源 → 统一步骤 ══
def _step(source, ts, label, status, detail, ref_id, trace_id,
          association="exact"):
    return {"trace_id": trace_id, "source": source, "ts": ts, "label": label,
            "status": status, "detail": detail, "ref_id": ref_id,
            "association": association}


def map_scan(r):
    st = (r.get("status") or "scanned")
    status = {"vuln": "failed", "blocked": "warning"}.get(st, "ok")
    return _step("scan", r.get("ts"), f"扫描 {r.get('target')}", status,
                 {"target": r.get("target"), "tentacle": r.get("tentacle"),
                  "note": r.get("note")}, r.get("target"), r.get("trace_id"))


def map_cross(r):
    st = (r.get("status") or "").lower()
    status = {"done": "ok", "ok": "ok", "mismatch": "warning",
              "failed": "failed"}.get(st, "running")
    return _step("cross", r.get("updated_at"), f"复查 {r.get('target')}", status,
                 {"target": r.get("target"), "owner": r.get("owner") or r.get("tentacle"),
                  "status": st}, r.get("id"), r.get("trace_id"))


def map_arb(r):
    same = r.get("consistent", r.get("same", None))
    status = "ok" if same in (1, True, "1") else "warning"
    return _step("arbitration", r.get("ts"), "仲裁结论", status,
                 {"conclusion": r.get("conclusion"), "consistent": same},
                 r.get("id"), r.get("trace_id"))


def map_seg(r):
    n = r.get("frame_count") or r.get("frames")
    return _step("devour", r.get("ts"), f"帧段 {r.get('start_frame')}–{r.get('end_frame')}",
                 "ok" if n else "unknown",
                 {"seg_id": r.get("seg_id") or r.get("id"),
                  "start_frame": r.get("start_frame"), "end_frame": r.get("end_frame"),
                  "frames": n, "path": r.get("path") or r.get("file"),
                  "sha256": r.get("sha256") or r.get("sha"),
                  "r2": r.get("r2_key"), "tier": r.get("tier")},
                 r.get("seg_id") or r.get("id"), r.get("trace_id"))


def map_gap(r):
    # ⚠️ gap 永远是一等 failed 步骤
    missing = r.get("missing") or r.get("count") or 1
    return _step("gap", r.get("ts"),
                 f"⚠ 丢帧 {missing} 帧 @{r.get('frame_no')}", "failed",
                 {"frame_no": r.get("frame_no"), "missing": missing,
                  "reason": r.get("reason"), "detected": r.get("ts")},
                 r.get("gap_id") or r.get("id"), r.get("trace_id"))


def map_brain(r):
    # 主脑卡点（skill_calls 里 engine=brain 或 verdict 非 continue）
    ok = bool(r.get("ok"))
    return _step("brain", r.get("ts"), f"主脑 {r.get('skill')}",
                 "ok" if ok else "warning",
                 {"skill": r.get("skill"), "error": r.get("error"),
                  "result": (r.get("result") or "")[:400]},
                 r.get("task_id"), r.get("trace_id"))


MAPPERS = {"map_scan": map_scan, "map_cross": map_cross, "map_arb": map_arb,
           "map_seg": map_seg, "map_gap": map_gap, "map_brain": map_brain}


# ══ 汇总：每个 trace 的计数（不拉全量）══
def trace_summary(led) -> dict:
    st = source_status(led)
    summ = {}
    # 帧段聚合
    if st["devour"]["available"]:
        t = st["devour"]["table"]
        for r in _q(led, f"""SELECT trace_id, COUNT(*) segs
                             FROM {t} WHERE trace_id IS NOT NULL
                             GROUP BY trace_id"""):
            summ.setdefault(r["trace_id"], {})["segs"] = r["segs"]
    # gap 聚合
    if st["gap"]["available"]:
        t, cols = st["gap"]["table"], _cols(led, st["gap"]["table"])
        mcol = _pick(cols, ("missing", "count")) or "1"
        for r in _q(led, f"""SELECT trace_id, COUNT(*) gaps,
                             SUM({mcol}) missing
                             FROM {t} WHERE trace_id IS NOT NULL
                             GROUP BY trace_id"""):
            summ.setdefault(r["trace_id"], {}).update(
                {"gaps": r["gaps"], "missing": r["missing"] or 0})
    # 交叉聚合
    if st["cross"]["available"]:
        t = st["cross"]["table"]
        for r in _q(led, f"""SELECT trace_id, COUNT(*) cross_n
                             FROM {t} WHERE trace_id IS NOT NULL
                             GROUP BY trace_id"""):
            summ.setdefault(r["trace_id"], {})["cross"] = r["cross_n"]
    return summ


# ══ 组装步骤链 ══
def build_chains(led, limit=30):
    st = source_status(led)
    chains = {}
    for s in SOURCES:
        info = st[s["key"]]
        if not info["available"]:
            continue
        table = info["table"]
        rows = _q(led, f"""SELECT * FROM {table}
                           WHERE trace_id IS NOT NULL
                           ORDER BY {s["time"]} DESC LIMIT {"{}"}""", (limit * 20,))
        fn = MAPPERS[s["map"]]
        for r in rows:
            tid = r.get("trace_id")
            if not tid:
                continue
            chains.setdefault(tid, {"trace_id": tid, "steps": []})["steps"].append(fn(r))
    summ = trace_summary(led)
    out = []
    for tid, c in chains.items():
        c["steps"].sort(key=lambda x: x["ts"] or 0)
        # 状态优先级: 任一 failed → 链 fail; 任一 warning → warn
        stats = {s["status"] for s in c["steps"]}
        c["status"] = ("failed" if "failed" in stats else
                       "warning" if "warning" in stats else "ok")
        c["summary"] = summ.get(tid, {})
        out.append(c)
    out.sort(key=lambda c: -(max((s["ts"] or 0) for s in c["steps"])))
    return {"source_status": st, "chains": out[:limit],
            "unlinked": unlinked_events(led, st)}


# ══ 未关联事件（不强行塞进链）══
def unlinked_events(led, st, limit=50):
    out = []
    for key in ("devour", "gap", "cross"):
        info = st[key]
        if not info["available"]:
            continue
        t = info["table"]
        rows = _q(led, f"""SELECT * FROM {t} WHERE trace_id IS NULL
                           ORDER BY rowid DESC LIMIT {"{}"}"""
                  if led.dialect == "sqlite" else
                  f"""SELECT * FROM {t} WHERE trace_id IS NULL
                      ORDER BY ctid DESC LIMIT {"{}"}""", (limit,))
        for r in rows:
            out.append({"source": key, "raw": {k: str(v)[:200] for k, v in r.items()}})
    return out[:limit]


# ══ 单链详情 + 明细分页 ══
def chain_detail(led, trace_id):
    st = source_status(led)
    steps = []
    for s in SOURCES:
        info = st[s["key"]]
        if not info["available"]:
            continue
        rows = _q(led, f"""SELECT * FROM {info["table"]}
                           WHERE trace_id={"{}"} ORDER BY {s["time"]}""", (trace_id,))
        fn = MAPPERS[s["map"]]
        steps += [fn(r) for r in rows]
    steps.sort(key=lambda x: x["ts"] or 0)
    stats = {s["status"] for s in steps}
    return {"trace_id": trace_id, "steps": steps,
            "status": ("failed" if "failed" in stats else
                       "warning" if "warning" in stats else "ok"),
            "source_status": st}


def chain_items(led, trace_id, source=None, limit=100):
    """明细：帧段全字段 / gap 全字段 / 交叉 + 仲裁"""
    st = source_status(led)
    out = {}
    for s in SOURCES:
        if source and s["key"] != source:
            continue
        info = st[s["key"]]
        if not info["available"]:
            out[s["key"]] = {"available": False, "reason": info["reason"]
                             if "reason" in info else "unavailable",
                             "items": []}
            continue
        rows = _q(led, f"""SELECT * FROM {info["table"]}
                           WHERE trace_id={"{}"}
                           ORDER BY {s["time"]} DESC LIMIT {"{}"}""",
                  (trace_id, limit))
        out[s["key"]] = {"available": True, "table": info["table"],
                         "items": [{k: (str(v)[:500] if v is not None else None)
                                    for k, v in r.items()} for r in rows]}
    return out
