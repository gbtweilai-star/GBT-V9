# panel/routes/body.py
import base64, hashlib, hmac, json, os
from fastapi import APIRouter, Depends, HTTPException, Query
from common.db import get_db, fetch_all, dialect          # ← 对齐点 D: 你现成的读路径

router = APIRouter(prefix="/api/body")
EVENT_TYPES = ("scan", "change", "fix", "harden")
_SIG = os.environ.get("PANEL_CURSOR_KEY", "body-cursor").encode()
LIMIT_MAX = 200

def _filter_digest(types, extra=""):
    return hashlib.sha256(json.dumps([sorted(types or []), extra],
                                     separators=(",", ":")).encode()).hexdigest()[:16]

def _enc_cursor(sort_key, fdig):
    raw = json.dumps({"k": sort_key, "f": fdig}, separators=(",", ":"))
    sig = hmac.new(_SIG, raw.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}|{sig}".encode()).decode().rstrip("=")

def _dec_cursor(cur, fdig):
    if not cur:
        return None
    try:
        raw, sig = base64.urlsafe_b64decode(cur + "=" * (-len(cur) % 4)).decode().rsplit("|", 1)
        if not hmac.compare_digest(sig, hmac.new(_SIG, raw.encode(), hashlib.sha256).hexdigest()[:16]):
            raise ValueError("cursor_sig")
        d = json.loads(raw)
        if d.get("f") != fdig:
            raise ValueError("cursor_filter_reused")     # 换筛选复用旧游标 → 拒
        return d["k"]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"cursor_invalid:{e}")


@router.get("/chain")
async def body_chain(cursor: str = None, limit: int = 50,
                     types: str = None, db=Depends(get_db)):
    limit = max(1, min(int(limit or 50), LIMIT_MAX))
    tlist = [t for t in (types.split(",") if types else []) if t in EVENT_TYPES]
    fdig = _filter_digest(tlist)
    try:
        after = _dec_cursor(cursor, fdig)            # 记的是上一页最后一条的 seq
    except ValueError as e:
        raise HTTPException(400, str(e))

    ph = lambda n: ",".join(["?"] * n) if dialect() == "sqlite" else ",".join(["%s"] * n)
    args, where = [], []
    if tlist:
        where.append(f"event_type IN ({ph(len(tlist))})"); args += tlist
    if after is not None:
        where.append("seq < " + ph(1)); args.append(int(after))
    wsql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = await fetch_all(db, f"""SELECT seq, trace_id, event_type, actor_tentacle,
            created_at, targets_hash, prev_hash, event_hash,
            (SELECT COUNT(*) FROM registration_targets t WHERE t.seq=registration.seq) AS targets
        FROM registration {wsql} ORDER BY seq DESC LIMIT {ph(1)}""", args + [limit + 1], db=db)

    has_more = len(rows) > limit
    rows = rows[:limit]
    nxt = _enc_cursor(rows[-1]["seq"], fdig) if (has_more and rows) else None
    return {"items": rows, "next_cursor": nxt, "has_more": has_more,
            "filter_digest": fdig, "dialect": dialect()}


@router.get("/chain/{seq}")
async def body_chain_one(seq: int, db=Depends(get_db)):
    row = (await fetch_all(db, "SELECT * FROM registration WHERE seq = ?", [seq], db=db))
    if not row:
        raise HTTPException(404, "seq_not_found")
    tg = await fetch_all(db, """SELECT path, before_hash, after_hash
        FROM registration_targets WHERE seq = ? ORDER BY path""", [seq], db=db)
    from body.hash import event_hash as _eh, targets_hash as _th
    return {"registration": row[0],
            "targets": tg,
            "verify": {"event_hash_match":
                           _eh(row[0]["seq"], row[0]["event_type"], row[0]["actor_tentacle"],
                               row[0]["created_at"], row[0]["payload_json"],
                               row[0]["targets_hash"], row[0]["prev_hash"]) == row[0]["event_hash"],
                       "targets_hash_match": _th(tg) == row[0]["targets_hash"]}}


@router.get("/coverage")
async def body_coverage(db=Depends(get_db)):
    """卡片主读数：链头/链健康/事件分布/触手覆盖排名/索引状态。全部真读数。"""
    head = (await fetch_all(db, """SELECT
            (SELECT MAX(seq) FROM registration)                        AS head_seq,
            (SELECT COUNT(*)  FROM registration)                       AS total,
            COALESCE(SUM(CASE WHEN event_type='scan'   THEN 1 ELSE 0 END),0) AS n_scan,
            COALESCE(SUM(CASE WHEN event_type='change' THEN 1 ELSE 0 END),0) AS n_change,
            COALESCE(SUM(CASE WHEN event_type='fix'    THEN 1 ELSE 0 END),0) AS n_fix,
            COALESCE(SUM(CASE WHEN event_type='harden' THEN 1 ELSE 0 END),0) AS n_harden
        FROM registration""", [], db=db))[0]
    per_tentacle = await fetch_all(db, """SELECT tentacle_id, COUNT(*) AS pages
        FROM tentacle_file_bindings GROUP BY tentacle_id ORDER BY pages DESC LIMIT 50""", [], db=db)
    idx = (await fetch_all(db, """SELECT
            COALESCE(SUM(CASE WHEN state='clean'   THEN 1 ELSE 0 END),0) AS clean,
            COALESCE(SUM(CASE WHEN state='dirty'   THEN 1 ELSE 0 END),0) AS dirty,
            COALESCE(SUM(CASE WHEN state='missing' THEN 1 ELSE 0 END),0) AS missing
        FROM body_files""", [], db=db))[0]
    man = await fetch_all(db, "SELECT * FROM brain_boot_manifest WHERE root_id = ?",
                          ["main"], db=db)
    return {"head": head, "per_tentacle": per_tentacle, "index": idx,
            "manifest": man[0] if man else None}


@router.get("/tentacle/{tentacle_id}/pages")
async def tentacle_pages(tentacle_id: str, cursor: str = None, q: str = None,
                         limit: int = 100, db=Depends(get_db)):
    limit = max(1, min(int(limit or 100), 500))
    fdig = _filter_digest(None, extra=f"{tentacle_id}|{(q or '').strip()}")
    try:
        after = _dec_cursor(cursor, fdig)
    except ValueError as e:
        raise HTTPException(400, str(e))
    args = [tentacle_id]
    wsql = "WHERE tentacle_id = ?"
    if q:
        wsql += " AND path LIKE ?"; args.append(f"%{q}%")
    if after:
        wsql += " AND path > ?"; args.append(after)
    rows = await fetch_all(db, f"""SELECT path, rule_id FROM tentacle_file_bindings
        {wsql} ORDER BY path ASC LIMIT {limit + 1}""", args, db=db)
    has_more = len(rows) > limit
    rows = rows[:limit]
    return {"items": rows, "has_more": has_more,
            "next_cursor": _enc_cursor(rows[-1]["path"], fdig) if has_more and rows else None}
