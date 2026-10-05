# body/witness_alerts.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 告警写入【永不被 freeze 拦】—— 冻结账本不能把报警本身也冻住;
#       去重键必须含 witness_id, 否则三个见证的同类故障被压成一条;
#       身份故障默认【不】封冻写路径
from __future__ import annotations
import json, os, time
from body.identity import E3, Err, countable
# 运行期助手统一由 witness_runtime 提供（一份实现，状态机与语音读同一套判定）
from body.witness_runtime import (acked as _acked, bump_fail as _bump_fail,
                                  bump_ok as _bump_ok, fresh as _fresh,
                                  ident_domain_fp as _ident_domain_fp,
                                  overdue as _overdue, _iso_now)

#                    kind                                level      去抖  class
ID_ALERT = {
    Err.CONFIG_IDENTITY_MISMATCH:    ("witness_identity_config_mismatch", "warning",  False, "config"),
    Err.CONFIG_OWNER_MISMATCH:       ("witness_identity_config_mismatch", "warning",  False, "config"),
    Err.AUTHORITY_IDENTITY_CONFLICT: ("witness_identity_conflict",        "critical", False, "authority"),
    Err.IDENTITY_UNVERIFIED:         ("witness_identity_unverified",      "warning",  True,  "evidence"),
    Err.IDENTITY_FIELD_FORBIDDEN:    ("witness_identity_unverified",      "warning",  True,  "evidence"),
    Err.IDENTITY_FIELD_UNSUPPORTED:  ("witness_identity_unverified",      "warning",  True,  "evidence"),
    Err.UNREACHABLE:                 ("witness_identity_unreachable",     "warning",  True,  "transport"),
    Err.AUTH_FAILED:                 ("witness_identity_auth_failed",     "warning",  True,  "transport"),
    # 身份正常但这一票没算上：必须单独开单，否则"掉票"没有任何可见原因
    # 等级不足是**确定性**条件（配置不变就不会闪）→ 立即开单，不走去抖
    Err.EVIDENCE_BELOW_REQUIRED:     ("witness_identity_evidence_below",  "warning",  False, "evidence"),
    Err.CONTENT_NOT_VERIFIED:        ("witness_content_unverified",       "warning",  True,  "content"),
}
# NOT_LIVE_YET / CREDENTIAL_DOMAIN_DUPLICATE 不在这里：前者是回填窗口内的正常态，
# 后者由 witness_probe_once 统一开一条 critical（凭据域重复是全体的故障，不是单见证的）
IDENTITY_OK = {"verified", "attested"}          # 可计票的身份状态
# 只有"权威冲突"值得隔离该见证; 全局封冻写路径必须显式配置, 默认 none
FREEZE_POLICY = os.getenv("BODY_WITNESS_FREEZE_ON_IDENTITY", "authority")  # authority|all|none
STALE_GRACE_MULT = 2                            # TTL×2 之内不告警, 之后才去抖升 degraded


def dedup_key(witness_id, kind, dimension, config_gen) -> str:
    return f"{dimension}:{witness_id}:{kind}:{config_gen or '-'}"


async def _emit(ledger, witness_id, kind, level, key, payload, *, now):
    """去重开单：同一 key 只开一次，重复轮询只累加计数与 last_seen。"""
    row = await ledger.fetch_one(
        "SELECT alert_keys_json FROM body_witness_status WHERE witness_id=?", (witness_id,))
    keys = json.loads((row or {}).get("alert_keys_json") or "{}")
    if key in keys:
        keys[key].update(count=keys[key].get("count", 1) + 1, last_seen=now)
        opened = False
    else:
        keys[key] = {"opened_at": now, "last_seen": now, "count": 1, "kind": kind}
        opened = True
    await ledger.execute("UPDATE body_witness_status SET alert_keys_json=? WHERE witness_id=?",
                         (json.dumps(keys, sort_keys=True), witness_id))
    if opened:
        # ★ bypass_freeze：告警通路必须走得通，哪怕账本被冻结
        await ledger.record_alert(kind, {**payload, "witness_id": witness_id,
                                         "dedup_key": key}, level=level, bypass_freeze=True)
    return opened


async def _close(ledger, witness_id, kind, dimension, config_gen, *, now):
    row = await ledger.fetch_one(
        "SELECT alert_keys_json FROM body_witness_status WHERE witness_id=?", (witness_id,))
    keys = json.loads((row or {}).get("alert_keys_json") or "{}")
    key = dedup_key(witness_id, kind, dimension, config_gen)
    if key not in keys:
        return False
    keys.pop(key)
    await ledger.execute("UPDATE body_witness_status SET alert_keys_json=? WHERE witness_id=?",
                         (json.dumps(keys, sort_keys=True), witness_id))
    await ledger.record_alert(f"{kind}_cleared", {"witness_id": witness_id}, level="info",
                              bypass_freeze=True)
    return True


def should_freeze_global(error_code: str) -> bool:
    """只有权威身份冲突默认值得封冻；配置写错/unverified 不值得（会把系统焊死）。"""
    if FREEZE_POLICY == "none":      return False
    if FREEZE_POLICY == "all":       return error_code is not None
    return error_code == Err.AUTHORITY_IDENTITY_CONFLICT


async def acknowledge(ledger, witness_id, *, by, now=None):
    """人工 ack：critical 不走自动清除，必须有人签字。"""
    await ledger.execute(
        "UPDATE body_witness_status SET identity_acknowledged_at=?, critical_alerted=0,"
        " isolated=0 WHERE witness_id=?", (_iso_now(now), witness_id))
    await ledger.record_alert("witness_identity_ack", {"witness_id": witness_id, "by": by},
                              level="info", bypass_freeze=True)


async def apply_identity(ledger, witness_id, *, ident, config_gen, now_fn=time.time,
                         content_ok=False, domain_unique=True, live_seq_ok=True):
    """身份维度状态机 + 告警。返回 (identity_status, vote_eligible, actions)。"""
    now = now_fn()
    st = ident.status
    err = ident.error_code
    # ① TTL 过期 → stale，立即停止计票，但【不是】安全冲突
    if st in IDENTITY_OK and not _fresh(ident, now):
        st = "stale"
    fresh_ok = st in IDENTITY_OK
    # ② 严格计票：与 body.identity.countable 同一个闸门（等级要求由环境变量决定，
    #    不许状态机自己写死一个等级，否则"少一票"的判定会和身份层说的不一样）
    min_ev = os.environ.get("BODY_WITNESS_MIN_EVIDENCE", "E3")
    ok_vote, vote_reason = countable(ident, min_evidence=min_ev, fresh=fresh_ok)
    vote = bool(ok_vote and content_ok and domain_unique and live_seq_ok)
    # 掉票必须说得出理由 → 落进 identity_error_code，语音与卡片都读这一列
    blocked = None
    if not vote:
        if vote_reason != "ok":
            blocked = vote_reason                       # 等级不足 / 已过期 / 身份状态本身不行
        elif not content_ok:
            blocked = Err.CONTENT_NOT_VERIFIED
        elif not domain_unique:
            blocked = Err.CREDENTIAL_DOMAIN_DUPLICATE
        elif not live_seq_ok:
            blocked = Err.NOT_LIVE_YET
    err_effective = (err or blocked) if blocked else err
    actions = []

    if err_effective in ID_ALERT and st not in IDENTITY_OK:
        kind, level, debounce, cls = ID_ALERT[err_effective]
        key = dedup_key(witness_id, kind, "identity", config_gen if cls == "config" else None)
        if not debounce or cls in ("config", "authority"):
            actions.append(await _emit(ledger, witness_id, kind, level, key,
                                       {"error_code": err_effective, "status": st,
                                        "evidence_level": ident.evidence_level}, now=_iso_now(now)))
        else:
            # 传输/证据类：连 3 次或持续 2 分钟才开单
            v = await _bump_fail(ledger, witness_id, now)
            if v >= 3:
                actions.append(await _emit(ledger, witness_id, kind, level, key,
                                           {"error_code": err_effective, "status": st,
                                            "fails": v}, now=_iso_now(now)))
        # critical 冲突 → 立即隔离该见证（不封冻全局）
        if cls == "authority":
            await ledger.execute("UPDATE body_witness_status SET isolated=1, "
                                 "critical_alerted=1 WHERE witness_id=?", (witness_id,))
    elif blocked and st in IDENTITY_OK:
        # ★身份没问题、票没算上：单独开单（否则"掉票"在面板上无迹可寻）
        kind, level, debounce, cls = ID_ALERT[blocked]
        key = dedup_key(witness_id, kind, "vote", None)
        v = await _bump_fail(ledger, witness_id, now)
        if not debounce or v >= 3 or blocked == Err.CONTENT_NOT_VERIFIED:
            actions.append(await _emit(ledger, witness_id, kind, level, key,
                                       {"error_code": blocked, "status": st,
                                        "evidence_level": ident.evidence_level,
                                        "min_evidence": min_ev}, now=_iso_now(now)))
    else:
        # 恢复：config/authority 类需 ack；传输/证据类连续 2 次成功即清
        ok_n = await _bump_ok(ledger, witness_id, now)
        if st not in IDENTITY_OK:
            pass
        elif ok_n >= 2 or _acked(ident, now):
            for code, (kind, _lv, _db, cls) in ID_ALERT.items():
                await _close(ledger, witness_id, kind, "identity",
                             config_gen if cls == "config" else None, now=_iso_now(now))
                await _close(ledger, witness_id, kind, "vote", None, now=_iso_now(now))
            await _bump_fail(ledger, witness_id, now, reset=True)

    # stale 宽限期：TTL×2 之后仍未刷新 → 去抖升 degraded（隐噪 vs 真信号的分界）
    if st == "stale" and _overdue(ident, now):
        kind, key = "witness_identity_stale", dedup_key(witness_id, "witness_identity_stale",
                                                       "identity", None)
        await _emit(ledger, witness_id, kind, "warning", key,
                    {"ttl_mult": STALE_GRACE_MULT}, now=_iso_now(now))

    await ledger.execute(
        """UPDATE body_witness_status SET identity_status=?, content_status=?,
             evidence_level=?, identity_error_code=?, identity_observed_at=?,
             identity_expires_at=?, account_domain_fp=?, config_generation=?,
             vote_eligible=?, last_seen_at=?
           WHERE witness_id=?""",
        (st, "valid" if content_ok else "unknown", ident.evidence_level, err_effective,
         ident.observed_at, ident.expires_at,
         _ident_domain_fp(ident), config_gen, 1 if vote else 0, _iso_now(now), witness_id))
    return st, vote, actions


# 采样入口在 body.witness_runtime（唯一一处 loop）；本模块只负责状态机 + 告警。
