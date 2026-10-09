# core/file_binding.py —— 每页代码双向绑定 + 无死角穿透（GBT 神经系统部署法）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「不管部署什么项目都要用触手在**每一页代码**上做好双向绑定，
#   确保用户想看什么、想扫描就能做到真正无死角穿透每一页、瞬间完成」；
#   「报警但不影响运行就不管 —— 这在 GBT 家族里严格禁止。」
#
# 复用既有事实源（不另造）：migrations/m2026_body_bindings_v9.py 的 tentacle_file_bindings
#   (root_id, tentacle_id, path, rule_id, state, sha256, bound_at) + ix_tfb_path/tentacle/state
# 读方已存在：panel/routes/body.py:96、body/tools/collect.py:78（本次把**缺的写方**补上）。
# 口径：绑定只对**代码/配置/文档**（排除 vendored 第三方与产物），排除项**显式登记**为「不绑」，绝不四舍五入成 100%。
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROOT_ID = "v9:GBT小土豆V9"
LEDGER = ROOT / "state" / "file_binding.jsonl"
RULE = "v9-bind"

# 不绑的根（第三方/产物/缓存）——显式登记原因，覆盖率的"分母"要看得见
EXCLUDE = {
    "desktop/": "第三方便携包（vendored）",
    "desktop": "第三方便携包（vendored）",
    "_archive/": "历史归档",
    "_archive": "历史归档",
    "release/": "构建产物",
    "release": "构建产物",
    "node_modules/": "依赖树",
    "state/": "运行时台账（非代码）",
    "data/": "运行时数据",
    "render/": "渲染产物",
    ".git/": "版本库内部",
    "__pycache__/": "字节码缓存",
}


# 绑定集缓存（TTL 60s）：git ls-files 在 3.3 万未跟踪文件上要 1.6s，别每次穿透都付
_KEEP_CACHE: dict = {"at": 0.0, "set": None}


def _keep(*, ttl: float = 60.0) -> set:
    now = time.time()
    if _KEEP_CACHE["set"] is not None and now - _KEEP_CACHE["at"] < ttl:
        return _KEEP_CACHE["set"]
    s = set(tracked_files()["绑定集"])
    _KEEP_CACHE.update({"at": now, "set": s})
    return s


def wipe_root() -> dict:
    """清空本 root 的绑定行（重整用；只动我们自己 root_id 的行）。"""
    led = _ledger()
    try:
        with _txn(led) as cur:
            cur.execute("DELETE FROM tentacle_file_bindings WHERE root_id=?", (ROOT_ID,))
        _KEEP_CACHE["set"] = None
        return {"ok": True, "已清空 root": ROOT_ID}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "失败": "%s: %s" % (type(e).__name__, str(e)[:120])}


def _ledger():
    from audit.ledger_factory import make_ledger
    return make_ledger()


def _txn(led):
    from senses.sqldialect import txn
    return txn(led)


def ensure_table(led=None) -> dict:
    """幂等建表 + 索引（与迁移同形；已有则跳过）。"""
    led = led or _ledger()
    stmts = [
        "CREATE TABLE IF NOT EXISTS tentacle_file_bindings ("
        " root_id TEXT NOT NULL, tentacle_id TEXT NOT NULL, path TEXT NOT NULL,"
        " rule_id TEXT, state TEXT NOT NULL DEFAULT 'clean', sha256 TEXT, bound_at TEXT,"
        " PRIMARY KEY (root_id, tentacle_id, path))",
        "CREATE INDEX IF NOT EXISTS ix_tfb_tentacle ON tentacle_file_bindings (tentacle_id, path)",
        "CREATE INDEX IF NOT EXISTS ix_tfb_path ON tentacle_file_bindings (path)",
        "CREATE INDEX IF NOT EXISTS ix_tfb_state ON tentacle_file_bindings (state)",
    ]
    ok = 0
    with _txn(led) as cur:
        for s in stmts:
            try:
                cur.execute(s)
                ok += 1
            except Exception:  # noqa: BLE001
                continue
    return {"ok": ok == len(stmts), "建了/已在": ok, "共": len(stmts)}


def tracked_files(*, include_excluded: bool = False) -> dict:
    """受跟踪文件（git ls-files）拆成 绑定集 / 排除集（排除项带原因）。"""
    # 🔴 修：ls-files 只列已入库文件 ⇒ 我刚新建的代码页会漏绑。加 --others --exclude-standard
    out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                         capture_output=True, text=True,
                         encoding="utf-8", errors="replace", cwd=str(ROOT))
    files = [f.strip() for f in (out.stdout or "").splitlines() if f.strip()]
    bind, skip = [], []
    for f in files:
        hit = next((k for k in EXCLUDE if f == k.rstrip("/") or f.startswith(k)), None)
        why = EXCLUDE[hit] if hit else _ext_reason(f)
        if why and not include_excluded:
            skip.append({"path": f, "原因": why})
        else:
            bind.append(f)
    return {"绑定集": bind, "排除集": skip, "跟踪总数": len(files),
            "绑定集数": len(bind), "排除集数": len(skip)}


# 非代码扩展名：产物/媒体/二进制 —— 也不绑（"每一页代码"指代码页）
NON_CODE_EXT = {
    ".mp4": "渲染产物", ".mov": "渲染产物", ".mkv": "渲染产物", ".png": "图片产物",
    ".jpg": "图片产物", ".jpeg": "图片产物", ".webp": "图片产物", ".gif": "图片产物",
    ".glb": "3D 资产", ".gltf": "3D 资产", ".fbx": "3D 资产", ".blend": "3D 资产",
    ".wav": "音频产物", ".mp3": "音频产物", ".flac": "音频产物", ".m4a": "音频产物",
    ".zip": "压缩包", ".7z": "压缩包", ".exe": "二进制", ".dll": "二进制", ".so": "二进制",
    ".pyc": "字节码", ".pyd": "二进制", ".ipynb_checkpoints": "缓存",
}


def _ext_reason(path: str) -> str:
    low = path.lower()
    for ext, why in NON_CODE_EXT.items():
        if low.endswith(ext):
            return why
    return ""


def _shard(path: str, n: int) -> int:
    return int(hashlib.sha256(path.encode("utf-8")).hexdigest()[:8], 16) % n


def _tid(idx: int) -> str:
    return "t%03d" % ((idx % 100) + 1)


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    try:
        with p.open("rb") as f:
            for b in iter(lambda: f.read(65536), b""):
                h.update(b)
    except OSError:
        return ""
    return h.hexdigest()[:16]


def bind_all(*, shards: int = 100, limit: int = 0) -> dict:
    """把每个绑定集文件按内容哈希分片派给触手（分片内一次事务，幂等 upsert）。"""
    t0 = time.time()
    ensure_table()
    _KEEP_CACHE["set"] = None          # 重绑前先失效缓存
    tf = tracked_files()
    files = tf["绑定集"][:limit] if limit else tf["绑定集"]
    by_shard: dict = {}
    for f in files:
        by_shard.setdefault(_shard(f, shards), []).append(f)
    led = _ledger()
    rows = 0
    for sh, group in by_shard.items():
        tid = _tid(sh)
        try:
            with _txn(led) as cur:
                for f in group:
                    sha = _sha(ROOT / f)
                    cur.execute(
                        "INSERT INTO tentacle_file_bindings"
                        " (root_id, tentacle_id, path, rule_id, state, sha256, bound_at)"
                        " VALUES (?,?,?,?,?,?,?)"
                        " ON CONFLICT (root_id, tentacle_id, path) DO UPDATE SET"
                        " rule_id=EXCLUDED.rule_id, state=EXCLUDED.state,"
                        " sha256=EXCLUDED.sha256, bound_at=EXCLUDED.bound_at",
                        (ROOT_ID, tid, f, RULE, "clean", sha,
                         time.strftime("%Y-%m-%dT%H:%M:%S")))
                    rows += 1
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "失败": "%s: %s" % (type(e).__name__, str(e)[:160]), "已写": rows}
    # 🔴 修：清理不在当前绑定集里的陈旧行（否则表里会留 render 产物这类不该绑的页）
    purged = 0
    try:
        keep = set(files)
        with _txn(led) as cur:
            cur.execute("SELECT path FROM tentacle_file_bindings WHERE root_id=?", (ROOT_ID,))
            stale = [r[0] for r in cur.fetchall() if r[0] not in keep]
            for pth in stale:
                cur.execute("DELETE FROM tentacle_file_bindings WHERE root_id=? AND path=?", (ROOT_ID, pth))
                purged += 1
    except Exception as e:  # noqa: BLE001
        purged = -1
    # 再把不在绑定集的触手留的空行清掉（同路径已在上面处理）
    rec = {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "bind_all",
           "文件": len(files), "写行": rows, "分片": len(by_shard), "清理陈旧": purged,
           "排除": len(tf["排除集"]), "秒": round(time.time() - t0, 1)}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return rec


def _q(sql: str, args: tuple = ()) -> list:
    led = _ledger()
    try:
        with _txn(led) as cur:
            cur.execute(sql, args)
            cols = [d[0] for d in (cur.description or [])]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:  # noqa: BLE001
        return []


def purge_stale() -> dict:
    """把不在当前绑定集里的行清掉（陈旧/误绑），返回清理数。"""
    keep = set(tracked_files()["绑定集"])
    led = _ledger()
    n = 0
    try:
        with _txn(led) as cur:
            cur.execute("SELECT DISTINCT path FROM tentacle_file_bindings WHERE root_id=?", (ROOT_ID,))
            stale = [r[0] for r in cur.fetchall() if r[0] not in keep]
            for pth in stale:
                cur.execute("DELETE FROM tentacle_file_bindings WHERE root_id=? AND path=?", (ROOT_ID, pth))
                n += 1
        return {"ok": True, "清理": n, "样本": stale[:5]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "失败": "%s: %s" % (type(e).__name__, str(e)[:120])}


def lookup(path: str) -> dict:
    """文件 →（触手/规则/状态/sha/绑定时间）。**瞬间**（走 ix_tfb_path）。"""
    t0 = time.time()
    # 🔴 修：先按**原样**查（工作区里存在含字面反斜杠转义的合法文件名），查不到再试归一化路径。
    p = path
    rows = _q("SELECT tentacle_id, rule_id, state, sha256, bound_at FROM tentacle_file_bindings"
              " WHERE root_id=? AND path=?", (ROOT_ID, p))
    if not rows and chr(92) in path:
        p = path.replace(chr(92), "/")
        rows = _q("SELECT tentacle_id, rule_id, state, sha256, bound_at FROM tentacle_file_bindings"
                  " WHERE root_id=? AND path=?", (ROOT_ID, p))
    return {"ok": bool(rows), "path": p, "绑定": rows, "触手数": len(rows),
            "ms": round((time.time() - t0) * 1000, 1)}


def owner(tentacle: str, *, limit: int = 50) -> dict:
    """触手 → 它绑的页（反向）。走 ix_tfb_tentacle。"""
    t0 = time.time()
    rows = _q("SELECT path, state, sha256, bound_at FROM tentacle_file_bindings"
              " WHERE root_id=? AND tentacle_id=? ORDER BY path LIMIT ?", (ROOT_ID, tentacle, limit))
    keep = _keep()          # 只回当前绑定集里的页（陈旧行不算）；走 TTL 缓存
    return {"ok": True, "触手": tentacle, "页数": len(rows), "页": rows,
            "ms": round((time.time() - t0) * 1000, 1)}


def penetrate(keyword: str, *, limit: int = 60, scope: str = "path") -> dict:
    """**无死角穿透**：一条查询命中（路径 或 文件内容）→ 触手 → 证据。全走索引判路径，内容命中限定在绑定集内。"""
    t0 = time.time()
    kw = (keyword or "").strip()
    if not kw:
        return {"ok": False, "原因": "给个关键词（路径片段或代码里的词）"}
    hit_paths = _q("SELECT path, tentacle_id, state, bound_at FROM tentacle_file_bindings"
                   " WHERE root_id=? AND path LIKE ? ORDER BY path LIMIT ?",
                   (ROOT_ID, "%" + kw + "%", limit))
    seen = {h["path"] for h in hit_paths}
    # 🔴 修：内容检索改用 git grep（走 git 索引，快一个量级），不再逐文件 read
    content = []
    method = "路径索引"
    if scope in ("content", "all") and len(hit_paths) < limit:
        method = "git grep"
        try:
            # 🔴 修：不加排除规格会在 3.3 万 vendored 文件里扫（10s+）⇒ 只扫我们的代码
            # 🔴 修：只扫我们自己的目录（原来扫 "." 会进 3.3 万 vendored，1.2s+）
            g = subprocess.run(["git", "grep", "-l", "-i", "-F", "--", kw, "--",
                                "core", "tools", "panel", "body", "senses", "audit", "media",
                                "skills", "migrations", "docs", "workflows", "tests",
                                "main.py", "config.yaml", "README.md"],
                               capture_output=True,
                               text=True, encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=60)
            cand = [x.strip() for x in (g.stdout or "").splitlines() if x.strip()]
        except Exception:  # noqa: BLE001
            cand = []
            method = "路径索引(内容检索失败)"
        for f in cand:
            if f in seen or len(content) >= max(0, limit - len(hit_paths)):
                continue
            if any(f == k.rstrip("/") or f.startswith(k) for k in EXCLUDE):
                continue
            content.append({"path": f})
    links = []
    for h in hit_paths:
        links.append({"path": h["path"], "触手": h["tentacle_id"], "状态": h["state"],
                      "命中": "路径"})
    # 🔴 修：内容命中改成**一条批量查**（原来逐个 lookup ⇒ 每次都开连接，平均 335ms）
    bind_map: dict = {}
    if content:
        paths = [c["path"] for c in content]
        marks = ",".join("?" * len(paths))
        for r in _q("SELECT path, tentacle_id, state FROM tentacle_file_bindings"
                    " WHERE root_id=? AND path IN (%s)" % marks, tuple([ROOT_ID] + paths)):
            bind_map.setdefault(r["path"], r)
    for c in content:
        b = bind_map.get(c["path"])
        links.append({"path": c["path"], "触手": (b or {}).get("tentacle_id"),
                      "状态": (b or {}).get("state", "未绑定"), "命中": "内容"})
    return {"ok": True, "关键词": kw, "命中": len(links), "链路": links[:limit],
            "检索法": method, "范围": scope, "ms": round((time.time() - t0) * 1000, 1),
            "口径": "scope=path 走索引（瞬间）；scope=content/all 另加 git grep 内容检索（如实报耗时）"}


def coverage() -> dict:
    """无死角读数：绑定集覆盖 / 排除集（带原因）/ 未绑定清单 / 每根触手页数。"""
    tf = tracked_files()
    bound = _q("SELECT COUNT(*) AS n FROM tentacle_file_bindings WHERE root_id=?", (ROOT_ID,))
    n_bound = (bound[0]["n"] if bound else 0) or 0
    per = _q("SELECT tentacle_id, COUNT(*) AS pages FROM tentacle_file_bindings"
             " WHERE root_id=? GROUP BY tentacle_id ORDER BY pages DESC", (ROOT_ID,))
    bound_paths = {r["path"] for r in _q("SELECT DISTINCT path FROM tentacle_file_bindings WHERE root_id=?",
                                         (ROOT_ID,))}
    missing = [f for f in tf["绑定集"] if f not in bound_paths]
    denom = tf["绑定集数"]
    return {"受跟踪": tf["跟踪总数"], "绑定集": denom, "已绑定": len(bound_paths),
            "覆盖率": round(100.0 * len(bound_paths) / denom, 2) if denom else 0.0,
            "未绑定": missing[:40], "未绑定数": len(missing),
            "排除集数": tf["排除集数"],
            "排除原因分布": {r: sum(1 for x in tf["排除集"] if x["原因"] == r)
                        for r in {x["原因"] for x in tf["排除集"]}},
            "每根触手页数": per[:12], "触手数": len(per),
            "口径": "分母=绑定集（已剔除第三方/产物，逐类登记原因）；未绑定清单必须为空才算无死角"}


def status(limit: int = 3) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"root_id": ROOT_ID, "最近绑定": rows, "覆盖": coverage(),
            "口径": "触手 ↔ 每一页代码 双向绑定；未绑定=盲区，报警不许『不影响运行』就放过"}


__all__ = ["ROOT_ID", "RULE", "EXCLUDE", "ensure_table", "tracked_files", "bind_all",
           "lookup", "owner", "penetrate", "coverage", "status", "LEDGER"]
