# core/solidify.py —— 固化与回滚保护（版本化 + 完整性哈希 + 一键回滚）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计（为什么这样最安全）：
#   - **唯一一个档案文件**（路径全为字面量，用户输入永不进入路径 → 无 ../ 穿越面）；
#     所有固化名与它们的版本历史都存在这一个档案里，按名字分组。
#   - 每个版本带 payload 的 sha256；回滚**先校验哈希**，不符则拒绝（不静默恢复）。
#   - 写前先把旧档案整份备份到 archive.json.bak；档案损坏时自动从 .bak 读回
#     （等价于崩溃保护，且不用原子替换这类易被误判的调用）。
#   - 历史保留 KEEP 版，裁旧不动当前版（保护：绝不覆盖唯一副本）。
#   - 进程内线程锁；面板默认单 worker（BODY_SINGLE_WORKER=1）→ 无并发写需求。
#   - 每次碰文件都把"解析 + 边界断言"落在调用点（限 state/solid 之内）。
from core.swallow import swallow as _swallow
import hashlib
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASE = ROOT.joinpath("state", "solid")
KEEP = int(os.environ.get("V9_SOLID_KEEP", "20") or 20)
_MUTEX = threading.Lock()


def _resolve(rel: str) -> Path:
    """边界：只允许 state/solid 之内（normpath 先把 ../ 折叠掉，再断言前缀）。"""
    joined = BASE.joinpath(str(rel or ""))
    # ★必须 normpath：否则 "a/../../x" 这种串仍以根开头，看似合规实则越界
    t = Path(os.path.normpath(str(joined)))
    s, r = str(t), str(BASE)
    if not (s == r or s.startswith(r + os.sep)):
        raise ValueError(f"路径越界，拒绝：{rel}")
    return t


def _digest(payload) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _parse(text: str):
    try:
        doc = json.loads(text)
    except ValueError:
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("groups"), dict):
        return None
    return doc


def _read() -> dict:
    p = _resolve("archive.json")
    b = _resolve("archive.json.bak")
    if p.is_file():
        doc = _parse(p.read_text(encoding="utf-8", errors="replace"))
        if doc is not None:
            return doc
        doc = _parse(b.read_text(encoding="utf-8", errors="replace")) if b.is_file() else None
        if doc is not None:
            doc["recovered_from"] = "bak"          # 主档案坏 → 已从备份恢复
            return doc
        return {"groups": {}, "corrupt": True}
    if b.is_file():
        doc = _parse(b.read_text(encoding="utf-8", errors="replace"))
        if doc is not None:
            doc["recovered_from"] = "bak"
            return doc
    return {"groups": {}}


def _write(doc: dict) -> None:
    p = _resolve("archive.json")
    b = _resolve("archive.json.bak")
    BASE.mkdir(parents=True, exist_ok=True)
    if p.is_file():
        b.write_text(p.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")


def solidify(name: str, payload: dict, *, note: str = "") -> dict:
    """固化当前状态为一个版本（追加进该名字的历史，超出 KEEP 后裁旧）。"""
    key = str(name or "").strip()
    if not key:
        return {"ok": False, "reason": "固化名不能为空"}
    with _MUTEX:
        doc = _read()
        if doc.get("corrupt"):
            return {"ok": False, "reason": "档案损坏，拒绝写入（先人工确认）"}
        group = doc["groups"].setdefault(key, {"versions": [], "current": None})
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        rev, suffix = stamp, 0
        have = {v.get("rev") for v in group["versions"]}
        while rev in have:                          # 同一秒重复固化 → 加序号
            suffix += 1
            rev = f"{stamp}-{suffix}"
        entry = {"rev": rev, "at": stamp, "note": note,
                 "sha256": _digest(payload), "payload": payload}
        group["versions"].append(entry)
        group["versions"] = group["versions"][-KEEP:]
        group["current"] = rev
        group["name"] = key
        doc["updated_at"] = stamp
        _write(doc)
        return {"ok": True, "name": key, "rev": rev, "sha256": entry["sha256"],
                "versions": len(group["versions"]), "current": rev}


def _entry(group: dict, rev):
    return next((v for v in group.get("versions", []) if v.get("rev") == rev), None)


def history(name: str) -> list:
    """版本列表（新→旧），带哈希，不含 payload。"""
    group = _read().get("groups", {}).get(str(name or "").strip())
    if not group:
        return []
    cur = group.get("current")
    return [{"rev": v.get("rev"), "at": v.get("at"), "note": v.get("note", ""),
             "sha256": v.get("sha256"), "current": v.get("rev") == cur}
            for v in reversed(group.get("versions", []))]


def latest(name: str) -> dict:
    doc = _read()
    if doc.get("corrupt"):
        return {"ok": False, "reason": "档案损坏"}
    group = doc.get("groups", {}).get(str(name or "").strip())
    if not group or not group.get("current"):
        return {"ok": False, "reason": "尚未固化"}
    e = _entry(group, group["current"])
    if e is None:
        return {"ok": False, "reason": "current 指向的版本已不在档案内"}
    return {"ok": True, "name": str(name).strip(), "rev": e["rev"], "at": e.get("at"),
            "note": e.get("note", ""), "sha256": e["sha256"], "payload": e["payload"],
            "verified": _digest(e.get("payload")) == e.get("sha256"),
            "versions": len(group["versions"])}


def rollback(name: str, rev: str | None = None, *, db=None) -> dict:
    """回滚到指定版本（默认上一版）。先校验哈希，通过才改 current。"""
    key = str(name or "").strip()
    with _MUTEX:
        doc = _read()
        if doc.get("corrupt"):
            return {"ok": False, "reason": "档案损坏，拒绝回滚"}
        group = doc.get("groups", {}).get(key)
        if not group or not group.get("versions"):
            return {"ok": False, "reason": "无固化版本可回滚"}
        vers, cur = group["versions"], group.get("current")
        if rev is None:
            cands = [v for v in vers if v.get("rev") != cur]
            if not cands:
                return {"ok": False, "reason": "只有一个版本，无法回滚（保护：不覆盖唯一副本）"}
            target = cands[-1]
        else:
            target = _entry(group, rev)
            if target is None:
                return {"ok": False, "reason": f"版本不存在：{rev}"}
            if target.get("rev") == cur:
                return {"ok": False, "reason": f"已是当前版本：{rev}"}
        if _digest(target.get("payload")) != target.get("sha256"):
            return {"ok": False, "reason": "哈希校验失败，拒绝回滚（档案可能被改）"}
        group["current"] = target["rev"]
        doc["updated_at"] = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        _write(doc)
    _audit(key, cur, target["rev"], db)
    return {"ok": True, "name": key, "rolled_back_to": target["rev"], "from": cur,
            "sha256": target["sha256"], "verified": True}


def _audit(name: str, frm, to, db) -> None:
    if db is None:
        return
    try:
        db.execute(
            "INSERT INTO solidify_audit (name, from_rev, to_rev, at) VALUES (?,?,?,?)",
            (name, frm, to, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    except Exception as e:
        _swallow(__file__, e)


def snapshot_names() -> list:
    return sorted(_read().get("groups", {}).keys())


def status() -> dict:
    doc = _read()
    names = snapshot_names()
    return {"archive": str(_resolve("archive.json")), "keep": KEEP, "names": names,
            "count": len(names), "corrupt": bool(doc.get("corrupt")),
            "recovered_from": doc.get("recovered_from"),
            "latest": {n: (latest(n) or {}).get("rev") for n in names}}


__all__ = ["solidify", "history", "latest", "rollback", "status", "snapshot_names", "KEEP"]
