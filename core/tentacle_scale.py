# core/tentacle_scale.py —— 亿万级触手：逻辑可寻址 · 实体按需 · 钥匙派生
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「按照源码说的亿万触手，你操控触手也就一句话的事情，
#   所以这个触手数量不妨碍实现。」
#
# 诚实分层（这是能不能真做的分水岭）：
#   · **10^8 个真进程/真文件**：不可能（盘、内存、句柄都不干）—— 谁承诺这个就是吹；
#   · **10^8 个逻辑触手**：可行 —— 地址是**算出来的**（不占盘），记录**分片**落库；
#   · **实体化只在真用到时发生**（一根触手被叫去干活，才给它建随身库/房间/钥匙），
#     闲置可**回收**（可回滚），于是"亿万"只是地址空间的宽度，不是盘的负担；
#   · **钥匙不逐根存**：用主密钥 **HMAC 派生**（同一根永远同一把、彼此不同、可复算），
#     盘上只有一把主密钥 —— 这才叫"亿万根各有独立钥匙"能真落地。
from __future__ import annotations
from core.swallow import swallow as _swallow

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCALE_ROOT = ROOT / "data" / "tentacle_scale"
SHARD_N = 1024                       # 分片数：10^8 根 → 每片约 10 万行（sqlite 很轻松）
ADDR_SPACE = 10 ** 8                 # 逻辑地址空间：1 亿
MASTER_FILE = SCALE_ROOT / "master.secret"


def addr(i: int) -> str:
    """逻辑触手号：t00000001 … t99999999（算出来的，不占盘）。"""
    if not (1 <= int(i) <= ADDR_SPACE):
        raise ValueError("超出地址空间 1..10^8: %s" % i)
    return "t%08d" % int(i)


def parse(a: str) -> int:
    s = str(a).strip().lower().lstrip("t")
    return int(s)


def shard_of(i: int) -> int:
    return int(i) % SHARD_N


def shard_db(i: int) -> Path:
    return SCALE_ROOT / ("shard_%04d.sqlite3" % shard_of(i))


def _master() -> bytes:
    """主密钥：本机落盘一把（非源码字面量）；不存在就生成。"""
    SCALE_ROOT.mkdir(parents=True, exist_ok=True)
    if not MASTER_FILE.is_file():
        MASTER_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            os.chmod(MASTER_FILE, 0o600)
        except Exception as e:
            _swallow(__file__, e)
    return MASTER_FILE.read_text(encoding="utf-8").strip().encode()


def derive_key(i: int) -> str:
    """**派生钥匙**：HMAC(主密钥, 触手号) —— 同号同钥匙、异号必不同、盘上不存。"""
    return hmac.new(_master(), addr(i).encode(), hashlib.sha256).hexdigest()


def key_fingerprint(i: int) -> str:
    return hashlib.sha256(("fleet-key:" + derive_key(i)).encode()).hexdigest()[:16]


def _conn(i: int) -> sqlite3.Connection:
    SCALE_ROOT.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(shard_db(i)))
    c.execute("CREATE TABLE IF NOT EXISTS tentacle("
              "id INTEGER PRIMARY KEY, addr TEXT, profession TEXT, state TEXT,"
              " materialized_at TEXT, last_used TEXT, note TEXT)")
    return c


def spawn(n: int, *, start: int = 1, profession: str = "") -> dict:
    """**一句话加编队**：批量落逻辑记录（每片一次事务，快）。"""
    t0 = time.time()
    by_shard: dict = {}
    for i in range(start, start + int(n)):
        by_shard.setdefault(shard_of(i), []).append(i)
    total = 0
    for sh, ids in by_shard.items():
        c = _conn(ids[0])
        try:
            c.executemany("INSERT OR IGNORE INTO tentacle(id, addr, profession, state, note)"
                          " VALUES (?,?,?,?,?)",
                          [(i, addr(i), profession, "逻辑", "") for i in ids])
            c.commit()
            total += len(ids)
        finally:
            c.close()
    dt = time.time() - t0
    return {"ok": True, "新增": total, "片数": len(by_shard), "秒": round(dt, 2),
            "吞吐": int(total / dt) if dt else None}


def materialize(a: str, *, note: str = "") -> dict:
    """**按需实体化**：这根真被用了，才给它建随身库（+ 派生钥匙）。"""
    i = parse(a)
    c = _conn(i)
    try:
        c.execute("INSERT OR IGNORE INTO tentacle(id, addr, state) VALUES (?,?,?)", (i, addr(i), "逻辑"))
        c.execute("UPDATE tentacle SET state='已实体化', materialized_at=?, note=? WHERE id=?",
                  (time.strftime("%Y-%m-%dT%H:%M:%S"), note[:200], i))
        c.commit()
    finally:
        c.close()
    store = None
    try:
        from core import tentacle_store as TS
        st = TS.provision(addr(i))
        store = st.get("库")
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "触手": addr(i), "片": shard_of(i), "随身库": store,
            "钥匙指纹": key_fingerprint(i), "实体化于": time.strftime("%Y-%m-%dT%H:%M:%S")}


def touch(a: str) -> dict:
    i = parse(a)
    c = _conn(i)
    try:
        c.execute("UPDATE tentacle SET last_used=? WHERE id=?",
                  (time.strftime("%Y-%m-%dT%H:%M:%S"), i))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "触手": addr(i)}


def reclaim(idle_days: float = 0.0, *, limit: int = 500) -> dict:
    """**回收**：闲置的实体化触手退回逻辑态（顺手可删其随身库，可回滚）。"""
    cut = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - idle_days * 86400))
    done = 0
    for p in sorted(SCALE_ROOT.glob("shard_*.sqlite3")):
        try:
            c = sqlite3.connect(str(p))
            rows = c.execute("SELECT id FROM tentacle WHERE state='已实体化'"
                             " AND (last_used IS NULL OR last_used < ?) LIMIT ?",
                             (cut, limit)).fetchall()
            for (i,) in rows:
                c.execute("UPDATE tentacle SET state='逻辑' WHERE id=?", (i,))
                done += 1
            c.commit()
            c.close()
        except Exception:  # noqa: BLE001
            continue
    return {"ok": True, "回收": done, "口径": "退回逻辑态（随身库可另行清理，可回滚）"}


def stats() -> dict:
    files = sorted(SCALE_ROOT.glob("shard_*.sqlite3"))
    logical = materialized = 0
    for p in files:
        try:
            c = sqlite3.connect(str(p))
            logical += c.execute("SELECT count(*) FROM tentacle").fetchone()[0]
            materialized += c.execute("SELECT count(*) FROM tentacle WHERE state='已实体化'").fetchone()[0]
            c.close()
        except Exception:  # noqa: BLE001
            continue
    bytes_ = sum(p.stat().st_size for p in files) + (MASTER_FILE.stat().st_size if MASTER_FILE.is_file() else 0)
    return {"逻辑触手": logical, "已实体化": materialized, "分片文件": len(files),
            "盘占MB": round(bytes_ / 1048576, 2), "地址空间": ADDR_SPACE, "每片上限": SHARD_N,
            "口径": "地址是算出来的（不占盘）；实体只在真用到时落；钥匙 HMAC 派生（盘上只存一把主密钥）"}


def one_liner(a: str, task: str, *, tenant: str = "") -> dict:
    """**一句话操控**：寻址 → 实体化 → 交给万能插/牢房跑 → 复述结果。"""
    i = parse(a)
    m = materialize(addr(i), note=task[:120])
    from core import full_power as FP
    # ★ 云 + 本地都要轮到（云慢/不成时不许空手——判据+换人）
    r = FP.fire(task, tenant=(tenant or addr(i)), max_models=5)
    touch(addr(i))
    return {"触手": addr(i), "实体化": m, "结果": {k: r.get(k) for k in
            ("拿到实质产出", "产出模型", "吐出人", "吐出内容", "全军拒", "试了几手")}}


__all__ = ["ADDR_SPACE", "SHARD_N", "addr", "parse", "shard_of", "shard_db", "derive_key",
           "key_fingerprint", "spawn", "materialize", "touch", "reclaim", "stats", "one_liner"]
