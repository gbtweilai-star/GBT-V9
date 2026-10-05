# tests/integration/test_credential_domain.py
"""凭据域独立性。

⚠ 诚实边界：本文件里标记 [GUARD] 的用例是「用真实探测结果合成同账户配置」，
   证明的是守卫能拦住同账户，**不是**证明两个真实账户互相独立。
   真实独立性只能由 [REAL] 那一条证明 —— 它需要两套真实凭据都在场。
"""
import os
import pytest
from body.witness_registry import (reject_duplicate_domains, witness_fingerprints)
from tests.integration.conftest import safe_fp

pytestmark = pytest.mark.integration
FP_KEY = os.getenv("BODY_WITNESS_FP_KEY", "it-fp-key").encode()


def _probe_identity(provider) -> dict:
    """真实探测 account_id / bucket_owner_id；探测不到就抛，绝不猜。"""
    account = provider.capabilities_sync().get("account_id")
    owner = provider.bucket_owner_sync()
    if not account or not owner:
        raise ValueError("identity_probe_incomplete")
    return {"witness_id": provider.name, "provider": provider.provider,
            "endpoint": provider.endpoint, "bucket": provider.bucket,
            "access_key_id": provider.key_id,
            "account_id": account, "bucket_owner_id": owner}


# ═══ [REAL] 两套真实凭据必须落在不同账户 ═══
def test_real_providers_are_in_different_accounts(providers):
    ids = [_probe_identity(p) for p in providers.values()]
    reject_duplicate_domains(ids, FP_KEY)              # 通过 = 两个真实账户确实独立
    f = [witness_fingerprints(i, FP_KEY) for i in ids]
    assert f[0]["account_domain"] != f[1]["account_domain"]
    assert f[0]["credential_domain"] != f[1]["credential_domain"]


# ═══ [GUARD] 四类"看着独立、其实同账户"的伪装，逐个反向验证 ═══
@pytest.fixture(scope="module")
def real_identity(providers):
    return _probe_identity(providers["r2-witness"])


@pytest.mark.parametrize("mutate,label", [
    (lambda w: {**w, "witness_id": "clone-access-key",
                "access_key_id": "ANOTHERKEYINDSAMEACCOUNT000000"}, "换 access key"),
    (lambda w: {**w, "witness_id": "clone-bucket", "bucket": "another-bucket-same-account"},
     "换 bucket"),
    (lambda w: {**w, "witness_id": "clone-endpoint",
                "endpoint": "https://same-account-other-endpoint.example.com"}, "换同账户 endpoint"),
    (lambda w: {**w, "witness_id": "clone-provider-name", "provider": "cloudflare-r2",
                "access_key_id": w["access_key_id"]}, "换 provider 名"),
])
def test_same_account_disguises_are_rejected(real_identity, mutate, label):
    dup = mutate(real_identity)
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains([real_identity, dup], FP_KEY)


# ═══ [GUARD] 身份探测不完整必须拒绝，不许默认独立 ═══
def test_incomplete_identity_probe_is_refused(real_identity):
    for missing in ("account_id", "bucket_owner_id", "access_key_id", "endpoint", "bucket"):
        w = {**real_identity, missing: None}
        with pytest.raises(ValueError, match="identity_probe_incomplete"):
            witness_fingerprints(w, FP_KEY)


def test_bucket_owner_mismatch_is_refused(real_identity):
    with pytest.raises(ValueError, match="bucket_owner_account_mismatch"):
        witness_fingerprints({**real_identity, "bucket_owner_id": "somebody-else"}, FP_KEY)


# ═══ [GUARD] identity_unverified 的见证不得计票 ═══
def test_identity_unverified_witness_excluded_from_quorum():
    from body.witness_registry import quorum
    class K:
        def verify(self, kid, rec): return True
    t = {"root_id": "main", "epoch": "e1", "seq": 7, "head_hash": "H7"}
    r = quorum([{"witness_id": "unverified-x", "kid": "x/1", "seq": 7, "head_hash": "H7",
                 "root_id": "main", "epoch": "e1"}], target=t, keyring=K(),
               revoked_kids=set(), live_from_seq={}, required=2,
               excluded_witnesses={"unverified-x"})
    assert r["count"] == 0 and r["status"] == "degraded"
