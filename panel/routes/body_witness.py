# panel/routes/body_witness.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from fastapi import APIRouter, Depends, HTTPException
from common.db import get_db, fetch_all
import os
import time

router = APIRouter(prefix="/api/body/witnesses")


def _age_seconds(observed_at) -> float | None:
    """快照年龄（秒）。时间格式统一走 common.timeutil（带 Z 的 UTC）。"""
    if not observed_at:
        return None
    try:
        from common.timeutil import to_epoch
        return max(0.0, time.time() - to_epoch(observed_at))
    except Exception:
        return None


@router.get("")
async def witnesses(db=Depends(get_db)):
    """顶层数字取自快照；新鲜度显式返回，前端据此决定是否还能用绿色。"""
    probe = (await fetch_all(db, "SELECT * FROM body_witness_probe WHERE id=1", [], db=db))
    p = probe[0] if probe else None
    age = _age_seconds(p["observed_at"]) if p else None
    ttl = int(os.getenv("BODY_WITNESS_PROBE_TTL", 60))
    rows = await fetch_all(db, """SELECT witness_id, provider, kid, persisted_status,
            live_status, last_verified_at, last_ok_at, last_ok_seq,
            live_from_seq, backfilled_through_seq, consecutive_fail, last_error,
            identity_status, evidence_level, content_status, vote_eligible,
            isolated, identity_error_code
        FROM body_witness_status ORDER BY witness_id""", [], db=db)
    sealed = await fetch_all(db, """SELECT seq, head_hash, at FROM body_anchors
        WHERE sealed=1 ORDER BY seq DESC LIMIT 1""", [], db=db)
    # ★顶层数字与数字人工具必须同源：都读 witness_snapshot 单行（v10 建）
    snap = (await fetch_all(db, "SELECT * FROM witness_snapshot WHERE id=1", [], db=db))
    snap = snap[0] if snap else None
    snap_age = _age_seconds(snap["observed_at"]) if snap else None
    return {
        "quorum": {"valid": p["quorum_valid"] if p else 0,
                   "required": p["required"] if p else int(os.getenv("BODY_WITNESS_REQUIRED_EXTERNAL", 2)),
                   "status": (p["status"] if p else "unknown")},
        "probe": {"observed_at": p["observed_at"] if p else None,
                  "age_seconds": age, "ttl_seconds": ttl,
                  "stale": (age is None or age > ttl)},
        # 证据等级视图：每个见证的身份状态 / 证据等级 / 这一票算不算 / 不算的原因
        "evidence": {
            "revision": snap["revision"] if snap else None,
            "observed_at": snap["observed_at"] if snap else None,
            "age_seconds": snap_age,
            "stale": (snap_age is None or snap_age > ttl),
            "valid_count": snap["valid_count"] if snap else None,
            "required": snap["required"] if snap else None,
            "status": snap["status"] if snap else "unknown",
            "witnesses": [{"witness_id": r["witness_id"],
                           "identity_status": r["identity_status"],
                           "evidence_level": r["evidence_level"],
                           "content_status": r["content_status"],
                           "vote_eligible": (None if r["vote_eligible"] is None
                                             else bool(r["vote_eligible"])),
                           "isolated": bool(r["isolated"]),
                           "reason_code": r["identity_error_code"]}
                          for r in rows],
        },
        "latest_sealed": sealed[0] if sealed else None,      # 历史事实，前端要标明"当时"
        "witnesses": rows,
    }


@router.get("/coverage")
async def coverage(db=Depends(get_db)):
    """每个见证的段 + 全局按有效见证数着色的段。段由真实锚点范围切出，不假设连续。"""
    req = int(os.getenv("BODY_WITNESS_REQUIRED_EXTERNAL", 2))
    per = await fetch_all(db, """SELECT w.witness_id, w.seq, w.status, s.live_from_seq,
            s.backfilled_through_seq
        FROM body_anchor_witnesses w
        LEFT JOIN body_witness_status s ON s.witness_id = w.witness_id
        ORDER BY w.witness_id, w.seq""", [], db=db)
    global_rows = await fetch_all(db, """SELECT a.seq,
            COUNT(DISTINCT CASE WHEN w.status='valid' THEN w.witness_id END) AS n_valid
        FROM body_anchors a
        LEFT JOIN body_anchor_witnesses w ON w.anchor_uid = a.anchor_uid
        GROUP BY a.seq ORDER BY a.seq""", [], db=db)
    head_seq = (await fetch_all(db, "SELECT COALESCE(MAX(seq),0) AS s FROM registration",
                                [], db=db))[0]["s"]
    return {"required": req, "head_seq": head_seq,
            "per_witness": _group_segments(per),
            "global": _segments([{"seq": r["seq"], "kind": "live" if r["n_valid"] >= req else
                                  ("partial" if r["n_valid"] > 0 else "gap")}
                                 for r in global_rows], head_seq)}


@router.get("/{witness_id}/evidence")
async def evidence(witness_id: str, limit: int = 20, db=Depends(get_db)):
    """下钻：证据不含任何凭据，只有对象 key / kid / 签名结果 / 读回时间 / 错误。"""
    rows = await fetch_all(db, """SELECT w.anchor_uid, w.seq, w.kid, w.status, w.object_key,
            w.sig_ok, w.verified_at, w.read_at, w.error, w.head_hash,
            a.sealed, a.head_hash AS anchor_head_hash
        FROM body_anchor_witnesses w
        LEFT JOIN body_anchors a ON a.anchor_uid = w.anchor_uid
        WHERE w.witness_id = ? ORDER BY w.seq DESC LIMIT ?""", [witness_id, min(limit, 200)], db=db)
    if not rows:
        raise HTTPException(404, "witness_not_found")
    return {"witness_id": witness_id, "items": rows}


def _segments(points: list[dict], head_seq: int) -> list[dict]:
    """把逐点读数合并成连续段。断点即断段 —— 不假设区间内部连续。"""
    out, cur = [], None
    for p in sorted(points, key=lambda x: int(x["seq"])):
        if cur and cur["kind"] == p["kind"] and p["seq"] == cur["to"] + 1:
            cur["to"] = p["seq"]
        else:
            if cur:
                out.append(cur)
            cur = {"kind": p["kind"], "from": p["seq"], "to": p["seq"]}
    if cur:
        out.append(cur)
    return out


def _group_segments(per: list[dict]) -> list[dict]:
    """按 witness 分组出覆盖段。

    段落类型（kind 白名单固定三元 + 回填）：
      backfill  seq <= backfilled_through_seq  —— 仅回填，**不计实时 quorum**
      live      实时区间内且 status=valid
      partial   实时区间内但 status != valid（见证不足，必须看得见）
      gap       既不在回填区间也不在实时区间
    """
    by_w: dict[str, list] = {}
    for r in per:
        by_w.setdefault(r["witness_id"], []).append(r)
    out = []
    for wid, rows in sorted(by_w.items()):
        pts = []
        for r in rows:
            seq = int(r["seq"])
            back = r.get("backfilled_through_seq")
            live_from = r.get("live_from_seq")
            if back is not None and seq <= int(back):
                kind = "backfill"
            elif live_from is not None and seq >= int(live_from):
                kind = "live" if r.get("status") == "valid" else "partial"
            else:
                kind = "gap"
            pts.append({"seq": seq, "kind": kind})
        head = max((p["seq"] for p in pts), default=0)
        out.append({"witness_id": wid, "head_seq": head,
                    "segments": _segments(pts, head)})
    return out
