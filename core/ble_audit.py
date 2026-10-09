# core/ble_audit.py —— 蓝牙操作审计（追加式 JSONL：谁、何时、哪台设备、结果）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么用追加式文件而不是单表：
#   · 审计要"只增不改"：一行一条，绝不覆盖历史（崩溃也只丢最后半行）
#   · 不接触任何查询语句，天然没有注入面
#   · 文件固定在仓库内 state/ble_ops.jsonl（文件名是常量，无用户输入参与路径）
import json
import os
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.normpath(os.path.join(ROOT, "state"))
FILENAME = "ble_ops.jsonl"


def audit_path() -> str:
    """审计文件绝对路径：目录是仓库内 state/，文件名是常量。"""
    return os.path.join(STATE_DIR, FILENAME)


def append(entry: dict) -> dict:
    """追加一条操作记录；失败如实返回 ok=False + 原因（绝不静默吞掉）。"""
    rec = {"op_id": uuid.uuid4().hex[:12],
           "at": time.time(),
           "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "action": str(entry.get("action") or ""),
           "target": str(entry.get("target") or ""),
           "decided_by": str(entry.get("decided_by") or ""),
           "ok": bool(entry.get("ok")),
           "reason": str(entry.get("reason") or "")[:400]}
    p = audit_path()
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"ok": True, "op_id": rec["op_id"], "path": p}
    except OSError as exc:                                # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}", "path": p}


def read_all() -> dict:
    """读回全部记录（新→旧）；坏行跳过并计数，不做静默丢弃。"""
    p = audit_path()
    if not os.path.isfile(p):
        return {"ok": True, "rows": [], "bad_lines": 0, "reason": "还没有蓝牙操作记录"}
    rows, bad = [], 0
    try:
        with open(p, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    bad += 1
    except OSError as exc:                                # noqa: BLE001
        return {"ok": False, "rows": [], "bad_lines": 0, "reason": f"{type(exc).__name__}"}
    rows.sort(key=lambda r: r.get("at") or 0, reverse=True)
    return {"ok": True, "rows": rows, "bad_lines": bad, "reason": ""}


def recent(limit: int = 20) -> dict:
    """最近 N 条（在 Python 侧切片，不把数量拼进任何查询）。"""
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 20
    got = read_all()
    rows = got.get("rows") or []
    return {**got, "rows": rows[:n], "count": len(rows), "shown": min(n, len(rows))}


def summary() -> dict:
    """审计概览：总条数 / 成功 / 失败 / 最近一条时间。"""
    got = read_all()
    rows = got.get("rows") or []
    ok = sum(1 for r in rows if r.get("ok"))
    return {"ok": got.get("ok"), "total": len(rows), "success": ok,
            "failed": len(rows) - ok, "bad_lines": got.get("bad_lines"),
            "latest_at": (rows[0]["iso"] if rows else None), "path": audit_path()}


__all__ = ["append", "read_all", "recent", "summary", "audit_path"]
