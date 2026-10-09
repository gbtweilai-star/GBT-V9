# body/identity_b2.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from core.swallow import swallow as _swallow
import json, urllib.request
from urllib.parse import urlparse
from body.identity import Identity, Err, E2, E3, finalize
from body.net_guard import assert_safe_outbound_url

B2_AUTH = "https://api.backblazeb2.com/b2api/v3/b2_authorize_account"


def _code(e) -> str:
    resp = getattr(e, "response", {}) or {}
    return (resp.get("Error") or {}).get("Code") or type(e).__name__


def probe_b2(*, witness_id: str, endpoint: str, bucket: str, key_id: str,
             app_key: str, client, bucket_name_for_list: str | None = None) -> Identity:
    """E3 = b2_authorize_account.accountId；E2 = b2_list_buckets 里该桶的 accountId。"""
    ident = Identity(witness_id=witness_id, provider="backblaze-b2",
                     endpoint_host=(urlparse(endpoint).hostname or ""), bucket=bucket)

    # ── E3：native 授权，accountId 是权威 ──
    try:
        auth = _b2_auth(key_id, app_key)
    except PermissionError:
        ident.status, ident.error_code = "auth_failed", Err.AUTH_FAILED
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)
    except Exception as e:
        ident.status, ident.error_code = "unreachable", Err.UNREACHABLE
        ident.detail = {"where": "b2_authorize_account", "code": type(e).__name__}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)
    ident.account_id, ident.account_source = auth.get("accountId"), E3
    ident.credential_id = auth.get("applicationKeyId")     # 只作辅助识别

    # ── E2：该桶归属的账户（同授权域，属权威服务端声明）──
    try:
        owner = _b2_bucket_owner(auth, bucket_name_for_list or bucket)
    except Exception as e:
        ident.owner_id, ident.owner_source = None, None
        ident.detail["bucket_owner_probe"] = _code(e)
        if _code(e) in ("unauthorized", "403", "AccessDenied"):
            ident.error_code = Err.IDENTITY_FIELD_FORBIDDEN     # 权限不足, 不回退配置
    else:
        ident.owner_id, ident.owner_source = owner, E2
        if owner and ident.account_id and owner != ident.account_id:
            # 同授权域内的两个权威字段打架 → critical
            ident.status, ident.error_code = ("critical_conflict",
                                              Err.AUTHORITY_IDENTITY_CONFLICT)
            return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)

    # ── 弱对照：S3 ListBuckets 的 Owner.ID（官方未保证等于 accountId）──
    s3_owner = None
    try:
        r = client.list_buckets()
        s3_owner = (r.get("Owner") or {}).get("ID")
    except Exception as e:
        _swallow(__file__, e)
    if s3_owner and ident.account_id and s3_owner != ident.account_id:
        # 不给 critical：该字段语义无官方保证，只记事实
        ident.detail["s3_owner_crosscheck"] = "differs_from_account_id(unconfirmed_semantics)"

    return finalize(ident, cfg_account_claim=None, cfg_owner_claim=ident.owner_id)


def _b2_auth(key_id, app_key) -> dict:
    import base64
    tok = base64.b64encode(f"{key_id}:{app_key}".encode()).decode()
    assert_safe_outbound_url(B2_AUTH)               # host 校验后再发请求
    req = urllib.request.Request(B2_AUTH, headers={"Authorization": f"Basic {tok}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise PermissionError(f"http_{e.code}") from None
        raise
