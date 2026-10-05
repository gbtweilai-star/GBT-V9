# body/identity_r2.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json, os, urllib.request
from urllib.parse import urlparse
from body.identity import Identity, Err, E1, E2, E3, finalize
from body.net_guard import assert_safe_outbound_url

CF_API = "https://api.cloudflare.com/client/v4/accounts"


def _code(exc) -> str:
    """S3/CF 错误码提取（本模块多处调用，此前未定义 → 运行时 NameError）。"""
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code") or type(exc).__name__)


def _host_account_claim(endpoint: str) -> str | None:
    """E1：官方规定 endpoint 主机名首段即账户 ID —— 但这是【配置推导】, 不是证据。"""
    host = (urlparse(endpoint).hostname or "").lower()
    if not host.endswith(".r2.cloudflarestorage.com"):
        return None
    seg = host.split(".")[0]
    return seg if seg and seg != "r2" else None


def _cf_account_ids(api_token: str) -> list[str]:
    """E3 来源：token 能访问的账户集合。← 对齐点：换成你现有 Cloudflare 客户端。"""
    assert_safe_outbound_url(CF_API)                # host 校验后再发请求
    req = urllib.request.Request(CF_API, headers={
        "Authorization": f"Bearer {api_token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        body = json.loads(r.read().decode())
    if not body.get("success", False):
        # ★CF 的 errors[].code 是数字：直接 join 会抛 TypeError，
        #   把"权限被拒"变成"未知异常"，于是静默降级成弱证据 —— 必须先转成字符串。
        raise PermissionError(",".join(str(e.get("code", "")) for e in body.get("errors", [])))
    return [a["id"] for a in body.get("result", []) if a.get("id")]


def _s3_owner_id(client, bucket) -> str | None:
    """E2（弱）：官方未确认 Owner.ID 语义，只作对照，不作权威。"""
    try:
        r = client.list_buckets()
        return (r.get("Owner") or {}).get("ID")
    except Exception:
        return None


def probe_r2(*, witness_id: str, endpoint: str, bucket: str, client,
             cf_api_token: str | None = None) -> Identity:
    ident = Identity(witness_id=witness_id, provider="cloudflare-r2",
                     endpoint_host=(urlparse(endpoint).hostname or ""),
                     bucket=bucket)
    claim = _host_account_claim(endpoint)
    if not claim:
        ident.status, ident.error_code = "config_mismatch", Err.CONFIG_IDENTITY_MISMATCH
        ident.detail = {"reason": "endpoint 不是 <ACCOUNT_ID>.r2.cloudflarestorage.com 形式"}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)

    # E2 对照：ListBuckets 的 Owner.ID（可能不存在；官方未确认语义）
    try:
        owner = _s3_owner_id(client, bucket)
    except Exception as e:
        ident.status, ident.error_code = "unreachable", Err.UNREACHABLE
        ident.detail = {"where": "s3_list_buckets", "code": type(e).__name__}
        return finalize(ident, cfg_account_claim=None, cfg_owner_claim=None)
    ident.owner_id, ident.owner_source = owner, E2
    if owner and owner != claim:
        # Owner.ID 语义未获官方保证 → 记为弱对照差异，不当权威冲突
        ident.detail["owner_crosscheck"] = "unconfirmed_field_differs"

    # E3：用 token 的实际账户集合**验证**主机名声明（成员证明，而不是采信配置）
    if cf_api_token:
        try:
            ids = _cf_account_ids(cf_api_token)
        except PermissionError:
            ident.status, ident.error_code = "auth_failed", Err.AUTH_FAILED
            return finalize(ident, cfg_account_claim=claim, cfg_owner_claim=owner)
        except Exception as e:
            # ★token 过期/无权限（HTTP 401/403）是"权限不足"，不能降级成 E2 弱证据继续计票
            http_code = str(getattr(e, "code", "") or "")
            if http_code in ("401", "403"):
                ident.status, ident.error_code = "auth_failed", Err.AUTH_FAILED
                ident.detail = {"where": "cf_api", "code": http_code}
                return finalize(ident, cfg_account_claim=claim, cfg_owner_claim=owner)
            ident.detail["cf_api_error"] = type(e).__name__
        else:
            if claim in ids:
                ident.account_id, ident.account_source = claim, E3
                ident.detail["member_of"] = len(ids)          # 只记数量，不记 id 列表
            else:
                ident.status, ident.error_code = ("config_mismatch",
                                                  Err.CONFIG_IDENTITY_MISMATCH)
                ident.detail["reason"] = "host 声明的账户不在该 token 可访问的账户集合内"
                return finalize(ident, cfg_account_claim=claim, cfg_owner_claim=owner)
    else:
        ident.account_id, ident.account_source = claim, E1   # 只有配置推导 → 弱
        ident.detail["upgrade_hint"] = "配置 CLOUDFLARE_API_TOKEN 可升到 E3"

    return finalize(ident, cfg_account_claim=claim, cfg_owner_claim=owner)

def capabilities_r2(client, bucket) -> dict:
    cap = {"provider": "cloudflare-r2"}
    # 条件写：R2 官方确认支持 → 实测确认（用临时 key）
    cap["conditional_write"] = _probe_conditional_write(client, bucket)
    # 这三个 R2 未实现 → 明确记 unsupported，不记 disabled
    for label, fn in (("versioning", "get_bucket_versioning"),
                      ("ownership_controls", "get_bucket_ownership_controls"),
                      ("object_lock", "get_object_lock_configuration")):
        try:
            getattr(client, fn)(Bucket=bucket)
            cap[label] = {"supported": True}
        except Exception as e:
            code = _code(e)
            cap[label] = ({"supported": False, "reason": "not_implemented"}
                          if code in ("NotImplemented", "501") else
                          {"unknown": code})
    return cap


def _probe_conditional_write(client, bucket) -> dict:
    import uuid
    key = f"_it/cap/{uuid.uuid4().hex}"
    try:
        client.put_object(Bucket=bucket, Key=key, Body=b"A", IfNoneMatch="*")
        try:
            client.put_object(Bucket=bucket, Key=key, Body=b"B", IfNoneMatch="*")
            return {"supported": False, "reason": "server_accepted_overwrite"}
        except Exception as e:
            ok = _code(e) in ("PreconditionFailed", "412")
            return {"supported": ok, "reason": _code(e) if not ok else "412"}
    except Exception as e:
        return {"supported": None, "reason": _code(e)}
