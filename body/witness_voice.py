# body/witness_voice.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 只在跳变时开口; 数字只从 witness_snapshot 取, 不自己重算;
#       不念账户ID/凭据/指纹; 播报失败必须留文本并如实标记, 不许假报"已播"
from __future__ import annotations
import json, logging, os, time, uuid

log = logging.getLogger("body.voice")

# ── 读音别名：witness_id → 可朗读的稳定名字（未知一律降级为"见证 X"）──
SPOKEN_NAMES = {"r2-primary": "R2 主见证", "archive-b": "归档 B",
                "ts-ots": "时间戳见证", "offline-usb": "本地副本"}


def spoken_name(witness_id: str) -> str:
    if witness_id in SPOKEN_NAMES:
        return SPOKEN_NAMES[witness_id]
    return f"见证 {abs(hash(witness_id)) % 97:02d}"     # 不念原始 id、不念指纹

# 白名单原因 → 人话（其余错误码不换词，绝不念异常全文）
REASON_TEXT = {
    "AUTHORITY_IDENTITY_CONFLICT": "权威身份冲突",
    "CONFIG_IDENTITY_MISMATCH":    "身份配置与服务端不符",
    "IDENTITY_UNVERIFIED":         "身份验证不足",
    "IDENTITY_FIELD_FORBIDDEN":    "无权读取身份字段",
    "IDENTITY_FIELD_UNSUPPORTED":  "服务端不支持身份查询",
    "AUTH_FAILED":                 "凭据认证失败",
    "UNREACHABLE":                 "身份探测不可达",
    "stale":                       "身份证据已过期",
    "IDENTITY_STALE":              "身份证据已过期",
    # 身份没问题、但这一票没算上（掉票必须说得出理由）
    "evidence_below_required":     "证据等级不足",
    "EVIDENCE_BELOW_REQUIRED":     "证据等级不足",
    "CONTENT_NOT_VERIFIED":        "内容对不上",
    "CREDENTIAL_DOMAIN_DUPLICATE": "与另一个见证共用同一份凭据",
    "NOT_LIVE_YET":                "还没到实时区间",
}
CRITICAL, DEGRADED, INFO = 0, 1, 2


# ═══════════ ① reconcile：一次算清快照 + 跳变 + outbox ═══════════
async def reconcile_witness_snapshot(ledger, probe_rows, *, required, now_fn=time.time):
    """probe_rows 是刚落库的 body_witness_status 行。返回 snapshot dict。"""
    prev = await ledger.fetch_one("SELECT * FROM witness_snapshot WHERE id=1")
    prev_states = json.loads(prev["states_json"]) if prev else {}
    rev = (prev["revision"] + 1) if prev else 1
    now = now_fn()

    states = {r["witness_id"]: {"vote_eligible": bool(r["vote_eligible"]),
                               "identity_status": r["identity_status"],
                               "evidence_level": r["evidence_level"],
                               "content_status": r["content_status"],
                               "isolated": bool(r["isolated"]),
                               "reason_code": r["identity_error_code"]}
              for r in probe_rows}
    valid = sum(1 for s in states.values() if s["vote_eligible"])
    conflicts = sorted(w for w, s in states.items()
                       if s["identity_status"] == "critical_conflict")
    status = ("critical" if conflicts else
              "healthy" if valid >= required else "degraded")

    events = []
    # ① 权威冲突：非冲突 → 冲突（已在冲突中不重复开口）
    newly = [w for w in conflicts
             if prev_states.get(w, {}).get("identity_status") != "critical_conflict"]
    if newly:
        events.append({"kind": "conflict", "priority": CRITICAL, "witnesses": newly,
                       "reason_code": "AUTHORITY_IDENTITY_CONFLICT"})
    # ② 掉票 / 归位：只在 1→0 / 0→1 时发生
    for w in sorted(states):
        s, p = states[w], prev_states.get(w, {})
        if prev_states and p.get("vote_eligible") and not s["vote_eligible"]:
            events.append({"kind": "vote_lost", "priority": DEGRADED, "witnesses": [w],
                           "reason_code": s["reason_code"]})
        elif prev_states and not p.get("vote_eligible") and s["vote_eligible"]:
            events.append({"kind": "vote_restored", "priority": INFO, "witnesses": [w],
                           "reason_code": None})
    # ③ 有效票跌破要求数：跨越阈值才算一次
    if valid < required and (prev is None or prev["valid_count"] >= required):
        events.append({"kind": "quorum_low", "priority": DEGRADED, "witnesses": [],
                       "reason_code": None})

    for e in events:
        tid = f"{e['kind']}:{','.join(sorted(e['witnesses']))}:{rev}"
        text = render_utterance([e], valid=valid, required=required)
        await ledger.execute(
            """INSERT INTO witness_voice_outbox
                 (event_id, revision, transition_id, kind, priority, witness_ids_json,
                  reason_code, valid_count, required, text, state, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,'pending',?)
               ON CONFLICT (transition_id) DO NOTHING""",
            (uuid.uuid4().hex, rev, tid, e["kind"], e["priority"],
             json.dumps(e["witnesses"]), e["reason_code"], valid, required, text, _iso(now)))

    await ledger.execute(
        """INSERT INTO witness_snapshot (id, revision, valid_count, required, status,
               states_json, observed_at) VALUES (1,?,?,?,?,?,?)
           ON CONFLICT (id) DO UPDATE SET revision=EXCLUDED.revision,
             valid_count=EXCLUDED.valid_count, required=EXCLUDED.required,
             status=EXCLUDED.status, states_json=EXCLUDED.states_json,
             observed_at=EXCLUDED.observed_at""",
        (rev, valid, required, status,
         json.dumps(states, sort_keys=True), _iso(now)))
    return {"revision": rev, "valid_count": valid, "required": required,
            "status": status, "states": states, "events": len(events)}


# ═══════════ ② 渲染：critical 先念，degraded 合并成一句 ═══════════
def render_utterance(events, *, valid: int, required: int) -> str:
    crit = [e for e in events if e["priority"] == CRITICAL]
    lost = sorted({w for e in events if e["kind"] == "vote_lost" for w in e["witnesses"]})
    rest = sorted({w for e in events if e["kind"] == "vote_restored" for w in e["witnesses"]})
    low  = any(e["kind"] == "quorum_low" for e in events)
    parts = []
    if crit:
        names = "、".join(spoken_name(w) for w in sorted({w for e in crit for w in e["witnesses"]}))
        parts.append(f"安全告警。{names}出现权威身份冲突，已隔离；"
                     f"有效见证 {valid}/{required}。请人工核验并确认。")
    if lost:
        names = "、".join(spoken_name(w) for w in lost)
        why = REASON_TEXT.get(next((e["reason_code"] for e in events
                                    if e["kind"] == "vote_lost" and e["reason_code"]), ""),
                              "身份不可计票")
        parts.append(f"见证{names}掉票，原因：{why}；"
                     f"有效见证 {valid}/{required}，要求 {required}。请检查身份探测配置。")
    elif low:
        parts.append(f"有效见证不足，当前 {valid}/{required}。")
    if rest and not parts:                      # 归位只在没有坏消息时单独念
        parts.append(f"{'、'.join(spoken_name(w) for w in rest)} 已恢复计票；"
                     f"有效见证 {valid}/{required}。")
    return " ".join(parts)


# ═══════════ ③ flush：合并窗口 + 优先级 + 打断 + 失败回退 ═══════════
async def flush_voice_outbox(ledger, tts, *, now_fn=time.time,
                             merge_window=2.0, crit_window=0.5,
                             degraded_cooldown=30.0, speak_fn=None):
    now = now_fn()
    rows = await ledger.fetch_all(
        "SELECT * FROM witness_voice_outbox WHERE state='pending' "
        "ORDER BY priority ASC, created_at ASC")
    if not rows:
        return {"spoken": 0, "reason": "empty"}

    crit     = [r for r in rows if r["priority"] == CRITICAL]
    degraded = [r for r in rows if r["priority"] == DEGRADED]
    info     = [r for r in rows if r["priority"] == INFO]
    batches = []
    if crit:
        batches.append(crit)                                    # critical 立即
    elif degraded:
        if now - _ts(degraded[0]["created_at"]) >= merge_window \
                or all(r["kind"] == "quorum_low" for r in degraded):
            last = await _last_spoken_at(ledger, DEGRADED)
            if last is None or now - last >= degraded_cooldown:  # 冷却，但不吞恢复/critical
                batches.append(degraded)
    elif info and not await _tts_busy(tts):
        batches.append(info)

    spoken, snap = 0, None
    for batch in batches:
        events = [{"kind": r["kind"], "priority": r["priority"],
                   "witnesses": json.loads(r["witness_ids_json"]),
                   "reason_code": r["reason_code"]} for r in batch]
        # ★数字只从 snapshots 取：用批内同 revision 的计数，绝不重算
        snap = await ledger.fetch_one("SELECT * FROM witness_snapshot WHERE id=1")
        text = render_utterance(events, valid=snap["valid_count"], required=snap["required"])
        ids = [r["event_id"] for r in batch]
        await _mark(ledger, ids, "playing", now)
        try:
            fn = speak_fn or tts.speak_priority
            await fn(text, priority=("critical" if batch[0]["priority"] == CRITICAL else "normal"),
                     interrupt=(batch[0]["priority"] == CRITICAL))
        except Exception as e:
            # ★失败必须留文本 + 如实标记 + 面板提示，不许假报已播
            await _mark(ledger, ids, "spoken_failed", now, error=f"{type(e).__name__}: {e}")
            await ledger.record_alert("witness_voice_failed",
                                      {"events": ids, "text": text,
                                       "error": type(e).__name__},
                                      level="warning", bypass_freeze=True)
            log.warning("voice failed, text kept for panel: %s", text)
        else:
            await _mark(ledger, ids, "spoken", now)
            spoken += len(batch)
    return {"spoken": spoken, "pending": len(rows) - spoken,
            "revision": (snap or {}).get("revision")}


# ═══════════ ④ 重启：不重念旧事 ═══════════
async def voice_outbox_boot(ledger, *, now_fn=time.time, pending_ttl=300):
    now = now_fn()
    await ledger.execute("UPDATE witness_voice_outbox SET state='interrupted_unknown' "
                         "WHERE state='playing'")        # 播没播无法可靠判定 → 不重播
    await ledger.execute("UPDATE witness_voice_outbox SET state='superseded' "
                         "WHERE state='pending' AND created_at < ?", (_iso(now - pending_ttl),))
    return await ledger.fetch_all("SELECT state, COUNT(*) n FROM witness_voice_outbox "
                                 "GROUP BY state")


# ═══════════ ⑤ 内部助手（此前只有调用、没有实现）═══════════
def _ts(value) -> float:
    """ISO/epoch → epoch 秒。时间口径统一走 common.timeutil（naive 按 UTC）。"""
    from common.timeutil import to_epoch
    return float(to_epoch(value))


def _iso(ts) -> str:
    from common.timeutil import canon_iso
    return canon_iso(ts)


async def _mark(ledger, event_ids, state: str, now, error=None) -> None:
    """标记 outbox 行状态（playing / spoken / spoken_failed / superseded）。"""
    for eid in event_ids:
        await ledger.execute(
            "UPDATE witness_voice_outbox SET state=?, updated_at=?, error=? "
            "WHERE event_id=?", (state, _iso(now), error, eid))


async def _last_spoken_at(ledger, priority):
    """上一次该类播报的时间（epoch）。没有 → None。"""
    rows = await ledger.fetch_all(
        "SELECT MAX(updated_at) AS t FROM witness_voice_outbox "
        "WHERE state='spoken' AND priority=?", (int(priority),))
    if not rows or not rows[0]["t"]:
        return None
    try:
        return _ts(rows[0]["t"])
    except Exception:
        return None


async def _tts_busy(tts) -> bool:
    """TTS 是否忙：优先问适配器；问不到就当作忙（宁可晚念，不插队）。"""
    if tts is None:
        return True
    for attr in ("busy", "is_busy"):
        v = getattr(tts, attr, None)
        if isinstance(v, bool):
            return v
    q = getattr(tts, "q", None)
    try:
        if q is not None:
            return q.qsize() > 0
    except Exception:
        pass
    return False


def render_witness_sentence(facts: dict, *, stale: bool = False,
                            age_text: str = "", status: str | None = None) -> str:
    """见证安全句：工具与卡片**共用同一措辞**（view.read_snapshot 是唯一读取路径）。"""
    from body.tools.base import unknown_sentence
    if stale:
        return unknown_sentence("见证快照已超出有效期", age_text)
    valid, required = facts.get("valid_count"), facts.get("required")
    if valid is None or required is None:
        return unknown_sentence("见证快照缺少计数")
    tail = {"critical": "存在权威身份冲突，需要人工核验。",
            "degraded": "有效见证不足，请检查身份探测配置。",
            "healthy": "见证正常。"}.get(status or "", "")
    return f"当前有效见证 {valid}/{required}。" + tail
