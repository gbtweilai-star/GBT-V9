# body/snapshots.py
TREND_METRICS = {
    "devour": lambda p: {"frames": p["frames"], "gaps": p["gap_intervals"],
                         "recovered": p["recovered_frames"],
                         "cache_ratio": (p.get("cache") or {}).get("ratio"),
                         "pending_bytes": (p.get("archive") or {}).get("pending_bytes")},
    "scan":   lambda p: {"coverage": p["coverage"], "scanned": p["scanned"],
                         "gaps": p["gaps"], "findings": p["findings"]},
    "queue":  lambda p: {"depth": p["depth"], "wait_p50_s": p["wait_p50_s"],
                         "failure_rate": p["failure_rate"], "dead": p["dead"],
                         "vram_used_mb": (p.get("vram") or {}).get("used_mb")},
}
HISTORY_DAYS = int(os.getenv("BODY_SNAPSHOT_HISTORY_DAYS", 30))

# publish_snapshot() 里追加（同一事务）：
#   INSERT INTO body_snapshot_history (domain, revision, observed_at, metrics_json,
#       evidence_json) VALUES (?,?,?,?,?) ON CONFLICT DO NOTHING
# 清理由 recheck leader 每天跑一次：
#   DELETE FROM body_snapshot_history WHERE observed_at < <now - HISTORY_DAYS>

@router.get("/snapshots/{domain}/detail")
async def snapshot_detail(domain: str, revision: int | None = None, db=Depends(get_db)):
    row = (await fetch_all(db, """SELECT * FROM body_snapshot_history
        WHERE domain=? AND (? IS NULL OR revision=?) ORDER BY revision DESC LIMIT 1""",
        [domain, revision, revision], db=db))
    if not row:
        raise HTTPException(404, "revision_not_found")
    r = row[0]
    ev = json.loads(r["evidence_json"])
    # 证据可用性：窗口内原始行还在不在（过了保留期就标不可复算）
    for s in ev.get("sources", []):
        if s.get("table") and s.get("id") is not None:
            alive = (await fetch_all(db, f"SELECT 1 FROM {WHITELIST[s['table']]} "
                                         f"WHERE id=? LIMIT 1", [s["max_id"]], db=db))
            s["available"] = bool(alive)
    return {"domain": domain, "revision": r["revision"], "observed_at": r["observed_at"],
            "producer": ..., "evidence": ev,
            "recomputable": all(s.get("available", True) for s in ev.get("sources", []))}


@router.post("/snapshots/{domain}/recompute")
async def recompute(domain: str, revision: int, db=Depends(get_db)):
    """★只重算白名单 query_id，且必须用【当时同一个窗口】——否则数字必然不一样。"""
    from body.producers import devour, scan, queue
    COLLECT = {"devour": devour.collect, "scan": scan.collect, "queue": queue.collect}
    hist = (await fetch_all(db, "SELECT * FROM body_snapshot_history WHERE domain=? "
                                "AND revision=?", [domain, revision], db=db))[0]
    ev = json.loads(hist["evidence_json"])
    fresh = await COLLECT[domain](db, state=app.state, window_override=ev.get("window"),
                                  dry_run=True)                 # ★不发布
    stored = json.loads((await fetch_all(db, "SELECT payload_json FROM body_read_snapshots "
                                             "WHERE domain=?", [domain], db=db))[0]["payload_json"])
    diff = {k: [stored.get(k), fresh.get(k)] for k in set(stored) | set(fresh)
            if stored.get(k) != fresh.get(k)}
    await db.execute("""INSERT INTO read_tool_audit (call_id, session_id, tool, domain,
            revision, params_digest, spoken_text, ok, at) VALUES (?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
        (uuid.uuid4().hex, "panel", f"recompute:{domain}", domain, revision,
         digest({"window": ev.get("window")}), json.dumps(diff, ensure_ascii=False),
         int(not diff)))
    return {"matched": not diff, "diff": diff, "window": ev.get("window")}
