# panel/routes_web.py —— 网页抓取三个接口（游标分页 + 双后端）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 游标签名 + 绑定筛选摘要, 换筛选复用旧游标 → 400(前端清空重拉);
#       所有用户值走参数绑定, 只按可信 db.dialect 选 ? / %s;
#       大内容不进响应体, 只给可用标记与受鉴权下载路径

from __future__ import annotations
import base64, hashlib, hmac, json, os, re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

router = APIRouter(prefix="/api/web", tags=["web"])

CURSOR_V  = 1
LIMIT_MAX = 100
ITEMS_MAX = 50

HOST_RE     = re.compile(r"[a-z0-9.-]{1,253}\Z")
RUN_ID_RE   = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
SELECTOR_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}\Z")
TIERS       = {"http", "browser", "stealth"}


# ───────────── 游标：签名 + 绑定筛选摘要 ─────────────
def _cursor_key() -> bytes:
    raw = os.environ.get("WEB_CURSOR_HMAC_KEY", "").strip()
    if len(raw) < 32:
        # fail closed：没有密钥就不提供分页，绝不退化成明文/未签名游标
        raise HTTPException(503, "cursor_key_unavailable")
    return raw.encode()


def _ph(db) -> str:
    return "%s" if db.dialect == "postgres" else "?"


def _utc_iso(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (value.astimezone(timezone.utc)
                 .isoformat(timespec="microseconds").replace("+00:00", "Z"))


def _epoch(value) -> float:
    return datetime.fromisoformat(_utc_iso(value).replace("Z", "+00:00")).timestamp()


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(_cursor_key(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + sig).decode().rstrip("=")


def decode_cursor(token: str, scope: str) -> dict:
    try:
        blob = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        raw, sig = blob[:-32], blob[-32:]
        if not hmac.compare_digest(
                sig, hmac.new(_cursor_key(), raw, hashlib.sha256).digest()):
            raise ValueError("bad signature")
        data = json.loads(raw)
        if data.get("v") != CURSOR_V or data.get("scope") != scope:
            raise ValueError("scope mismatch")      # ★换筛选复用旧游标 → 这里拦住
        return data
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "invalid_cursor")


def _norm_host(value: str) -> str:
    v = value.strip().lower().rstrip(".")
    if not HOST_RE.fullmatch(v):
        raise HTTPException(422, "invalid_host")
    return v


# ───────────── ① 抓取列表 ─────────────
@router.get("/scrapes")
async def list_scrapes(
    host: str | None = None,
    tier: str | None = None,
    matched: bool | None = None,
    from_ts: float | None = None,
    to_ts: float | None = None,
    limit: int = Query(50, ge=1, le=LIMIT_MAX),
    cursor: str | None = None,
    db=Depends(get_db),                     # ← 对齐点：你现有的依赖
):
    host = _norm_host(host) if host else None
    if tier is not None and tier not in TIERS:
        raise HTTPException(422, "invalid_tier")
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(422, "invalid_time_range")

    filters = {"host": host, "tier": tier, "matched": matched,
               "from_ts": from_ts, "to_ts": to_ts}
    scope = hashlib.sha256(
        json.dumps(filters, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    cur = decode_cursor(cursor, scope) if cursor else None

    p = _ph(db)
    clauses, args = [], []

    def add(sql: str, value) -> None:
        clauses.append(sql.replace("?", p)); args.append(value)

    if host is not None:    add("host = ?", host)
    if tier is not None:    add("tier = ?", tier)
    if matched is not None: add("matched = ?", 1 if matched else 0)
    if from_ts is not None: add("created_at >= ?", db.timestamp_param(from_ts))
    if to_ts is not None:   add("created_at <= ?", db.timestamp_param(to_ts))

    if cur:
        ts = db.timestamp_param(cur["created_at"])
        clauses.append(
            f"(created_at < {p} OR (created_at = {p} AND run_id < {p}))")
        args.extend([ts, ts, cur["run_id"]])          # ★keyset：同时间戳靠 run_id 兜底

    sql = ("SELECT run_id, trace_id, tentacle_id, host, tier, result_count, "
           "matched, match_score, status, policy_code, created_at, "
           "selector_id, selector_version FROM web_scrape_runs")
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += f" ORDER BY created_at DESC, run_id DESC LIMIT {p}"

    rows = await db.fetch_all(sql, (*args, limit + 1))   # ★多拿一条判 has_more
    has_more = len(rows) > limit
    rows = rows[:limit]

    items = [{**r, "created_at_ts": _epoch(r["created_at"])} for r in rows]
    next_cursor = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = encode_cursor({
            "v": CURSOR_V, "scope": scope,
            "created_at": _utc_iso(last["created_at"]),
            "run_id": last["run_id"]})

    return {"data": {"items": items, "next_cursor": next_cursor,
                     "has_more": has_more}}


# ───────────── ② 抓取记录详情 ─────────────
@router.get("/scrapes/{run_id}")
async def scrape_detail(run_id: str, db=Depends(get_db)):
    if not RUN_ID_RE.fullmatch(run_id):
        raise HTTPException(422, "invalid_run_id")
    p = _ph(db)

    rows = await db.fetch_all(
        f"SELECT * FROM web_scrape_runs WHERE run_id = {p}", (run_id,))
    if not rows:
        raise HTTPException(404, "scrape_not_found")
    run = rows[0]

    items = await db.fetch_all(
        f"""SELECT position, excerpt, content_json FROM web_scrape_items
            WHERE run_id = {p} ORDER BY position ASC LIMIT {p}""",
        (run_id, ITEMS_MAX))

    # 选择器摘要（前端用 d.selector）
    selector = None
    if run.get("selector_id") and run.get("selector_version") is not None:
        srows = await db.fetch_all(
            f"""SELECT selector_id, selector, version, match_score, last_matched
                FROM web_selector_state
                WHERE domain = {p} AND selector_id = {p} AND version = {p}""",
            (run["host"], run["selector_id"], run["selector_version"]))
        if srows:
            s = srows[0]
            selector = {"selector_id": s["selector_id"], "selector": s["selector"],
                        "version": s["version"], "match_score": s["match_score"],
                        "last_matched": s["last_matched"]}

    return {"data": {
        "run": {**run, "created_at_ts": _epoch(run["created_at"])},
        "items": items,
        "items_truncated": int(run.get("result_count") or 0) > len(items),
        "selector": selector,
        "artifact_available": bool(run.get("artifact_ref")),   # ★只给标记，不给预签名 URL
    }}


# ───────────── ③ 选择器命中详情 ─────────────
@router.get("/selectors/{domain}/{selector_id}")
async def selector_history(
    domain: str,
    selector_id: str,
    limit: int = Query(50, ge=1, le=LIMIT_MAX),
    cursor: int | None = None,
    db=Depends(get_db),
):
    domain = _norm_host(domain)
    if not SELECTOR_RE.fullmatch(selector_id):
        raise HTTPException(422, "invalid_selector_id")
    if cursor is not None and cursor < 1:
        raise HTTPException(422, "invalid_cursor")

    p = _ph(db)
    clauses = [f"domain = {p}", f"selector_id = {p}"]
    args = [domain, selector_id]
    if cursor is not None:
        clauses.append(f"version < {p}")            # 整数 keyset，天然稳定
        args.append(cursor)

    rows = await db.fetch_all(
        f"""SELECT version, selector, properties, last_matched, match_score
            FROM web_selector_state
            WHERE {" AND ".join(clauses)}
            ORDER BY version DESC LIMIT {p}""",
        (*args, limit + 1))

    has_more = len(rows) > limit
    rows = rows[:limit]
    versions = list(reversed(rows))                 # ★升序给前端画趋势图

    return {"data": {
        "selector": rows[0]["selector"] if rows else None,
        "versions": versions,
        "has_more": has_more,
        "next_cursor": rows[-1]["version"] if has_more and rows else None,
    }}
