# tests/test_witness_rules.py —— 见证规则：凭据域指纹 / quorum 计票 / 去抖状态机 / 身份判定
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 断言全部落在**真库行**（body_alerts / body_blocked_log / body_witness_status），
# 不看 mock 调用次数；反例（"不该报警""不该合并""不该计票"）与正例成对出现。
import asyncio
import hmac
import tempfile
from pathlib import Path

import pytest

from body import identity as ID
from body.adapters.sqlite_db import SqliteDb
from body.identity import Identity, countable, finalize
from body.witness_probe import _apply_status
from body.witness_registry import (fp, reject_duplicate_domains,
                                   witness_fingerprints, quorum)
from migrations.runner import apply_pending

FP_KEY = b"unit-test-fp-key-not-a-credential"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def wdb(tmp_path):
    db = run(SqliteDb.open(str(tmp_path / "witness.db")))
    run(apply_pending(db))
    return db


def st(status, detail="-", seq=9, kid="k/1", provider="p"):
    return {"status": status, "detail": detail, "seq": seq, "kid": kid,
            "provider": provider}


def _witness(wid, provider, account, key_id, endpoint="https://a.example.com",
             bucket="b1", owner=None):
    return {"witness_id": wid, "provider": provider, "endpoint": endpoint,
            "account_id": account, "bucket_owner_id": owner or account,
            "access_key_id": key_id, "bucket": bucket}


# ═══════════ 凭据域指纹：机械证明"这是两个账户" ═══════════
def test_same_provider_same_account_same_key_is_rejected():
    ws = [_witness("w1", "cloudflare-r2", "acct-1", "key-A"),
          _witness("w2", "cloudflare-r2", "acct-1", "key-A")]
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains(ws, FP_KEY)


def test_same_account_different_endpoint_still_rejected():
    """★反例核心：endpoint 不同 ≠ 账户独立；同账户两桶照样一次泄露全丢。"""
    ws = [_witness("w1", "cloudflare-r2", "acct-1", "key-A",
                   endpoint="https://a.example.com", bucket="b1"),
          _witness("w2", "cloudflare-r2", "acct-1", "key-A",
                   endpoint="https://b.example.com", bucket="b2")]
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains(ws, FP_KEY)


def test_two_accounts_two_providers_accepted():
    ws = [_witness("r2-primary", "cloudflare-r2", "acct-1", "key-A"),
          _witness("archive-b", "backblaze-b2", "acct-2", "key-B")]
    reject_duplicate_domains(ws, FP_KEY)               # 不抛：真独立
    f1, f2 = ws[0]["_fp"], ws[1]["_fp"]
    assert f1["account_domain"] != f2["account_domain"]
    assert f1["credential_domain"] != f2["credential_domain"]


def test_same_account_across_providers_is_still_same_account_domain():
    """同一账户号在两家 provider 下：account_domain 带 provider 名 → 视为不同域，
    但 credential_domain 也不同（不同 key）。这条是文档化的语义，必须显式断言。"""
    ws = [_witness("a", "cloudflare-r2", "acct-9", "key-A"),
          _witness("b", "backblaze-b2", "acct-9", "key-B")]
    reject_duplicate_domains(ws, FP_KEY)
    assert ws[0]["_fp"]["account_domain"] != ws[1]["_fp"]["account_domain"]


def test_fingerprints_require_identity_probe():
    bad = {"witness_id": "x", "provider": "cloudflare-r2",
           "endpoint": "https://a.example.com", "bucket": "b"}
    with pytest.raises(ValueError, match="identity_probe_incomplete"):
        witness_fingerprints(bad, FP_KEY)


def test_bucket_owner_must_match_account():
    w = _witness("x", "cloudflare-r2", "acct-1", "key-A", owner="acct-2")
    with pytest.raises(ValueError, match="bucket_owner_account_mismatch"):
        witness_fingerprints(w, FP_KEY)


def test_fingerprint_is_hmac_not_plaintext():
    w = _witness("x", "cloudflare-r2", "acct-1", "key-A")
    f = witness_fingerprints(w, FP_KEY)
    assert f["credential_domain"] == hmac.new(
        FP_KEY, b'["credential","cloudflare-r2","acct-1","key-A"]',
        __import__("hashlib").sha256).hexdigest()      # 口径确定：HMAC 可控复算
    assert "key-A" not in f["credential_domain"]       # 绝不含原文


# ═══════════ quorum：按 witness 去重、回填不计、冲突 critical ═══════════
class _Keyring:
    def __init__(self, mapping): self.m = mapping
    def verify(self, kid, rec): return bool(self.m.get(kid, False))


TARGET = {"root_id": "main", "epoch": "e1", "seq": 5, "head_hash": "H5"}


def rec(w, kid, seq=5, head="H5", epoch="e1"):
    return {"witness_id": w, "kid": kid, "seq": seq, "head_hash": head,
            "root_id": "main", "epoch": epoch}


def test_two_independent_witnesses_healthy():
    r = quorum([rec("r2-primary", "r2/1"), rec("archive-b", "ab/1")], target=TARGET,
               keyring=_Keyring({"r2/1": True, "ab/1": True}), revoked_kids=set(),
               live_from_seq={}, required=2)
    assert (r["status"], r["count"]) == ("healthy", 2)


def test_retired_kid_still_counts_but_revoked_does_not():
    ks = _Keyring({"r2/old": True, "r2/leaked": True, "ab/1": True})
    ok = quorum([rec("r2-primary", "r2/old"), rec("archive-b", "ab/1")], target=TARGET,
                keyring=ks, revoked_kids=set(), live_from_seq={}, required=2)
    assert ok["status"] == "healthy"                   # 退役只是停签，旧票仍有效
    bad = quorum([rec("r2-primary", "r2/leaked"), rec("archive-b", "ab/1")],
                 target=TARGET, keyring=ks, revoked_kids={"r2/leaked"},
                 live_from_seq={}, required=2)
    assert bad["status"] == "degraded" and bad["valid_witnesses"] == ["archive-b"]


def test_two_kids_of_same_witness_is_one_vote():
    r = quorum([rec("archive-b", "ab/old"), rec("archive-b", "ab/new")], target=TARGET,
               keyring=_Keyring({"ab/old": True, "ab/new": True}), revoked_kids=set(),
               live_from_seq={}, required=2)
    assert (r["count"], r["status"]) == (1, "degraded")   # 不是 2/healthy


def test_backfilled_history_not_counted_as_live():
    r = quorum([rec("archive-b", "ab/1", seq=3, head="H3"),
                rec("r2-primary", "r2/1", seq=3, head="H3")],
               target={"root_id": "main", "epoch": "e1", "seq": 3, "head_hash": "H3"},
               keyring=_Keyring({"ab/1": True, "r2/1": True}), revoked_kids=set(),
               live_from_seq={"archive-b": 4}, required=2)
    assert (r["count"], r["status"]) == (1, "degraded")   # 事后回填 ≠ 当时见证
    assert r["valid_witnesses"] == ["r2-primary"]         # 只有它在实时区间内


def test_conflicting_head_hash_is_critical_not_ignored():
    """★反向用例：同 seq 两个见证哈希不同 → critical（不得静默忽略）。"""
    r = quorum([rec("r2-primary", "r2/1"), rec("archive-b", "ab/1", head="H5-BAD")],
               target=TARGET, keyring=_Keyring({"r2/1": True, "ab/1": True}),
               revoked_kids=set(), live_from_seq={}, required=2)
    assert (r["status"], r["conflicting"]) == ("critical", ["archive-b"])


def test_other_epoch_records_ignored():
    r = quorum([rec("archive-b", "ab/1", epoch="e0")], target=TARGET,
               keyring=_Keyring({"ab/1": True}), revoked_kids=set(),
               live_from_seq={}, required=2)
    assert r["count"] == 0


# ═══════════ 去抖状态机：阈值/重启/恢复/critical（读真行） ═══════════
def _fail(wdb, wid="archive-b", t=1000.0):
    run(_apply_status(wdb, wid, st("unreachable", "timeout"), now_fn=lambda: t))


def test_threshold_minus_one_no_alert_but_one_blocked(wdb):
    _fail(wdb, t=1000.0)
    _fail(wdb, t=1001.0)
    row = run(wdb.fetch_one(
        "SELECT consecutive_fail, degraded_alerted FROM body_witness_status "
        "WHERE witness_id=?", ("archive-b",)))
    assert row["consecutive_fail"] == 2 and row["degraded_alerted"] == 0
    assert run(wdb.alerts("witness_degraded")) == []           # 阈值前一刻不报警
    assert len(run(wdb.blocked("witness_probe_transient"))) == 1   # 首次失败只记 blocked


def test_third_failure_alerts_exactly_once_even_if_polled_more(wdb):
    for t in (1000.0, 1001.0, 1002.0, 1003.0, 1004.0):
        _fail(wdb, t=t)
    assert len(run(wdb.alerts("witness_degraded", level="warning"))) == 1
    row = run(wdb.fetch_one(
        "SELECT degraded_alerted FROM body_witness_status WHERE witness_id=?",
        ("archive-b",)))
    assert row["degraded_alerted"] == 1


def test_time_threshold_two_minutes_triggers_even_with_few_failures(wdb):
    _fail(wdb, t=1000.0)
    _fail(wdb, t=1000.0 + 121)                                 # 跨越 2 分钟
    alerts = run(wdb.alerts("witness_degraded"))
    assert len(alerts) == 1
    assert "elapsed_s" in alerts[0]["payload_json"]


def test_state_survives_restart(tmp_path):
    """★重启不失忆：新连接新实例，计数与首次失败时间从库里读。"""
    path = str(tmp_path / "w.db")
    a = run(SqliteDb.open(path))
    run(apply_pending(a))
    _fail(a, t=1000.0)
    _fail(a, t=1001.0)
    b = run(SqliteDb.open(path))                               # 模拟重启
    _fail(b, t=1002.0)
    row = run(b.fetch_one(
        "SELECT consecutive_fail, first_fail_at, degraded_alerted "
        "FROM body_witness_status WHERE witness_id=?", ("archive-b",)))
    assert row["consecutive_fail"] == 3                        # 计数没归零
    assert row["first_fail_at"]                                # 首次失败时间已落库
    assert len(run(b.alerts("witness_degraded"))) == 1


def test_recovery_needs_two_successes_and_clears_flags(wdb):
    for t in (1000.0, 1001.0, 1002.0):
        _fail(wdb, t=t)
    run(_apply_status(wdb, "archive-b", st("valid"), now_fn=lambda: 1010.0))
    assert run(wdb.alerts("witness_recovered")) == []          # 1 次成功不清警
    run(_apply_status(wdb, "archive-b", st("valid"), now_fn=lambda: 1020.0))
    assert len(run(wdb.alerts("witness_recovered", level="info"))) == 1
    row = run(wdb.fetch_one(
        "SELECT consecutive_fail, degraded_alerted, first_fail_at "
        "FROM body_witness_status WHERE witness_id=?", ("archive-b",)))
    assert (row["consecutive_fail"], row["degraded_alerted"],
            row["first_fail_at"]) == (0, 0, None)


def test_transient_unreachable_never_raises_critical(wdb):
    """★反例：瞬时不可达 ≠ 篡改。"""
    for t in (1000.0, 1060.0, 1120.0, 1180.0, 1240.0):
        _fail(wdb, t=t)
    assert run(wdb.alerts("witness_critical")) == []


def test_invalid_is_immediate_and_deduped(wdb):
    for t in (1000.0, 1001.0, 1002.0, 1003.0):
        run(_apply_status(wdb, "r2-primary", st("invalid", "sig mismatch"),
                          now_fn=lambda: t))
    assert len(run(wdb.alerts("witness_critical", level="critical"))) == 1
    assert run(wdb.blocked()) == []                            # critical 不走去抖路径


def test_success_does_not_clear_critical(wdb):
    """★反例：critical 只能人工 ack 清除，恢复绿也不许自动撤。"""
    run(_apply_status(wdb, "r2-primary", st("disagreement"), now_fn=lambda: 1000.0))
    for t in (1010.0, 1020.0, 1030.0):
        run(_apply_status(wdb, "r2-primary", st("valid"), now_fn=lambda: t))
    row = run(wdb.fetch_one(
        "SELECT live_status, critical_alerted FROM body_witness_status "
        "WHERE witness_id=?", ("r2-primary",)))
    assert row["live_status"] == "valid" and row["critical_alerted"] == 1
    assert len(run(wdb.alerts("witness_critical"))) == 1


# ═══════════ 身份判定：证据不足 = 少一票（identity_unverified 不计票） ═══════════
def _ident(status="attested", level=ID.E2, account="acct-1"):
    i = Identity(witness_id="w", provider="cloudflare-r2",
                 endpoint_host="a.example.com", bucket="b")
    i.status, i.evidence_level, i.account_id, i.account_source = \
        status, level, account, level
    return i


def test_identity_unverified_is_not_countable():
    i = _ident(status="identity_unverified", level=ID.E1)
    ok, why = countable(i, min_evidence="E3", fresh=True)
    assert ok is False and why in (ID.Err.IDENTITY_UNVERIFIED,
                                   "identity_unverified", "evidence_below_required")


def test_attested_ok_for_e2_but_not_e3():
    i = _ident(status="attested", level=ID.E2)
    assert countable(i, min_evidence="E2", fresh=True)[0] is True
    assert countable(i, min_evidence="E3", fresh=True)[0] is False


def test_stale_identity_never_counts():
    i = _ident(status="verified", level=ID.E3)
    ok, why = countable(i, min_evidence="E3", fresh=False)
    assert ok is False and why == "IDENTITY_STALE"   # 规范错误码


def test_finalize_marks_unverified_without_config_evidence():
    i = Identity(witness_id="w", provider="cloudflare-r2",
                 endpoint_host="a.example.com", bucket="b")
    out = finalize(i, cfg_account_claim=None, cfg_owner_claim=None)
    assert out.status == "identity_unverified"
    assert out.evidence_level == ID.E1
