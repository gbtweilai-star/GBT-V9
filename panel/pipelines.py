# panel/pipelines.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 流水线数据源（面板 / 告警 / 趋势共用）：把账本里的表映射成统一的步骤流。
# 交付时本文件只给了「SOURCES 增项 + map_repair + trace_summary 汇总」片段，
# 这里按片段语义补全为可直接导入的模块；_cols / _pick / source_status / _series
# 为防御式取数工具（缺表缺列一律返回空，不抛）。
from __future__ import annotations

from typing import Any
from core.swallow import swallow as _swallow

# ── 数据源清单（每项：key/label/表名/时间列/引用列/映射函数名）──
SOURCES: list[dict[str, Any]] = [
    {"key": "scan", "label": "扫描", "shape": "scan", "color": "#4dabf7",
     "tables": ("ledger",), "time": "ts", "ref": ("target",), "map": "map_scan"},
    {"key": "devour", "label": "吞噬", "shape": "devour", "color": "#38d9a9",
     "tables": ("devour_frames",), "time": "captured_at", "ref": ("ref",), "map": "map_devour"},
    {"key": "queue", "label": "队列", "shape": "queue", "color": "#b197fc",
     "tables": ("queue_tasks",), "time": "created_at", "ref": ("task_id",), "map": "map_queue"},
    {"key": "gap", "label": "丢帧", "shape": "gap", "color": "#ff8787",
     "tables": ("frame_gaps",), "time": "ts", "ref": ("gap_id",), "map": "map_gap"},
    # ── SOURCES 里追加一项（丢帧补偿，来自对话片段）──
    {"key": "repair", "label": "丢帧补偿", "shape": "wrench", "color": "#ffa94d",
     "tables": ("repair_attempts",), "time": "ts",
     "ref": ("gap_id",), "map": "map_repair"},
]

MAPPERS: dict[str, Any] = {}


def _step(key: str, ts: Any, label: str, status: str, detail: dict,
          ref: Any = None, trace_id: Any = None) -> dict:
    return {"key": key, "ts": ts, "label": label, "status": status,
            "detail": detail, "ref": ref, "trace_id": trace_id}


def _pick(obj: Any, *names: Any, default: Any = None) -> Any:
    """兼容两种用法：
    - _pick(row, "restored", "resampled")   → 从行/字典取值；
    - _pick(cols, ("missing", "count"))     → 从列名集合里挑存在的列名。
    """
    if len(names) == 1 and isinstance(names[0], (tuple, list, set, frozenset)):
        candidates = list(names[0])
    else:
        candidates = list(names)
    try:
        if isinstance(obj, (set, frozenset, list, tuple)):
            for name in candidates:
                if name in obj:
                    return name
            return default
        for name in candidates:
            if isinstance(obj, dict):
                if name in obj:
                    return obj[name]
            else:
                try:
                    return obj[name]
                except Exception:
                    continue
    except Exception as e:
        _swallow(__file__, e)

    return default


def _cols(led: Any, table: str | None = None) -> set:
    """防御式取列名：
    - _cols(rows)         → 从查询结果首行取键；
    - _cols(led, table)   → 向账本适配器问该表的列（失败返回空集，不抛）。
    """
    if table is None:
        rows = led
        try:
            if not rows:
                return set()
            first = rows[0]
            if isinstance(first, dict):
                return set(first.keys())
            keys = getattr(first, "keys", None)
            return set(keys()) if callable(keys) else set()
        except Exception:
            return set()
    for attr in ("columns", "table_columns", "pragma", "query"):
        fn = getattr(led, attr, None)
        if not callable(fn):
            continue
        try:
            return set(fn(table))
        except Exception:
            continue
    return set()


def source_status(led: Any) -> dict:
    """每个数据源的可用性：{key: {available, table, columns}}；缺表缺列不抛。"""
    out = {}
    for src in SOURCES:
        table = src["tables"][0]
        cols = _cols(led, table)
        out[src["key"]] = {"available": bool(cols), "table": table, "columns": sorted(cols)}
    return out


def _series(led: Any, table: str, tcol: str, agg: str,
            start: int, end: int, bucket: int) -> dict:
    """分桶聚合：返回 {bucket_start: {列: 值}}；查询失败返回空（前端断线，不伪装零）。"""
    fn = None
    for attr in ("query", "fetch_all", "series"):
        cand = getattr(led, attr, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        return {}
    sql = (f"SELECT CAST(({tcol}-{int(start)})/{int(bucket)} AS INTEGER)*{int(bucket)}"
           f"+{int(start)} AS bucket, {agg} FROM {table}"
           f" WHERE {tcol} >= {int(start)} AND {tcol} < {int(end)}"
           f" GROUP BY bucket ORDER BY bucket")
    try:
        rows = fn(sql)
    except Exception:
        return {}
    out = {}
    for r in rows or []:
        if isinstance(r, dict):
            b = int(r.get("bucket") or 0)
            out[b] = dict(r)
    return out


def map_repair(r: Any) -> dict:
    """丢帧补偿行 → 步骤（片段原文语义，防御式包装）。"""
    status_raw = _pick(r, "status")
    status = {"restored": "ok", "resampled": "warning",
              "permanent": "failed", "failed": "warning"}.get(status_raw, "running")
    label = {"restored": f"✔ 写回补齐 {_pick(r, 'restored')} 帧",
             "resampled": f"↻ 重采 {_pick(r, 'resampled')} 帧（原缺帧仍缺失）",
             "permanent": f"⛔ 永久丢失 {_pick(r, 'permanent')} 帧",
             "failed": f"✕ 第 {_pick(r, 'attempt_no')} 次补偿失败"}.get(status_raw, "补偿中")
    return _step("repair", _pick(r, "ts"), label, status,
                 {"gap_id": _pick(r, "gap_id"), "attempt": _pick(r, "attempt_no"),
                  "strategy": _pick(r, "strategy"), "cause": _pick(r, "cause"),
                  "restored": _pick(r, "restored"), "resampled": _pick(r, "resampled"),
                  "permanent": _pick(r, "permanent"),
                  "artifact": _pick(r, "artifact"), "sha": _pick(r, "artifact_sha"),
                  "error": _pick(r, "error")},
                 _pick(r, "gap_id"), _pick(r, "trace_id"))


# ── MAPPERS 注册 ──
MAPPERS["map_repair"] = map_repair


def trace_summary(led: Any, trace_id: str) -> dict:
    """按 trace 汇总补偿侧数字（片段原文语义，防御式实现）。"""
    fn = None
    for attr in ("query", "fetch_all"):
        cand = getattr(led, attr, None)
        if callable(cand):
            fn = cand
            break
    if fn is None:
        return {}
    st = source_status(led)
    if not st.get("repair", {}).get("available"):
        return {}
    t = st["repair"]["table"]
    try:
        rows = fn(f"SELECT trace_id, SUM(restored) restored, SUM(resampled) resampled, "
                  f"SUM(permanent) permanent FROM {t} "
                  f"WHERE trace_id IS NOT NULL GROUP BY trace_id")
    except Exception:
        return {}
    for r in rows or []:
        if _pick(r, "trace_id") == trace_id:
            return {"restored": _pick(r, "restored", default=0) or 0,
                    "resampled": _pick(r, "resampled", default=0) or 0,
                    "permanent": _pick(r, "permanent", default=0) or 0}
    return {}


__all__ = ["SOURCES", "MAPPERS", "_step", "_pick", "_cols", "source_status",
           "_series", "map_repair", "trace_summary"]
