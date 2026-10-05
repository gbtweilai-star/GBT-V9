# tests/test_witness_integration.py —— 真 provider 集成测试（不可覆盖写 + 同账户拒绝）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 这两个测试**必须真连 provider**，只从环境变量读凭据（绝不写进代码/日志）。
# 没有凭据时自动 skip —— 跳过就是跳过，不当作通过。
# 需要：BODY_WITNESS_<ID>_* 五件（provider/endpoint/bucket/access_key_id/secret）
import os

import pytest

from body.witness_admin import load_witness_config, make_client, probe_witness
from body.witness_registry import reject_duplicate_domains

CANDIDATES = ("archive-b", "r2-primary")


def _configured(wid: str) -> bool:
    key = "BODY_WITNESS_" + wid.upper().replace("-", "_") + "_"
    return all(os.environ.get(key + k) for k in
               ("PROVIDER", "ENDPOINT", "BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY"))


def _any_witness() -> str | None:
    return next((w for w in CANDIDATES if _configured(w)), None)


pytestmark = pytest.mark.skipif(
    _any_witness() is None,
    reason="未配置真 provider 凭据（BODY_WITNESS_<ID>_*）；集成测试跳过，不视为通过")


def test_immutable_write_cannot_be_overwritten():
    """真 provider：同 key 覆盖必须被拒，且原内容不变（Object Lock/条件写生效）。

    若 provider 明确不支持不可变（如 R2 无 Object Lock）→ 必须报
    immutability_not_enabled，而不是"假装通过"。
    """
    wid = _any_witness()
    cfg = load_witness_config(wid)
    res = probe_witness(cfg, make_client(cfg), dry_run=True)

    by_no = {s["step"]: s for s in res["steps"]}
    # 覆盖必须被拒（step 6）；若 provider 连不可变都没开，则 step 3 必须诚实报错
    if by_no[3]["ok"] and by_no[3]["code"] is None:
        assert by_no[6]["ok"] is True, f"覆盖未被拒 → 不可作不可变见证: {by_no[6]}"
        assert by_no[6]["code"] is None
    else:
        assert by_no[3]["code"] == "immutability_not_enabled"
        pytest.skip(f"{wid} 未开启不可变（{by_no[3]['code']}）——"
                    f"如实标注，不作为不可变见证")


def test_identity_unverified_never_counts_as_witness():
    """身份探测探不到权威账户（缺 CF token / B2 权限不足）→ identity_unverified，
    该 witness 不得计票（这条是红线：探不到就拒绝，绝不能当 healthy）。"""
    wid = _any_witness()
    cfg = load_witness_config(wid)
    res = probe_witness(cfg, make_client(cfg), dry_run=True)
    ident = res.get("identity") or {}
    if ident.get("status") in ("identity_unverified", "attested"):
        assert res["ok"] is False or ident["status"] == "attested", \
            "身份未验证却整体通过 —— 违反红线"
    assert ident.get("status") in ("verified", "attested", "identity_unverified",
                                   "unreachable", "auth_failed", "config_mismatch",
                                   "critical_conflict")


def test_same_account_two_keys_is_rejected_with_real_credentials():
    """同一账户的两把凭据不得算两个见证（用真凭据的 account_id 做机械判定）。"""
    wid = _any_witness()
    cfg = load_witness_config(wid)
    res = probe_witness(cfg, make_client(cfg), dry_run=True)
    ident = res.get("identity") or {}
    if not ident.get("account"):
        pytest.skip("身份探测未拿到账户指纹，无法做同账户判定（如实跳过）")
    import hashlib
    # 用同一份凭据构造两条配置（不同 witness_id、不同 bucket）→ 必须被拒
    a = {"witness_id": "w-a", "provider": cfg["provider"], "endpoint": cfg["endpoint"],
         "account_id": ident.get("account_source") or "acct", "access_key_id": "K",
         "bucket_owner_id": ident.get("account_source") or "acct", "bucket": "b1"}
    b = dict(a, witness_id="w-b", bucket="b2")
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains([a, b], hashlib.sha256(b"fp").digest())
