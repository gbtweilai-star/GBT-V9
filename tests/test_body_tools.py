# tests/test_body_tools.py —— 只读工具 + 快照采样 + 见证告警/语音（离线确定性）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 工具注册/白名单/审计；② 没有快照 ≠ 0；③ stale 必须说"无法确认"；
#   ④ 卡片路径与工具路径同源同数；⑤ 采样写快照（真读数，含 revision 单调）；
#   ⑥ 告警：config_mismatch / critical_conflict / identity_unverified / 证据不足掉票；
#   ⑦ 语音：掉票跳变才开口、播报失败留文本不假报已播。
import asyncio
import json

import pytest

from body.adapters.sqlite_db import SqliteDb
from migrations.runner import apply_pending

KEY = "k" * 32


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BODY_WITNESS_FP_KEY", KEY)
    monkeypatch.setenv("BODY_WITNESS_REQUIRED_EXTERNAL", "2")
    monkeypatch.setenv("BODY_WITNESS_MIN_EVIDENCE", "E3")


@pytest.fixture
def ready_db(tmp_path):
    db = run(SqliteDb.open(str(tmp_path / "body.db")))
    run(apply_pending(db))
    return db


# ═══ ① 注册与清单：清单里有、实现里就有 ═══
def test_tools_registered_and_specs_complete():
    from body.tools.base import TOOLS, DOMAINS
    from body.tools.prompt import tool_specs, tool_names
    assert set(tool_names()) == {"media.capture", "scan.coverage", "media.queue",
                                "get_witness_snapshot"}
    for t in TOOLS.values():
        assert t.domain in DOMAINS
    for s in tool_specs():
        f = s["function"]
        assert f["parameters"]["additionalProperties"] is False      # 白名单语义
        assert f["name"] and f["description"]


def test_unknown_tool_is_audited(ready_db):
    from body.tools.base import call_tool
    res = run(call_tool(ready_db, "nope", session_id="s1"))
    assert res.unknown_reason == "unknown_tool"
    rows = run(ready_db.fetch_all("SELECT tool, ok, error_code FROM read_tool_audit"))
    assert rows and rows[0]["tool"] == "nope" and rows[0]["ok"] == 0


def test_bad_param_rejected_by_whitelist(ready_db):
    from body.tools.base import call_tool
    res = run(call_tool(ready_db, "media.queue", session_id="s1",
                        params={"window_s": 60, "evil": "1"}))
    assert res.unknown_reason.startswith("invalid_params")


# ═══ ②③ 采样缺失 / 过期：一律"无法确认"，绝不报 0 ═══
def test_missing_snapshot_says_unknown_not_zero(ready_db):
    from body.tools.view import read_snapshot
    res = run(read_snapshot(ready_db, "queue"))
    assert res.unknown_reason == "no_snapshot"
    assert "无法确认" in res.safe_sentence
    assert "0" not in res.safe_sentence.split("原因")[0]


def test_stale_snapshot_says_unknown(ready_db):
    from body.tools.view import read_snapshot
    run(ready_db.execute(
        """INSERT INTO body_read_snapshots
             (domain, revision, observed_at, sample_period_s, payload_json, evidence_json)
           VALUES (?,?,?,?,?,?)""",
        ("queue", 1, "2020-01-01T00:00:00Z", 5,
         json.dumps({"coverage": "observed", "depth": {"queued": 3}}), "[]")))
    res = run(read_snapshot(ready_db, "queue"))
    assert res.stale is True and res.unknown_reason == "stale"
    assert "无法确认" in res.safe_sentence and "3" not in res.safe_sentence


def test_fresh_snapshot_reports_real_numbers(ready_db):
    from body.tools.view import read_snapshot
    from body.tools.base import call_tool
    from common.timeutil import canon_iso
    import time as _t
    run(ready_db.execute(
        """INSERT INTO body_read_snapshots
             (domain, revision, observed_at, sample_period_s, payload_json, evidence_json)
           VALUES (?,?,?,?,?,?)""",
        ("devour", 7, canon_iso(_t.time()), 5,
         json.dumps({"coverage": "observed", "frames": 128, "span": 130, "gaps": 2,
                     "gap_seqs": [4, 9], "segments": 3}), "[]")))
    # ★卡片路径与工具路径必须给出同一份读数（同 revision、同句子）
    card = run(read_snapshot(ready_db, "devour"))
    tool = run(call_tool(ready_db, "media.capture", session_id="s"))
    assert (card.revision, card.safe_sentence) == (tool.revision, tool.safe_sentence)
    assert card.revision == 7 and card.stale is False
    assert "128" in card.safe_sentence and "2 个断点" in card.safe_sentence


# ═══ ④⑤ 采样：真读数落库，revision 单调 ═══
def test_collect_writes_three_domains_with_real_devour_index(ready_db, tmp_path):
    from body.tools import collect as C
    idx = tmp_path / "index.jsonl"
    idx.write_text("\n".join(json.dumps(r) for r in [
        {"seq": 0, "state": "stored", "sha256": "a"},
        {"seq": 1, "state": "stored", "sha256": "b"},
        {"seq": 3, "state": "stored", "sha256": "c"}]) + "\n", encoding="utf-8")
    (tmp_path / "segments.jsonl").write_text(
        json.dumps({"seg_id": "s1"}) + "\n" + json.dumps({"seg_id": "s1"}) + "\n",
        encoding="utf-8")
    first = run(C.refresh_once(ready_db, frame_dir=tmp_path, scan_ledger=None))
    assert set(first) == {"devour", "scan", "queue"}
    facts = json.loads(run(ready_db.fetch_one(
        "SELECT payload_json FROM body_read_snapshots WHERE domain='devour'"))
        ["payload_json"])
    assert (facts["frames"], facts["span"], facts["gaps"], facts["segments"]) == (3, 4, 1, 1)
    second = run(C.refresh_once(ready_db, frame_dir=tmp_path, scan_ledger=None))
    assert second["devour"]["revision"] == first["devour"]["revision"] + 1


def test_queue_snapshot_unavailable_without_ledger(ready_db, tmp_path):
    from body.tools import collect as C
    run(C.refresh_once(ready_db, frame_dir=tmp_path, scan_ledger=None))
    facts = json.loads(run(ready_db.fetch_one(
        "SELECT payload_json FROM body_read_snapshots WHERE domain='queue'"))
        ["payload_json"])
    assert facts["coverage"] == "unavailable"           # 没有账本 → 不可用，而不是 0


# ═══ ⑥ 见证告警：三类状态各开自己的单 ═══
def _ident(status, error_code, level="E1_config"):
    from body.identity import Identity
    return Identity(witness_id="w1", provider="s3-compatible",
                    endpoint_host="s3.local", bucket="b", status=status,
                    error_code=error_code, evidence_level=level,
                    account_id="acct-a", observed_at="2026-01-01T00:00:00Z",
                    expires_at="2099-01-01T00:00:00Z")


def test_alerts_for_the_three_identity_statuses(ready_db):
    from body.identity import Err
    from body.witness_alerts import apply_identity
    cases = [(Err.CONFIG_IDENTITY_MISMATCH, "witness_identity_config_mismatch", "warning", 1),
             (Err.AUTHORITY_IDENTITY_CONFLICT, "witness_identity_conflict", "critical", 1),
             # 证据类走 3 次去抖（传输/探测本身会闪）
             (Err.IDENTITY_UNVERIFIED, "witness_identity_unverified", "warning", 3)]
    for code, kind, level, strikes in cases:
        run(ready_db.execute("DELETE FROM body_alerts"))
        st, vote = None, None
        for _ in range(strikes):
            st, vote, _ = run(apply_identity(ready_db, "w1", ident=_ident("config_mismatch"
                             if code == Err.CONFIG_IDENTITY_MISMATCH else
                             ("critical_conflict" if code == Err.AUTHORITY_IDENTITY_CONFLICT
                              else "identity_unverified"), code), config_gen="g1"))
        assert vote is False, code
        alerts = run(ready_db.fetch_all("SELECT kind, level FROM body_alerts"))
        hit = [a for a in alerts if a["kind"] == kind]
        assert hit and hit[0]["level"] == level, (code, alerts)


def test_authority_conflict_isolates_but_does_not_freeze(ready_db):
    from body.identity import Err
    from body.witness_alerts import apply_identity, should_freeze_global
    run(ready_db.execute("""INSERT INTO body_witness_status (witness_id, provider)
        VALUES ('w1','s3-compatible')"""))
    run(apply_identity(ready_db, "w1", ident=_ident("critical_conflict",
        Err.AUTHORITY_IDENTITY_CONFLICT), config_gen="g1"))
    row = run(ready_db.fetch_one(
        "SELECT isolated, identity_status, vote_eligible FROM body_witness_status "
        "WHERE witness_id='w1'"))
    assert row["isolated"] == 1 and row["identity_status"] == "critical_conflict"
    assert row["vote_eligible"] == 0
    assert should_freeze_global(Err.AUTHORITY_IDENTITY_CONFLICT) is True
    assert should_freeze_global(Err.CONFIG_IDENTITY_MISMATCH) is False   # 配置写错不封冻


def test_vote_blocked_by_evidence_records_reason(ready_db):
    """身份没问题但等级不够 → 掉票原因必须落库（语音据此说出是哪一个掉票）"""
    from body.identity import Err
    from body.witness_alerts import apply_identity
    run(ready_db.execute("""INSERT INTO body_witness_status (witness_id, provider)
        VALUES ('w1','s3-compatible')"""))
    st, vote, _ = run(apply_identity(ready_db, "w1",
        ident=_ident("attested", None, level="E2_resource_owner"),
        config_gen="g1", content_ok=True))
    assert (st, vote) == ("attested", False)
    row = run(ready_db.fetch_one(
        "SELECT identity_error_code, vote_eligible FROM body_witness_status WHERE witness_id='w1'"))
    assert row["identity_error_code"] == Err.EVIDENCE_BELOW_REQUIRED
    assert row["vote_eligible"] == 0
    kinds = [a["kind"] for a in run(ready_db.fetch_all("SELECT kind FROM body_alerts"))]
    assert "witness_identity_evidence_below" in kinds


def test_generic_s3_attested_votes_when_e2_required(ready_db, monkeypatch):
    monkeypatch.setenv("BODY_WITNESS_MIN_EVIDENCE", "E2")
    from body.witness_alerts import apply_identity
    run(ready_db.execute("""INSERT INTO body_witness_status (witness_id, provider)
        VALUES ('w1','s3-compatible')"""))
    _st, vote, _ = run(apply_identity(ready_db, "w1",
        ident=_ident("attested", None, level="E2_resource_owner"),
        config_gen="g1", content_ok=True))
    assert vote is True


# ═══ ⑦ 语音：跳变才开口；失败留文本不假报已播 ═══
def _row(wid, vote, ident_status="verified", reason=None, content="valid"):
    return {"witness_id": wid, "vote_eligible": 1 if vote else 0,
            "identity_status": ident_status, "evidence_level": "E3_authoritative_account",
            "content_status": content, "isolated": 0, "identity_error_code": reason}


def test_reconcile_emits_only_on_jumps(ready_db):
    from body.witness_voice import reconcile_witness_snapshot
    s1 = run(reconcile_witness_snapshot(ready_db, [_row("w1", True), _row("w2", True)],
                                        required=2))
    assert (s1["valid_count"], s1["events"]) == (2, 0)     # 首轮：健康，不开口
    s2 = run(reconcile_witness_snapshot(ready_db, [_row("w1", True),
                                                   _row("w2", False, "verified",
                                                        "EVIDENCE_BELOW_REQUIRED")],
                                        required=2))
    assert s2["valid_count"] == 1 and s2["events"] == 2     # 掉票 + 跌破要求数
    out = run(ready_db.fetch_all(
        "SELECT kind, reason_code, text, state FROM witness_voice_outbox ORDER BY kind"))
    kinds = {o["kind"] for o in out}
    assert {"vote_lost", "quorum_low"} <= kinds
    lost = [o for o in out if o["kind"] == "vote_lost"][0]
    assert lost["reason_code"] == "EVIDENCE_BELOW_REQUIRED"
    assert "掉票" in lost["text"] and "证据等级不足" in lost["text"]
    assert all(o["state"] == "pending" for o in out)
    # 同一跳变不重复开口（transition_id 唯一）
    run(reconcile_witness_snapshot(ready_db, [_row("w1", True),
                                              _row("w2", False, "verified",
                                                   "EVIDENCE_BELOW_REQUIRED")], required=2))
    assert len(run(ready_db.fetch_all("SELECT * FROM witness_voice_outbox"))) == len(out)


def test_voice_flush_marks_spoken_and_keeps_text_on_failure(ready_db):
    from body.witness_voice import flush_voice_outbox, reconcile_witness_snapshot
    run(reconcile_witness_snapshot(ready_db, [_row("w1", True), _row("w2", True)],
                                   required=2))
    run(reconcile_witness_snapshot(ready_db, [_row("w1", False, "critical_conflict",
                                                   "AUTHORITY_IDENTITY_CONFLICT"),
                                              _row("w2", True)], required=2))

    class OK:
        def __init__(self): self.said = []
        async def speak_priority(self, text, priority="normal", interrupt=False):
            self.said.append((text, priority, interrupt))

    ok = OK()
    r = run(flush_voice_outbox(ready_db, ok, degraded_cooldown=0))
    assert r["spoken"] == 1 and ok.said[0][1] == "critical"      # 冲突优先，立即念
    assert "权威身份冲突" in ok.said[0][0] and "1/2" in ok.said[0][0]
    states = run(ready_db.fetch_all(
        "SELECT state FROM witness_voice_outbox WHERE kind='conflict'"))
    assert states[0]["state"] == "spoken"

    # 播报失败：文本必须留着 + 面板告警 + 绝不标记为已播
    run(ready_db.execute("UPDATE witness_voice_outbox SET state='pending'"))
    run(reconcile_witness_snapshot(ready_db, [_row("w1", True), _row("w2", True)],
                                   required=2))
    run(reconcile_witness_snapshot(ready_db, [_row("w1", False, "critical_conflict",
                                                   "AUTHORITY_IDENTITY_CONFLICT"),
                                              _row("w2", True)], required=2))

    class Boom:
        async def speak_priority(self, text, priority="normal", interrupt=False):
            raise RuntimeError("tts down")

    r2 = run(flush_voice_outbox(ready_db, Boom(), degraded_cooldown=0))
    assert r2["spoken"] == 0
    row = run(ready_db.fetch_one(
        "SELECT state, text FROM witness_voice_outbox WHERE kind='conflict'"))
    assert row["state"] == "spoken_failed" and "权威身份冲突" in row["text"]
    kinds = [a["kind"] for a in run(ready_db.fetch_all("SELECT kind FROM body_alerts"))]
    assert "witness_voice_failed" in kinds


def test_boot_never_replays_old_voice(ready_db):
    from body.witness_voice import reconcile_witness_snapshot, voice_outbox_boot
    run(reconcile_witness_snapshot(ready_db, [_row("w1", True), _row("w2", True)], required=2))
    run(reconcile_witness_snapshot(ready_db, [_row("w1", False, "critical_conflict",
                                                   "AUTHORITY_IDENTITY_CONFLICT"),
                                              _row("w2", True)], required=2))
    run(ready_db.execute("UPDATE witness_voice_outbox SET state='playing'"))
    run(voice_outbox_boot(ready_db, pending_ttl=0))
    states = {r["state"] for r in run(ready_db.fetch_all(
        "SELECT state FROM witness_voice_outbox"))}
    assert states <= {"interrupted_unknown", "superseded"}      # 不重念
