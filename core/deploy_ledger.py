# core/deploy_ledger.py —— 部署与变更日志（扫描 / 新增 / 修改 / 部署 / 固化 全留痕）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么不是表而是追加式文件：变更日志要么完整要么没有意义。
#   一行一条、只增不改，崩溃最多丢最后半行；不接触任何查询语句，天然没有注入面。
#   文件固定在仓库内 state/ 下，目录名与文件名都是常量，无用户输入参与路径。
#
# 记录粒度（对应主人要求"每一次扫描了那里，修改了那里，新增了那里全部都要有完整的记录"）：
#   kind = scan     扫描：扫了哪里（scope）+ 扫到什么（清点项/新增/修改/消失 计数）
#   kind = add      新增：新增了什么（where 精确到 流水线/步骤/插件槽）
#   kind = modify   修改：改了哪里、改前改后（before → after）
#   kind = deploy   部署：哪一步部署到哪个云插件槽/库槽，结果如何
#   kind = solidify 固化：定格了哪个名字、哪个版本、哈希多少
import json
import os
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.normpath(os.path.join(ROOT, "state"))
FILENAME = "deploy_journal.jsonl"
SNAPSHOT_NAME = "deploy_scan_snapshot.jsonl"      # 快照也追加：读最后一行即最新清点

KINDS = ("scan", "add", "modify", "deploy", "solidify")


def journal_path() -> str:
    """变更日志路径：目录是仓库内 state/，文件名是常量。"""
    return os.path.join(STATE_DIR, FILENAME)


def snapshot_path() -> str:
    """扫描快照路径（追加式，只读最后一行）。"""
    return os.path.join(STATE_DIR, SNAPSHOT_NAME)


def _append(path: str, obj: dict) -> dict:
    """追加一行 JSON；失败如实返回原因（绝不静默吞掉）。"""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return {"ok": True}
    except OSError as exc:                                # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}"}


def record(kind: str, where: str, *, detail=None, before=None, after=None,
           ok: bool = True, reason: str = "") -> dict:
    """追加一条变更记录。kind 只认 5 类；未知 kind 直接拒绝（不写脏数据）。"""
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        return {"ok": False, "reason": f"未知记录类型：{kind}（只允许 {KINDS}）"}
    rec = {"rec_id": uuid.uuid4().hex[:12],
           "at": time.time(),
           "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "kind": k, "where": str(where or "")[:300],
           "detail": _short(detail), "before": _short(before), "after": _short(after),
           "ok": bool(ok), "reason": str(reason or "")[:400]}
    got = _append(journal_path(), rec)
    return {**got, "rec_id": rec["rec_id"], "path": journal_path()}


def _short(v):
    """把任意值压成可读短串（**只用于日志的人读字段**）。

    注意：结构化数据（dict/list）在超过软上限时会退化成摘要 —— 只用于日志展示。
    扫描快照要用完整结构做逐项比对，走 _snapshot_items()，绝不走这里。
    """
    if v is None:
        return None
    if isinstance(v, (int, float, bool)):
        return v
    if isinstance(v, str):
        return v[:600]
    try:
        blob = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(v)[:600]
    if len(blob) <= 4000:
        return v
    return {"_摘要": True, "_字节": len(blob), "_片段": blob[:800]}


def _snapshot_items(inventory: dict) -> dict:
    """快照用的清点项：逐项保留完整结构（可序列化即原样存）。"""
    out = {}
    for k, v in (inventory or {}).items():
        try:
            json.dumps(v, ensure_ascii=False, default=str)
            out[str(k)] = v
        except (TypeError, ValueError):
            out[str(k)] = str(v)[:600]
    return out


def _read_lines(path: str) -> dict:
    if not os.path.isfile(path):
        return {"ok": True, "rows": [], "bad_lines": 0, "reason": "文件还不存在"}
    rows, bad = [], 0
    try:
        with open(path, encoding="utf-8") as fh:
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
    return {"ok": True, "rows": rows, "bad_lines": bad, "reason": ""}


def read_all() -> dict:
    """读回全部记录（新→旧）；坏行跳过并计数。"""
    got = _read_lines(journal_path())
    if not got["ok"]:
        return got
    rows = got["rows"]
    rows.sort(key=lambda r: r.get("at") or 0, reverse=True)
    return {**got, "rows": rows}


def recent(limit: int = 50) -> dict:
    """最近 N 条（在 Python 侧切片）。"""
    try:
        n = max(1, int(limit))
    except (TypeError, ValueError):
        n = 50
    got = read_all()
    rows = got.get("rows") or []
    return {**got, "rows": rows[:n], "count": len(rows)}


def prev_hash(path: str, *, kind: str = "") -> str:
    """这条路径**上一次**记录的 after 哈希 —— 用它当本次的 before。

    为什么不用 git：本机这个 git 仓库的根是家目录，HEAD 树里根本没有本项目
    （`git show HEAD:gbt-potato-v9/...` 直接报"exists on disk, but not in HEAD"），
    所以早期的 before 全写成了"(HEAD 无)"，等于没记。台账本来就该**自己把哈希串起来**：
    上一条 after 就是这一条的 before，链式可回放，不依赖任何外部版本库。
    """
    p = str(path or "")
    for r in read_all().get("rows") or []:
        if str(r.get("where") or "") != p:
            continue
        if kind and str(r.get("kind") or "") != str(kind):
            continue
        after = str(r.get("after") or "")
        if after and not after.startswith("("):
            return after
    return ""


def record_change(path: str, *, detail: str = "", ok: bool = True, reason: str = "",
                  kind: str = "modify") -> dict:
    """记一次文件变更：**before 自动取上一条 after**（链式），after 取文件当前 sha256。

    这是"每一次修改都要有完整记录"的正确做法：不依赖 git，也不用手工传哈希。
    """
    import hashlib
    from pathlib import Path as _P
    p = _P(str(path or ""))
    try:
        after = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
    except OSError:
        after = "(读不到)"
    before = prev_hash(str(path), kind=kind) or "(首次记录)"
    return record(kind, str(path), detail=detail, before=before, after=after, ok=ok,
                  reason=reason)


def summary() -> dict:
    """分类型统计：扫描/新增/修改/部署/固化 各多少条，最近一条什么时候。"""
    rows = read_all().get("rows") or []
    by = {k: 0 for k in KINDS}
    for r in rows:
        if r.get("kind") in by:
            by[r["kind"]] += 1
    return {"total": len(rows), "by_kind": by,
            "latest_iso": (rows[0]["iso"] if rows else None),
            "path": journal_path(), "snapshot": snapshot_path(),
            "scopes": scopes(),
            "failed": sum(1 for r in rows if not r.get("ok"))}


# ═══════════ 扫描 → 比对 → 记录（新增/修改/消失 一目了然）═══════════
# 关键设计：快照**按 scope 分仓**放在同一个文件里（文件名是常量，无路径穿越面）。
#   —— 否则不同扫描（流水线 / 三套工具包）会互相把对方的项判成"消失"，那就是混乱。
def _load_snapshot(scope: str) -> dict:
    """取某个 scope 的最新清点（= 快照文件最后一行的 scopes[scope]）。"""
    got = _read_lines(snapshot_path())
    rows = got.get("rows") or []
    if not rows:
        return {"items": {}, "at": None}
    last = rows[-1] if isinstance(rows[-1], dict) else {}
    scopes = last.get("scopes")
    if not isinstance(scopes, dict):
        # 兼容最早那版只存 items 的快照：当作 scope="default"
        legacy = last.get("items")
        return {"items": legacy if isinstance(legacy, dict) else {}, "at": last.get("at")}
    box = scopes.get(scope) or {}
    return {"items": box.get("items") or {}, "at": box.get("at")}


def _save_snapshot(scope: str, items: dict) -> dict:
    """把该 scope 的清点写进快照（保留其它 scope 的清点，互不干扰）。"""
    got = _read_lines(snapshot_path())
    rows = got.get("rows") or []
    last = rows[-1] if rows and isinstance(rows[-1], dict) else {}
    scopes = last.get("scopes") if isinstance(last.get("scopes"), dict) else {}
    scopes = {k: v for k, v in scopes.items() if k != scope}
    scopes[scope] = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "items": _snapshot_items(items)}
    doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "scopes": scopes}
    got2 = _append(snapshot_path(), doc)
    return {**got2, "items": len(doc["scopes"][scope]["items"]), "scopes": sorted(scopes)}


def scan_and_record(inventory: dict, scope: str = "default") -> dict:
    """扫描并记录：当前清点与该 scope 上次快照比对，逐条记 新增/修改/消失（都带"哪里"）。

    inventory 形如 {item_key: {"状态":…, "云插件":…}}；item_key 就是"哪里"。
    """
    scope = str(scope or "default")
    prev = _load_snapshot(scope).get("items") or {}
    cur = {k: _snapshot_items({k: v})[k] for k, v in (inventory or {}).items()}

    def _canon(x):
        return json.dumps(x, sort_keys=True, ensure_ascii=False)

    added = sorted(k for k in cur if k not in prev)
    removed = sorted(k for k in prev if k not in cur)
    changed = sorted(k for k in cur if k in prev and _canon(cur[k]) != _canon(prev[k]))

    record("scan", scope, detail={"清点项": len(cur), "上次清点": len(prev),
                                  "新增": len(added), "修改": len(changed),
                                  "消失": len(removed)})
    for k in added:
        record("add", k, detail=cur[k])
    for k in changed:
        record("modify", k, before=prev[k], after=cur[k])
    for k in removed:
        record("modify", k, before=prev[k], after=None, ok=False, reason="本次扫描已不存在（消失）")
    saved = _save_snapshot(scope, cur)
    return {"ok": True, "scope": scope, "items": len(cur), "added": added,
            "changed": changed, "removed": removed, "snapshot": saved,
            "at": _load_snapshot(scope).get("at")}


def scopes() -> list:
    """快照里已经记过清点的 scope 列表。"""
    got = _read_lines(snapshot_path())
    rows = got.get("rows") or []
    if not rows or not isinstance(rows[-1], dict):
        return []
    scopes_box = rows[-1].get("scopes")
    if isinstance(scopes_box, dict):
        return sorted(scopes_box)
    return ["default"] if rows[-1].get("items") else []


__all__ = ["record", "read_all", "recent", "summary", "scan_and_record", "scopes",
           "KINDS", "journal_path", "snapshot_path", "prev_hash", "record_change"]
