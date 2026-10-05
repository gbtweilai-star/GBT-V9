# body/witness_probe.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 阈值不可调成"绿"; 单次不可达只记 blocked; 连续失败才升 amber;
#       invalid/disagreement 不走去抖, 立即 critical
from __future__ import annotations
import asyncio, json, os, time

INTERVAL   = int(os.getenv("BODY_WITNESS_PROBE_INTERVAL", 30))   # 采样
TTL        = int(os.getenv("BODY_WITNESS_PROBE_TTL", 60))        # 快照有效期
FAIL_STREAK, FAIL_SECS = 3, 120                                  # 升 amber 条件
OK_STREAK              = 2                                       # 清 amber 条件
CRITICAL_STATES = {"invalid", "disagreement"}
SOFT_STATES     = {"unreachable", "missing", "pending"}
SQL_STATUS = "SELECT * FROM body_witness_status WHERE witness_id=?"


def _parse_iso(v):
    if not v:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _iso_ts(ts) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")


def _iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _apply_status(ledger, witness_id, st: dict, *, first_fail_at_store=None,
                        now_fn=time.time):
    """去抖状态机（**状态落库**，重启不归零）。

    invalid/disagreement 立即 critical（不走去抖，且只报一次）；
    unreachable/missing/pending 需连续 3 次或持续 2 分钟才升 amber；
    恢复需连续 2 次成功；critical 只能人工 ack 清除（见 witness_alerts.acknowledge）。
    """
    row = await ledger.fetch_one(SQL_STATUS, (witness_id,))
    prev = row["persisted_status"] if row else None
    fail = int(row["consecutive_fail"] or 0) if row else 0
    ok = int(row["consecutive_ok"] or 0) if row else 0
    first_fail_at = _parse_iso(row["first_fail_at"]) if row else None
    deg_alerted = int(row["degraded_alerted"] or 0) if row else 0
    crit_alerted = int(row["critical_alerted"] or 0) if row else 0
    now = now_fn()

    if st["status"] == "valid":
        ok, fail = ok + 1, 0
        first_fail_at = None                      # 恢复即清"首次失败时间"
        if ok >= OK_STREAK and (prev in SOFT_STATES or deg_alerted):
            await ledger.record_alert("witness_recovered",
                                      {"witness_id": witness_id}, level="info")
            deg_alerted = 0
    elif st["status"] in CRITICAL_STATES:
        ok = 0
        if not crit_alerted:                      # ★只报一次，避免刷屏
            await ledger.record_alert("witness_critical",
                                      {"witness_id": witness_id,
                                       "status": st["status"],
                                       "detail": st.get("detail")},
                                      level="critical")
            crit_alerted = 1
    else:                                         # 软故障
        fail, ok = fail + 1, 0
        if first_fail_at is None:
            first_fail_at = now                   # 首次失败时间**落库**
        if fail == 1:
            await ledger.record_blocked("witness_probe_transient",
                                        {"witness_id": witness_id,
                                         "error": st.get("detail")})
        elapsed = now - first_fail_at
        if (fail >= FAIL_STREAK or elapsed >= FAIL_SECS) and not deg_alerted:
            await ledger.record_alert("witness_degraded",
                                      {"witness_id": witness_id, "fails": fail,
                                       "elapsed_s": int(elapsed),
                                       "detail": st.get("detail")},
                                      level="warning")
            deg_alerted = 1

    await ledger.execute(
        """INSERT INTO body_witness_status
             (witness_id, provider, kid, persisted_status, live_status, last_verified_at,
              last_ok_at, last_ok_seq, consecutive_fail, consecutive_ok, last_error,
              first_fail_at, degraded_alerted, critical_alerted)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT (witness_id) DO UPDATE SET
             live_status=EXCLUDED.live_status,
             persisted_status=EXCLUDED.persisted_status,
             last_verified_at=EXCLUDED.last_verified_at,
             last_ok_at=COALESCE(EXCLUDED.last_ok_at, body_witness_status.last_ok_at),
             last_ok_seq=COALESCE(EXCLUDED.last_ok_seq, body_witness_status.last_ok_seq),
             consecutive_fail=EXCLUDED.consecutive_fail,
             consecutive_ok=EXCLUDED.consecutive_ok,
             last_error=EXCLUDED.last_error,
             first_fail_at=EXCLUDED.first_fail_at,
             degraded_alerted=EXCLUDED.degraded_alerted,
             critical_alerted=EXCLUDED.critical_alerted""",
        (witness_id, st.get("provider", "-"), st.get("kid"),
         st["status"], st["status"], _iso_now(),
         _iso_now() if st["status"] == "valid" else None,
         st.get("seq"), fail, ok, st.get("detail"),
         _iso_ts(first_fail_at) if first_fail_at else None,
         deg_alerted, crit_alerted))
    return {"fail": fail, "ok": ok, "degraded_alerted": deg_alerted,
            "critical_alerted": crit_alerted}


# 采样循环（probe_loop / verify_all / escalate）在 body.witness_runtime —— 唯一一处实现。
# 本模块只保留**持久化去抖状态机** _apply_status：它把"连续失败/恢复"落在状态行里，
# 重启不归零；身份维度与内容维度各写各的列，互不覆盖。
