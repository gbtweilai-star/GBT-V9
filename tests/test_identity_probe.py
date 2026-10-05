# tests/test_identity_probe.py —— 身份探测单测（假 S3 + 假 Cloudflare 响应）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖四类情形（S3 与 Cloudflare 各一套）：
#   ① 权限不足（AccessDenied/403、CF 401/403）→ auth_failed，绝不当 healthy
#   ② 501 NotImplemented → unsupported，明确"服务端不支持"，不是故障
#   ③ 配置与服务端相反（配置声明 ≠ 服务端权威）→ config_mismatch，不选一个信
#   ④ 服务端自相矛盾（两个权威源打架）→ critical_conflict（critical）
# 外加：探不到 → identity_unverified 且【不计票】；过期/证据不足 → countable=False
#
# 全程离线：不连任何真实端点；CF 响应由假 urlopen 提供，S3 由假 client 提供。
import json
import urllib.error

import pytest

from body.identity import (E1, E2, E3, Err, Identity, countable, finalize)
from body.identity_r2 import capabilities_r2, probe_r2
from body.identity_s3 import capabilities_s3, probe_s3

ENDPOINT = "https://acct-abc123.r2.cloudflarestorage.com"


# ─────────── 假 S3 client ───────────
class S3Error(Exception):
    """boto3 ClientError 的最小复刻：错误码在 .response['Error']['Code']。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3:
    def __init__(self, *, buckets=None, owner="acct-owner-1", raise_on_list=None,
                 caps=None, accept_overwrite=False):
        self._buckets = buckets if buckets is not None else ["wit-a"]
        self._owner = owner
        self._raise = raise_on_list
        self._caps = caps if caps is not None else {}
        self._accept_overwrite = accept_overwrite
        self._keys: set[str] = set()
        self.calls: list[tuple] = []

    def list_buckets(self):
        self.calls.append(("list_buckets",))
        if self._raise:
            raise S3Error(self._raise)
        body = {"Buckets": [{"Name": b} for b in self._buckets]}
        if self._owner is not None:
            body["Owner"] = {"ID": self._owner}
        return body

    def get_bucket_versioning(self, Bucket=None):
        return self._maybe("versioning")

    def get_object_lock_configuration(self, Bucket=None):
        return self._maybe("object_lock")

    def put_object(self, Bucket=None, Key=None, Body=None, IfNoneMatch=None):
        """条件写语义：首次写入成功；IfNoneMatch=* 命中已存在的 key → 412（除非服务端会覆盖）。"""
        self.calls.append(("put_object", Key))
        if Key in self._keys and IfNoneMatch == "*" and not self._accept_overwrite:
            raise S3Error("PreconditionFailed")
        self._keys.add(Key)
        return {}

    def _maybe(self, label):
        v = self._caps.get(label, "unsupported")
        if v == "unsupported":
            raise S3Error("NotImplemented")
        return {}


# ─────────── 假 Cloudflare HTTP 响应 ───────────
class FakeHTTP:
    def __init__(self, payload: dict, status: int = 200):
        self._body = json.dumps(payload).encode()
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_cf(monkeypatch, payload=None, status=200):
    """把 urlopen 换成假 Cloudflare：真实解析代码不动，只替换响应。"""
    import body.identity_r2 as R2

    def _urlopen(req, timeout=None):
        if status >= 400:
            raise urllib.error.HTTPError(req.full_url, status, "denied", {}, None)
        return FakeHTTP(payload or {})

    monkeypatch.setattr(R2.urllib.request, "urlopen", _urlopen)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("BODY_WITNESS_FP_KEY", "k" * 32)
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)


# ═══ ① 权限不足：有端点、没权限 → auth_failed（不是 unreachable，也不是 healthy）═══
def test_s3_permission_denied_is_auth_failed():
    for code in ("AccessDenied", "403", "InvalidAccessKeyId", "SignatureDoesNotMatch"):
        ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                         client=FakeS3(raise_on_list=code))
        assert ident.status == "auth_failed", code
        assert ident.error_code == Err.AUTH_FAILED
        assert ident.detail["code"] == code
        ok, why = countable(ident, min_evidence="E2", fresh=True)
        assert (ok, why) == (False, Err.AUTH_FAILED)


def test_cf_permission_denied_is_auth_failed(monkeypatch):
    fake_cf(monkeypatch, status=403)
    ident = probe_r2(witness_id="w1", endpoint=ENDPOINT, bucket="wit-a",
                     client=FakeS3(owner="acct-abc123"), cf_api_token="tok")
    assert ident.status == "auth_failed"
    assert ident.error_code == Err.AUTH_FAILED
    ok, why = countable(ident, min_evidence="E2", fresh=True)
    assert ok is False and why == Err.AUTH_FAILED


# ═══ ② 501：服务端不支持该调用 → unsupported（明确能力缺失，不是故障）═══
def test_s3_501_is_unsupported_not_failure():
    for code in ("NotImplemented", "501", "UnsupportedOperation"):
        ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                         client=FakeS3(raise_on_list=code))
        assert ident.status == "unsupported", code
        assert ident.error_code == Err.IDENTITY_FIELD_UNSUPPORTED
        # ★不支持 ≠ 已确认：一样不计票
        assert countable(ident, min_evidence="E2", fresh=True)[0] is False


def test_capabilities_501_marked_not_implemented():
    cap = capabilities_s3(FakeS3(), "wit-a")
    assert cap["versioning"] == {"supported": False, "reason": "not_configured"}
    assert cap["object_lock"] == {"supported": False, "reason": "not_configured"}
    r2cap = capabilities_r2(FakeS3(), "wit-a")
    assert r2cap["versioning"] == {"supported": False, "reason": "not_implemented"}
    # 条件写实测：服务端接受覆盖 → 明确记为不支持（不可覆盖写是见证的命门）
    loose = capabilities_r2(FakeS3(accept_overwrite=True), "wit-a")
    assert loose["conditional_write"]["supported"] is False
    assert loose["conditional_write"]["reason"] == "server_accepted_overwrite"


def test_capabilities_conditional_write_supported_when_412():
    cap = capabilities_r2(FakeS3(), "wit-a")
    assert cap["conditional_write"]["supported"] is True
    assert cap["conditional_write"]["reason"] == "412"


# ═══ ③ 配置与服务端相反 → config_mismatch（不选一个信）═══
def test_s3_config_opposite_of_server_is_mismatch():
    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                     client=FakeS3(owner="acct-server"), cfg_account_claim="acct-config")
    assert ident.status == "config_mismatch"
    assert ident.error_code == Err.CONFIG_IDENTITY_MISMATCH
    assert ident.owner_id == "acct-server"           # 服务端读数如实保留，不被配置覆盖
    assert countable(ident, min_evidence="E2", fresh=True)[0] is False


def test_s3_config_matching_server_is_attested_not_authoritative():
    """对照：配置与服务端一致 → E2 attested（可计票），且显式标注非 E3 权威。"""
    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                     client=FakeS3(owner="acct-same"), cfg_account_claim="acct-same")
    assert (ident.status, ident.evidence_level) == ("attested", E2)
    assert ident.detail.get("note", "").startswith("E2 自洽")
    assert countable(ident, min_evidence="E2", fresh=True) == (True, "ok")
    # 但要求 E3 的场合 → 显式不足（原因必须是规范错误码，与告警表同拼写）
    assert countable(ident, min_evidence="E3", fresh=True) == (False, Err.EVIDENCE_BELOW_REQUIRED)


def test_cf_account_not_in_token_scope_is_mismatch(monkeypatch):
    fake_cf(monkeypatch, {"success": True, "result": [{"id": "acct-OTHER"}]})
    ident = probe_r2(witness_id="w1", endpoint=ENDPOINT, bucket="wit-a",
                     client=FakeS3(owner="acct-abc123"), cf_api_token="tok")
    assert ident.status == "config_mismatch"
    assert ident.error_code == Err.CONFIG_IDENTITY_MISMATCH
    assert countable(ident, min_evidence="E3", fresh=True)[0] is False


def test_cf_host_claim_not_r2_domain_is_mismatch():
    ident = probe_r2(witness_id="w1", endpoint="https://s3.example.com", bucket="wit-a",
                     client=FakeS3(), cf_api_token=None)
    assert ident.status == "config_mismatch"
    assert ident.error_code == Err.CONFIG_IDENTITY_MISMATCH
    assert "r2.cloudflarestorage.com" in ident.detail["reason"]


def test_cf_token_confirms_account_is_e3_and_countable(monkeypatch):
    fake_cf(monkeypatch, {"success": True,
                          "result": [{"id": "acct-abc123"}, {"id": "acct-x"}]})
    ident = probe_r2(witness_id="w1", endpoint=ENDPOINT, bucket="wit-a",
                     client=FakeS3(owner="acct-abc123"), cf_api_token="tok")
    assert (ident.status, ident.evidence_level) == ("verified", E3)
    assert ident.account_source == E3
    assert ident.detail["member_of"] == 2            # 只记数量
    assert countable(ident, min_evidence="E3", fresh=True) == (True, "ok")


def test_cf_api_success_false_is_auth_failed(monkeypatch):
    fake_cf(monkeypatch, {"success": False, "errors": [{"code": 10000}]})
    ident = probe_r2(witness_id="w1", endpoint=ENDPOINT, bucket="wit-a",
                     client=FakeS3(), cf_api_token="tok")
    assert ident.status == "auth_failed"
    assert ident.error_code == Err.AUTH_FAILED


def test_cf_token_absent_keeps_host_claim_as_e1_weak(monkeypatch):
    fake_cf(monkeypatch, {"success": True, "result": []})
    ident = probe_r2(witness_id="w1", endpoint=ENDPOINT, bucket="wit-a",
                     client=FakeS3(owner="acct-abc123"), cf_api_token=None)
    assert ident.account_source == E1                # 配置推导，不是证据
    assert "upgrade_hint" in ident.detail
    assert countable(ident, min_evidence="E3", fresh=True)[0] is False


# ═══ ④ 服务端自相矛盾 + 探不到：critical_conflict / identity_unverified ═══
def test_two_authoritative_sources_disagree_is_critical_conflict():
    ident = Identity(witness_id="w1", provider="cloudflare-r2",
                     endpoint_host="x.r2.cloudflarestorage.com", bucket="b")
    ident.account_id, ident.account_source = "acct-abc", E3
    ident.owner_id, ident.owner_source = "acct-zzz", E2
    ident = finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)
    assert ident.status == "critical_conflict"
    assert ident.error_code == Err.AUTHORITY_IDENTITY_CONFLICT
    assert countable(ident, min_evidence="E2", fresh=True)[0] is False


def test_s3_owner_absent_is_unverified_never_healthy():
    """红线：探不到就拒绝 —— 不管服务端多"正常"，没有 owner 就不计票。"""
    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                     client=FakeS3(owner=None))
    assert ident.status == "identity_unverified"
    assert ident.error_code == Err.IDENTITY_UNVERIFIED
    assert ident.detail["reason"] == "owner_id_absent"
    assert countable(ident, min_evidence="E2", fresh=True)[0] is False


def test_s3_bucket_not_listed_is_unverified():
    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="missing",
                     client=FakeS3(buckets=["other"]))
    assert ident.status == "identity_unverified"
    assert ident.detail["reason"] == "bucket_not_listed"
    assert countable(ident, min_evidence="E2", fresh=True)[0] is False


def test_network_failure_is_unreachable_not_healthy():
    class Boom(FakeS3):
        def list_buckets(self):
            raise ConnectionError("dns")

    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                     client=Boom())
    assert ident.status == "unreachable"
    assert ident.error_code == Err.UNREACHABLE
    assert countable(ident, min_evidence="E2", fresh=True)[0] is False


# ═══ 计票闸门：过期即失效（缓存不得计票）═══
def test_stale_identity_never_counts():
    ident = probe_s3(witness_id="w1", endpoint="https://s3.local", bucket="wit-a",
                     client=FakeS3(owner="acct-same"), cfg_account_claim="acct-same")
    assert countable(ident, min_evidence="E2", fresh=True)[0] is True
    assert countable(ident, min_evidence="E2", fresh=False) == (False, "IDENTITY_STALE")
