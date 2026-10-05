# body/identity_s3.py —— 通用 S3 兼容 provider 的身份探测（R2/B2 之外的第三类）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用途: 任何 S3 兼容端点（MinIO / moto / 自建网关）都能作为见证存储。
# 证据等级: E2 —— ListBuckets 的 Owner.ID 是**服务端自己给出的**所有者声明
#          （不是配置里读来的声明，所以不是 E1；但也不是账户级权威证明，所以不是 E3）。
# 纪律: 探不到 Owner → identity_unverified，该 witness 不计 quorum（红线）。
from __future__ import annotations

from urllib.parse import urlparse

from body.identity import E2, Identity, Err, finalize


def probe_s3(*, witness_id: str, endpoint: str, bucket: str, client,
             provider: str = "s3-compatible",
             cfg_account_claim: str | None = None) -> Identity:
    """通用 S3：以 ListBuckets Owner.ID 作为所有者证据（E2）。"""
    ident = Identity(witness_id=witness_id, provider=provider,
                     endpoint_host=(urlparse(endpoint).hostname or ""), bucket=bucket)
    try:
        # ① 列桶：既有权限证明，也能拿到 Owner
        buckets = client.list_buckets()
    except Exception as e:
        # ★权限不足 ≠ 探不到：403 是"有端点、没权限"，必须与网络不通分开报，
        #   否则运维会去查网络；501 是"服务端不支持该调用"，也不能当成故障。
        code = _code(e)
        if code in ("AccessDenied", "403", "InvalidAccessKeyId",
                    "SignatureDoesNotMatch", "InvalidToken", "ExpiredToken",
                    "AuthorizationHeaderMalformed"):
            ident.status, ident.error_code = "auth_failed", Err.AUTH_FAILED
        elif code in ("NotImplemented", "501", "MethodNotAllowed",
                      "UnsupportedOperation"):
            ident.status, ident.error_code = "unsupported", Err.IDENTITY_FIELD_UNSUPPORTED
        else:
            ident.status, ident.error_code = "unreachable", Err.UNREACHABLE
        ident.detail = {"where": "list_buckets", "code": code}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)

    names = [b.get("Name") for b in buckets.get("Buckets", [])]
    if bucket not in names:
        ident.status, ident.error_code = "identity_unverified", Err.IDENTITY_UNVERIFIED
        ident.detail = {"reason": "bucket_not_listed", "bucket": bucket}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)

    owner = (buckets.get("Owner") or {}).get("ID")
    ident.owner_id, ident.owner_source = owner, E2
    if not owner:
        # 服务端没给 owner → 探不到就拒绝（绝不当 healthy）
        ident.status, ident.error_code = "identity_unverified", Err.IDENTITY_UNVERIFIED
        ident.detail = {"reason": "owner_id_absent"}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)

    ident.account_id, ident.account_source = owner, E2
    return finalize(ident, cfg_account_claim=cfg_account_claim, cfg_owner_claim=None)


def _code(exc) -> str:
    """S3 错误码提取（本地实现，不跨模块借私有函数）。"""
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code") or type(exc).__name__)


def capabilities_s3(client, bucket) -> dict:
    """通用 S3 能力：条件写（不可覆盖的关键）+ Object Lock / 版本控制。"""
    from body.identity_r2 import _probe_conditional_write
    cap = {"provider": "s3-compatible"}
    cap["conditional_write"] = _probe_conditional_write(client, bucket)
    for label, fn in (("versioning", "get_bucket_versioning"),
                      ("object_lock", "get_object_lock_configuration")):
        try:
            getattr(client, fn)(Bucket=bucket)
            cap[label] = {"supported": True}
        except Exception as e:
            code = _code(e)
            cap[label] = ({"supported": False, "reason": "not_configured"}
                          if code in ("NotImplemented", "501",
                                      "ObjectLockConfigurationNotFoundError",
                                      "NoSuchObjectLockConfiguration")
                          else {"unknown": code})
    return cap
