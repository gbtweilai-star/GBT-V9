# core/tentacle_store.py —— 每根触手的**随身存储**（可有可无的东西一丢，主脑批量慢看）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「数据库也是一样，一根触手自己重新去配备一个随身的存储东西方便，
#   有些东西可有可无但是也要向主脑汇报，这时候节省能源直接把东西往数据库一丢，主脑慢慢看不费事。」
#
# 落地成三条：
#   ① **随身**：一根触手一个 SQLite 文件 data/tentacle_store/<触手>.sqlite3 —— 能单独拷走；
#   ② **一丢**：stash() 随手存（备忘/中间物/可有可无）；report() 落"汇报"（**必须让主脑知道的**）；
#   ③ **慢看不费事**：inbox() 主脑**批量**取未读（一次一条 SQL，不打断、不等回执），mark_read() 标已读。
# 能源口径：丢库是**本地写**（几乎不耗）；只有"真需要主脑当场决策"的才升级为上报。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STORE_ROOT = ROOT / "data" / "tentacle_store"
LEVELS = ("备忘", "常规", "重要", "紧急")      # 升级口径：紧急才可能打断主脑

_DDL = (
    """CREATE TABLE IF NOT EXISTS kv(
         k TEXT PRIMARY KEY, v TEXT, kind TEXT DEFAULT '备忘', at TEXT);""",
    """CREATE TABLE IF NOT EXISTS reports(
         id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, level TEXT, title TEXT,
         body TEXT, payload TEXT, read INTEGER DEFAULT 0, read_at TEXT);""",
    """CREATE TABLE IF NOT EXISTS drops(
         id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, name TEXT, sha256 TEXT,
         bytes INTEGER, note TEXT);""",
)


def path(tentacle: str) -> Path:
    return STORE_ROOT / ("%s.sqlite3" % tentacle)


def provision(tentacle: str) -> dict:
    """给一根触手配备随身库（幂等）。"""
    STORE_ROOT.mkdir(parents=True, exist_ok=True)
    p = path(tentacle)
    fresh = not p.is_file()
    con = sqlite3.connect(str(p))
    try:
        for ddl in _DDL:
            con.execute(ddl)
        con.commit()
    finally:
        con.close()
    return {"ok": True, "tentacle": tentacle, "库": str(p.relative_to(ROOT)), "新配": fresh,
            "字节": p.stat().st_size}


def provision_fleet(n: int = 100) -> dict:
    ok = 0
    for i in range(1, n + 1):
        try:
            if provision("t%03d" % i).get("ok"):
                ok += 1
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": ok == n, "配备": ok, "共": n, "目录": str(STORE_ROOT.relative_to(ROOT))}


def _con(t: str):
    provision(t)
    return sqlite3.connect(str(path(t)))


def stash(tentacle: str, key: str, value, kind: str = "备忘", ttl: str = "") -> dict:
    """② 一丢：随手存进自己的库（可有可无的都往这儿丢，不打扰主脑）。"""
    con = _con(tentacle)
    try:
        con.execute("INSERT OR REPLACE INTO kv(k, v, kind, at) VALUES (?,?,?,?)",
                    (key, json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value,
                     kind, time.strftime("%Y-%m-%dT%H:%M:%S")))
        con.commit()
        row = con.execute("SELECT count(*) FROM kv").fetchone()[0]
    finally:
        con.close()
    return {"ok": True, "tentacle": tentacle, "键": key, "库内条数": row}


def report(tentacle: str, title: str, body: str = "", level: str = "常规", payload=None) -> dict:
    """② 汇报：**必须让主脑知道**的往 reports 丢（标未读，等主脑批量看）。"""
    lv = level if level in LEVELS else "常规"
    con = _con(tentacle)
    try:
        cur = con.execute("INSERT INTO reports(at, level, title, body, payload, read) VALUES (?,?,?,?,?,0)",
                          (time.strftime("%Y-%m-%dT%H:%M:%S"), lv, title[:200], body[:2000],
                           json.dumps(payload, ensure_ascii=False, default=str) if payload else ""))
        con.commit()
        rid = cur.lastrowid
        unread = con.execute("SELECT count(*) FROM reports WHERE read=0").fetchone()[0]
    finally:
        con.close()
    return {"ok": True, "tentacle": tentacle, "汇报号": rid, "级别": lv, "该库未读": unread,
            "口径": "不打断主脑；主脑 inbox() 批量慢看"}


def drop(tentacle: str, name: str, sha256: str = "", bytes_: int = 0, note: str = "") -> dict:
    """中间物/产物登记（不把大文件塞库，只登记指纹，省空间省能源）。"""
    con = _con(tentacle)
    try:
        con.execute("INSERT INTO drops(at, name, sha256, bytes, note) VALUES (?,?,?,?,?)",
                    (time.strftime("%Y-%m-%dT%H:%M:%S"), name[:200], sha256[:32], int(bytes_ or 0), note[:300]))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "tentacle": tentacle, "登记": name}


def inbox(*, level: str = "", unread_only: bool = True, limit: int = 300, tentacles: list | None = None) -> dict:
    """③ **主脑慢看不费事**：一次 SQL 批量取未读汇报（跨库），读完再标已读。"""
    rows = []
    names = tentacles or [p.stem for p in sorted(STORE_ROOT.glob("t*.sqlite3"))]
    for t in names:
        if not path(t).is_file():
            continue
        try:
            con = sqlite3.connect(str(path(t)))
            q = "SELECT id, at, level, title, body, read FROM reports WHERE 1=1"
            args = []
            if unread_only:
                q += " AND read=0"
            if level:
                q += " AND level=?"
                args.append(level)
            q += " ORDER BY id DESC LIMIT ?"
            args.append(int(limit))
            for r in con.execute(q, args).fetchall():
                rows.append({"触手": t, "号": r[0], "at": r[1], "级别": r[2], "标题": r[3],
                             "正文": (r[4] or "")[:300], "已读": bool(r[5])})
            con.close()
        except Exception:  # noqa: BLE001
            continue
    lv_order = {l: i for i, l in enumerate(LEVELS)}
    rows.sort(key=lambda x: (-lv_order.get(x["级别"], 1), x["触手"]))
    return {"未读条数": len(rows), "汇报": rows[:limit],
            "口径": "批量取，不打断；紧急级别才值得当场叫主脑"}


def mark_read(items: list) -> dict:
    n = 0
    for it in items or []:
        t = it.get("触手") if isinstance(it, dict) else None
        rid = it.get("号") if isinstance(it, dict) else None
        if not (t and rid):
            continue
        try:
            con = sqlite3.connect(str(path(t)))
            con.execute("UPDATE reports SET read=1, read_at=? WHERE id=?",
                        (time.strftime("%Y-%m-%dT%H:%M:%S"), rid))
            con.commit()
            con.close()
            n += 1
        except Exception:  # noqa: BLE001
            continue
    return {"ok": True, "标已读": n}


def status() -> dict:
    files = sorted(STORE_ROOT.glob("t*.sqlite3")) if STORE_ROOT.is_dir() else []
    total_kv = total_rep = unread = total_drop = 0
    sizes = 0
    for p in files:
        sizes += p.stat().st_size
        try:
            con = sqlite3.connect(str(p))
            total_kv += con.execute("SELECT count(*) FROM kv").fetchone()[0]
            total_rep += con.execute("SELECT count(*) FROM reports").fetchone()[0]
            unread += con.execute("SELECT count(*) FROM reports WHERE read=0").fetchone()[0]
            total_drop += con.execute("SELECT count(*) FROM drops").fetchone()[0]
            con.close()
        except Exception:  # noqa: BLE001
            continue
    return {"配备了": len(files), "目录": str(STORE_ROOT.relative_to(ROOT)),
            "总字节": sizes, "随手存": total_kv, "汇报": total_rep, "未读": unread, "产物登记": total_drop}


def run(op: str = "status", **kw) -> dict:
    if op in ("status", "report", ""):
        return status()
    if op == "provision":
        return provision(kw.get("tentacle", "t001"))
    if op == "provision_fleet":
        return provision_fleet(int(kw.get("n") or 100))
    if op == "stash":
        return stash(kw.get("tentacle", "t001"), kw.get("key", "k"), kw.get("value", ""), kw.get("kind", "备忘"))
    if op == "report":
        return report(kw.get("tentacle", "t001"), kw.get("title", ""), kw.get("body", ""),
                      kw.get("level", "常规"), kw.get("payload"))
    if op == "inbox":
        return inbox(level=kw.get("level", ""), unread_only=bool(kw.get("unread_only", True)))
    return {"ok": False, "error": "unknown operation: %s" % op}


__all__ = ["STORE_ROOT", "LEVELS", "path", "provision", "provision_fleet", "stash", "report",
           "drop", "inbox", "mark_read", "status", "run"]
