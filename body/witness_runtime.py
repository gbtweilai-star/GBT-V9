# body/witness_runtime.py —— 见证运行期：身份缓存 / 内容复核 / 采样循环 / 升级
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 见证 = 【已登记】的（body_witness_status 有行），不是"碰巧存在"的存储；
#       探不到就不计票（红线）；身份缓存过期即失效；重启后缓存冷启 → 重新探测，不猜。
from __future__ import annotations
import asyncio
import json
import logging
import os
import time

from body.anchor import anchor_record, latest_key
from body.identity import E3, Err, Identity, countable, finalize
from body.witness_registry import witness_fingerprints

log = logging.getLogger("body.witness")

IDENTITY_TTL = int(os.getenv("BODY_WITNESS_IDENTITY_TTL", "900"))     # 身份证据有效期
ACK_TTL      = int(os.getenv("BODY_WITNESS_ACK_TTL", str(24 * 3600)))  # 人工 ack 有效期
PROBE_INTERVAL = int(os.getenv("BODY_WITNESS_PROBE_INTERVAL", "30"))
REQUIRED     = int(os.getenv("BODY_WITNESS_REQUIRED_EXTERNAL", "2"))

# 进程内身份缓存：witness_id -> {"ident": Identity, "at": epoch}
_ID_CACHE: dict[str, dict] = {}
# 去抖计数（身份维度独立于 _apply_status 的持久化计数，互不污染）
_FAILS: dict[tuple, int] = {}
_OKS: dict[tuple, int] = {}


def _iso_now(ts=None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts if ts is not None else time.time()))


def _parse_iso(v):
    if not v:
        return None
    from common.timeutil import to_epoch
    try:
        return float(to_epoch(v))
    except Exception:
        return None


# ─────────── 身份维度的小工具（witness_alerts 与语音都读同一套判定）───────────
def ident_domain_fp(ident: Identity) -> str | None:
    """凭据域指纹：只存 HMAC（含端点），不存账户 ID 原文。没有身份证据 → None。

    注意：密钥缺失时 ident_fp 会抛错 → 这里返回 None（指纹不可得）。调用方必须
    把"有账户但拿不到指纹"当成缺口报出来，不能静默跳过重复域检查。
    """
    if not ident.account_id and not ident.owner_id:
        return None
    try:
        from body.identity import ident_fp
        return ident_fp("credential-domain", ident.provider, ident.endpoint_host,
                        ident.bucket, ident.account_id or ident.owner_id)
    except Exception:                                        # noqa: BLE001
        return None


def fresh(ident: Identity, now: float) -> bool:
    exp = _parse_iso(ident.expires_at)
    return bool(exp and exp > now)


def overdue(ident: Identity, now: float, mult: int = 2) -> bool:
    exp = _parse_iso(ident.expires_at)
    return bool(exp and now > exp + IDENTITY_TTL * (mult - 1))


def acked(ident: Identity, now: float) -> bool:
    at = _parse_iso(getattr(ident, "acknowledged_at", None))
    return bool(at and now - at < ACK_TTL)


async def bump_fail(ledger, witness_id, now, *, reset=False) -> int:
    k = (witness_id, "identity")
    _FAILS[k] = 0 if reset else _FAILS.get(k, 0) + 1
    if reset:
        _OKS[k] = _OKS.get(k, 0) + 1
    return _FAILS[k]


async def bump_ok(ledger, witness_id, now) -> int:
    k = (witness_id, "identity")
    _OKS[k] = _OKS.get(k, 0) + 1
    _FAILS[k] = 0
    return _OKS[k]


# ─────────── 身份探测：带缓存 + 读回人工 ack ───────────
async def resolve(ledger, witness_id, *, probe_fn, force=False, now_fn=time.time,
                  ttl=None) -> Identity:
    """取该见证的身份结论。缓存未过期直接用；force/过期 → 重新探测并刷新缓存。"""
    cache_key = f"{witness_id}:{os.environ.get('BODY_WITNESS_CONFIG_GEN', '-')}"
    ent = _ID_CACHE.get(cache_key)
    now = now_fn()
    limit = ttl or IDENTITY_TTL
    if ent and not force and (now - ent["at"]) < limit:
        return ent["ident"]
    ident = probe_fn() if not asyncio.iscoroutinefunction(probe_fn) else await probe_fn()
    if not isinstance(ident, Identity):
        ident = Identity(witness_id=witness_id, provider="?", endpoint_host="?", bucket="?",
                         status="identity_unverified",
                         error_code=Err.IDENTITY_UNVERIFIED,
                         detail={"reason": "probe_returned_invalid"})
    ident.observed_at = _iso_now(now)
    ident.expires_at = _iso_now(now + limit)
    row = await ledger.fetch_one(
        "SELECT identity_acknowledged_at FROM body_witness_status WHERE witness_id=?",
        (witness_id,))
    ident.acknowledged_at = (row or {}).get("identity_acknowledged_at")
    _ID_CACHE[cache_key] = {"ident": ident, "at": now}
    return ident


def clear_cache(witness_id: str | None = None) -> None:
    """配置变更后必须清缓存 —— 否则旧身份结论会被继续计票。"""
    if witness_id is None:
        _ID_CACHE.clear()
        return
    for k in [k for k in _ID_CACHE if k.split(":")[0] == witness_id]:
        _ID_CACHE.pop(k, None)


# ─────────── 内容维度：该见证的锚点对象读回并比对 ───────────
async def probe_content(provider, *, root_id="main", epoch=None, prefix="anchors",
                        expected_head=None) -> dict:
    """只读：读该见证存储里的 LATEST 锚点对象，与库中 head 比对（不一致 = disagreement）。"""
    if provider is None:
        return {"status": "pending", "detail": "no_provider", "seq": None}
    key = latest_key(prefix, root_id, epoch or os.getenv("BODY_ANCHOR_EPOCH", "e1"))
    try:
        payload = await provider.get(key)
    except Exception as e:                                   # noqa: BLE001
        return {"status": "unreachable", "detail": f"{type(e).__name__}", "seq": None}
    if payload is None:
        return {"status": "pending", "detail": "no_anchor_object", "seq": None}
    try:
        got = json.loads(payload.decode("utf-8"))
    except Exception:                                        # noqa: BLE001
        return {"status": "invalid", "detail": "anchor_object_unparsable", "seq": None}
    seq = got.get("seq")
    if expected_head and got.get("head_hash") != expected_head:
        return {"status": "disagreement", "detail": "head_hash_differs", "seq": seq,
                "found": got.get("head_hash"), "expected": expected_head}
    rec = anchor_record(got.get("root_id", root_id), got.get("epoch", epoch or "e1"),
                        got.get("seq", 0), got.get("head_hash"), got.get("prev_anchor_hash"),
                        got.get("kid"), got.get("at"))
    from body.anchor import anchor_hash
    if anchor_hash(rec) != got.get("anchor_hash", anchor_hash(rec)):
        return {"status": "invalid", "detail": "anchor_self_hash_broken", "seq": seq}
    return {"status": "valid", "detail": "ok", "seq": seq,
            "kid": got.get("kid"), "head_hash": got.get("head_hash")}


# ─────────── 见证清单：只认已登记的 ───────────
class WitnessRuntime:
    """一个见证 = 一份登记配置 + 一个锚点存储 provider（可为 None，诚实留空）。"""

    def __init__(self, witness_id, *, provider=None, cfg=None, client=None):
        self.witness_id, self.provider, self.cfg, self.client = \
            witness_id, provider, cfg, client

    @property
    def kid(self):
        return (self.cfg or {}).get("kid")

    def probe_identity(self) -> Identity:
        """有配置才探测；没配置 = identity_unverified（不是"默认可信"）。"""
        cfg = self.cfg or {}
        if not cfg.get("endpoint") or not cfg.get("bucket"):
            return Identity(witness_id=self.witness_id, provider=cfg.get("provider") or "s3",
                            endpoint_host="", bucket="",
                            status="identity_unverified",
                            error_code=Err.IDENTITY_UNVERIFIED,
                            detail={"reason": "witness_config_absent"})
        if self.client is None:
            return Identity(witness_id=self.witness_id, provider=cfg.get("provider") or "s3",
                            endpoint_host=cfg["endpoint"], bucket=cfg["bucket"],
                            status="unreachable", error_code=Err.UNREACHABLE,
                            detail={"where": "client", "code": "no_client"})
        kind = (cfg.get("provider") or cfg.get("kind") or "s3").lower()
        if "cloudflare" in kind or "r2" in kind:
            from body.identity_r2 import probe_r2
            return probe_r2(witness_id=self.witness_id, endpoint=cfg["endpoint"],
                            bucket=cfg["bucket"], client=self.client,
                            cf_api_token=cfg.get("cf_api_token") or None)
        from body.identity_s3 import probe_s3
        return probe_s3(witness_id=self.witness_id, endpoint=cfg["endpoint"],
                        bucket=cfg["bucket"], client=self.client, provider=kind,
                        cfg_account_claim=cfg.get("account_claim") or None)


async def load_witnesses(ledger, anchor_multi=None) -> list[WitnessRuntime]:
    """已登记的见证（body_witness_status 有行）→ 运行期对象；provider 按名字对齐。"""
    from body.witness_admin import load_witness_config, make_client
    rows = await ledger.fetch_all(
        "SELECT witness_id, provider FROM body_witness_status ORDER BY witness_id")
    providers = {getattr(p, "name", ""): p
                 for p in (getattr(anchor_multi, "providers", None) or [])}
    out = []
    for r in rows or []:
        wid = r["witness_id"]
        try:
            cfg = load_witness_config(wid)
        except Exception:                                     # noqa: BLE001
            cfg = {}
        client = None
        try:
            client = make_client(cfg) if (cfg or {}).get("endpoint") else None
        except Exception:                                     # noqa: BLE001
            client = None
        provider = providers.get(wid) or providers.get((cfg or {}).get("provider", ""))  \
            or providers.get((cfg or {}).get("kind", ""))
        out.append(WitnessRuntime(wid, provider=provider, cfg=cfg, client=client))
    return out


# ─────────── 一轮采样：身份 + 内容 → 报告 ───────────
async def verify_all(ledger, witnesses, *, root_id="main", export_dir=None,
                     now_fn=time.time) -> dict:
    """逐见证复核。返回报告；quorum.count 只数【身份侧可计票】，权威票数以
    witness_snapshot 为准（apply_identity 之后由调用方回填）。"""
    rows = await ledger.fetch_all(
        "SELECT seq, head_hash, epoch FROM body_anchors WHERE root_id=? "
        "ORDER BY seq DESC LIMIT 1", (root_id,))
    head = rows[0] if rows else None
    min_ev = os.environ.get("BODY_WITNESS_MIN_EVIDENCE", "E3")
    report_witnesses, idents, n_countable = [], [], 0
    for w in witnesses:
        ident = await resolve(ledger, w.witness_id, probe_fn=w.probe_identity,
                              now_fn=now_fn)
        content = await probe_content(
            w.provider, root_id=root_id,
            epoch=(head or {}).get("epoch"),
            expected_head=(head or {}).get("head_hash"))
        ok, why = countable(ident, min_evidence=min_ev, fresh=fresh(ident, now_fn()))
        n_countable += 1 if ok else 0
        idents.append(ident)
        report_witnesses.append({
            "witness_id": w.witness_id, "provider": (w.cfg or {}).get("provider", "-"),
            "kid": w.kid, "status": content["status"], "detail": content.get("detail"),
            "seq": content.get("seq"), "identity_status": ident.status,
            "evidence_level": ident.evidence_level, "error_code": ident.error_code,
            "vote_blocked_by": None if ok else why,
            "content": content, "identity": ident})
    # 凭据域唯一性：用指纹判定（两个见证共用同一凭据域 → 全部不计票）
    domains: dict[str, list[str]] = {}
    for ident in idents:
        d = ident_domain_fp(ident)
        if d:
            domains.setdefault(d, []).append(ident.witness_id)
    dup = sorted({w for v in domains.values() if len(v) > 1 for w in v})
    # 有账户却拿不到指纹（密钥缺失）→ 重复域检查形同虚设，必须报出来
    fp_gap = any(i.account_id and not ident_domain_fp(i) for i in idents)
    status = ("critical" if any(x["status"] in ("disagreement", "invalid")
                                or x["identity_status"] == "critical_conflict"
                                for x in report_witnesses)
              else "healthy" if n_countable >= REQUIRED else "degraded")
    return {"witnesses": report_witnesses, "idents": idents,
            "quorum": {"count": n_countable, "required": REQUIRED, "status": status},
            "duplicate_domains": dup, "fingerprint_gap": fp_gap,
            "summary": {"witnesses": len(report_witnesses), "countable": n_countable,
                        "required": REQUIRED, "min_evidence": min_ev,
                        "duplicate_domains": dup, "fingerprint_gap": fp_gap},
            "head": dict(head) if head else None}


async def write_probe_row(ledger, *, votes, required, status, summary, now_fn=time.time):
    """旧的顶层单行快照（面板 probe 块）。数字与 witness_snapshot 同源写入。"""
    await ledger.execute(
        """INSERT INTO body_witness_probe (id, observed_at, quorum_valid, required,
               status, detail_json) VALUES (1,?,?,?,?,?)
           ON CONFLICT (id) DO UPDATE SET observed_at=EXCLUDED.observed_at,
             quorum_valid=EXCLUDED.quorum_valid, required=EXCLUDED.required,
             status=EXCLUDED.status, detail_json=EXCLUDED.detail_json""",
        (_iso_now(now_fn()), int(votes), int(required), status,
         json.dumps(summary or {}, ensure_ascii=False, default=str)))


async def escalate(app, report) -> dict:
    """见证层面的升级处置：权威冲突/不一致才 critical；不默认封冻全局。"""
    from body.witness_alerts import should_freeze_global
    bad = [w for w in report["witnesses"]
           if w["status"] in ("disagreement", "invalid")
           or w["identity_status"] == "critical_conflict"]
    if not bad:
        return {"ok": True, "escalated": False}
    err = (bad[0].get("error_code") or "WITNESS_CONTENT_MISMATCH")
    fail_mode = os.getenv("BODY_CHAIN_FAIL_MODE", "warn")
    await app.state.ledger.record_alert(
        "witness_integrity", {"witnesses": [w["witness_id"] for w in bad],
                              "statuses": [w["status"] for w in bad],
                              "error_code": err},
        level="critical", bypass_freeze=True)
    msg = f"见证异常：{'、'.join(w['witness_id'] for w in bad)}（{err}）"
    if fail_mode == "freeze" and should_freeze_global(err):
        app.state.ledger.chain_frozen = True
        msg += "，已封冻登记写入，等待大脑指令"
    try:
        from body.voice_bus import announce
        await announce(msg, priority=0)
    except Exception:                                          # noqa: BLE001
        log.warning("body: %s", msg)
    return {"ok": False, "escalated": True, "witnesses": [w["witness_id"] for w in bad]}


# ─────────── 采样循环（唯一一处；recheck_leader 保证单 worker 写）───────────
async def probe_once(app, *, now_fn=time.time) -> dict:
    from body.recheck import recheck_leader
    from body.witness_alerts import apply_identity
    from body.witness_voice import reconcile_witness_snapshot
    ledger = app.state.ledger
    async with recheck_leader(ledger, key="body:witness-probe") as who:
        if who is None:
            return {"skipped": "not_leader"}
        witnesses = await load_witnesses(ledger, getattr(app.state, "anchor_multi", None))
        cfg_gen = getattr(app.state, "config_generation", None) or \
            os.environ.get("BODY_WITNESS_CONFIG_GEN", "-")
        report = await verify_all(ledger, witnesses, root_id="main", now_fn=now_fn)
        if report["duplicate_domains"]:
            await ledger.record_alert("witness_credential_domain_duplicate",
                                      {"witnesses": report["duplicate_domains"]},
                                      level="critical", bypass_freeze=True)
        if report.get("fingerprint_gap"):
            await ledger.record_blocked("witness_fingerprint_key_missing",
                                        {"hint": "BODY_WITNESS_FP_KEY 未设置："
                                                 "凭据域重复检查无法执行"})
        dup = set(report["duplicate_domains"])
        votes = 0
        for w in report["witnesses"]:
            ident = w["identity"]
            _st, vote, _actions = await apply_identity(
                ledger, w["witness_id"], ident=ident, config_gen=cfg_gen,
                content_ok=(w["status"] == "valid"),
                domain_unique=(w["witness_id"] not in dup),
                live_seq_ok=w["seq"] is not None)
            votes += 1 if vote else 0
        # ★数字只有一个来源：apply_identity 落库后由 reconcile 统一算快照，
        #   跳变（权威冲突/掉票/归位/跌破要求数）写 outbox → 立刻尝试播报
        rows = await ledger.fetch_all(
            "SELECT * FROM body_witness_status ORDER BY witness_id")
        snap = await reconcile_witness_snapshot(ledger, rows, required=REQUIRED)
        voiced = await flush_voice(app, now_fn=now_fn)
        await write_probe_row(ledger, votes=snap["valid_count"], required=REQUIRED,
                              status=snap["status"], summary=report["summary"],
                              now_fn=now_fn)
        if snap["status"] == "critical":
            await escalate(app, report)
        return {"votes": snap["valid_count"], "required": REQUIRED,
                "status": snap["status"], "events": snap["events"], "voiced": voiced,
                "witnesses": [w["witness_id"] for w in report["witnesses"]]}


async def flush_voice(app, *, now_fn=time.time, **kw) -> dict:
    """把 outbox 里待播的见证事件交给语音总线（页面同时收到文本）。

    没有总线时不假装播过 —— 事件留在 pending，等页面/总线就绪。
    kw 透传给 flush_voice_outbox（merge_window / degraded_cooldown 等）。
    """
    from body.witness_voice import flush_voice_outbox
    ledger = app.state.ledger
    bus = getattr(app.state, "voice_bus", None)
    if bus is None:
        return {"spoken": 0, "reason": "no_voice_bus"}

    async def speak(text, *, priority="normal", interrupt=False):
        await bus.submit("witness", text,
                         priority=0 if priority == "critical" else 1, source="alert")
    return await flush_voice_outbox(ledger, tts=bus, speak_fn=speak, now_fn=now_fn, **kw)


async def probe_loop(app, *, interval=PROBE_INTERVAL):
    """后台采样。异常绝不拖垮 app（只记日志）。

    零见证时**退避**（真机缺陷：一个见证都没登记时，探测仍每分钟跑一次，
    把 witness_snapshot.revision 空涨到 800+ —— 白写白刷）。改为逐次翻倍到上限，
    一旦有见证就立刻恢复原节奏。
    """
    backoff = interval
    max_idle = float(os.environ.get("V9_WITNESS_IDLE_MAX", "900"))
    while True:
        rows = []
        try:
            await probe_once(app)
        except asyncio.CancelledError:
            raise
        except Exception:                                      # noqa: BLE001
            log.exception("witness probe error")
        try:
            led = getattr(getattr(app, "state", None), "ledger", None)
            rows = await led.fetch_all("SELECT witness_id FROM body_witness_status")
        except Exception:                                      # noqa: BLE001
            rows = []
        if rows:
            backoff = interval
        else:
            backoff = min(max_idle, max(interval, backoff * 2))
            log.info("witness probe idle（未登记见证）→ 下次 %.0fs 后", backoff)
        await asyncio.sleep(backoff)


async def start(app) -> None:
    """接线：重启不重念旧事 → voice_outbox_boot()；然后起采样循环。"""
    from body.witness_voice import voice_outbox_boot
    try:
        await voice_outbox_boot(app.state.ledger)
    except Exception:                                          # noqa: BLE001
        log.warning("voice outbox boot skipped")
    app.state.witness_probe_task = asyncio.create_task(probe_loop(app))
    log.info("witness probe loop started")


async def stop(app) -> None:
    t = getattr(app.state, "witness_probe_task", None)
    if t is not None:
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)


__all__ = ["WitnessRuntime", "load_witnesses", "resolve", "clear_cache", "fresh",
           "overdue", "acked", "bump_fail", "bump_ok", "ident_domain_fp",
           "probe_content", "verify_all", "write_probe_row", "escalate",
           "probe_once", "probe_loop", "start", "stop", "IDENTITY_TTL", "REQUIRED"]
