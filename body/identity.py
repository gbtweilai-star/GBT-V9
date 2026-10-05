# body/identity.py —— witness 身份探测的通用层
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 配置里读出来的身份是"声明", 不是证据; 冲突时【不覆盖】, 报新错误码;
#       证据不足 = 少一票, 绝不拿配置补证
from __future__ import annotations
import hashlib, hmac, json, os, time
from dataclasses import dataclass, asdict, field

class Err:
    UNREACHABLE               = "UNREACHABLE"
    AUTH_FAILED               = "AUTH_FAILED"
    IDENTITY_FIELD_FORBIDDEN  = "IDENTITY_FIELD_FORBIDDEN"
    IDENTITY_FIELD_UNSUPPORTED= "IDENTITY_FIELD_UNSUPPORTED"
    IDENTITY_UNVERIFIED       = "IDENTITY_UNVERIFIED"
    CONFIG_IDENTITY_MISMATCH  = "CONFIG_IDENTITY_MISMATCH"
    CONFIG_OWNER_MISMATCH     = "CONFIG_OWNER_MISMATCH"
    AUTHORITY_IDENTITY_CONFLICT = "AUTHORITY_IDENTITY_CONFLICT"
    # 身份本身没问题、但"这一票没算上"的原因（掉票必须说得出理由）
    EVIDENCE_BELOW_REQUIRED     = "EVIDENCE_BELOW_REQUIRED"
    CONTENT_NOT_VERIFIED        = "CONTENT_NOT_VERIFIED"
    CREDENTIAL_DOMAIN_DUPLICATE = "CREDENTIAL_DOMAIN_DUPLICATE"
    NOT_LIVE_YET                = "NOT_LIVE_YET"

E1, E2, E3 = "E1_config", "E2_resource_owner", "E3_authoritative_account"

STATUS_BY_ERR = {
    Err.UNREACHABLE:                "unreachable",
    Err.AUTH_FAILED:                "auth_failed",
    Err.IDENTITY_FIELD_FORBIDDEN:   "identity_unverified",
    Err.IDENTITY_FIELD_UNSUPPORTED: "unsupported",
    Err.IDENTITY_UNVERIFIED:        "identity_unverified",
    Err.CONFIG_IDENTITY_MISMATCH:   "config_mismatch",
    Err.CONFIG_OWNER_MISMATCH:      "config_mismatch",
    Err.AUTHORITY_IDENTITY_CONFLICT: "critical_conflict",
}
# 哪些状态可以计票
COUNTABLE = {"verified", "attested"}          # 见第三节分级策略


@dataclass
class Identity:
    witness_id: str
    provider: str                     # cloudflare-r2 | backblaze-b2
    endpoint_host: str
    bucket: str
    # 证据
    account_id: str | None = None
    account_source: str | None = None          # E3 来源
    owner_id: str | None = None
    owner_source: str | None = None            # E2 来源
    credential_id: str | None = None           # 只做辅助识别
    claims: dict = field(default_factory=dict) # E1 配置声明（仅对照）
    evidence_level: str = E1
    status: str = "identity_unverified"
    error_code: str | None = None
    detail: dict = field(default_factory=dict)
    observed_at: str | None = None
    expires_at: str | None = None
    acknowledged_at: str | None = None      # 人工 ack 时间（由 resolve 从状态行读回）

    def as_dict(self) -> dict:
        d = asdict(self)
        d.pop("claims", None)                       # 原始配置不入库/不打日志
        return d


def fp_key() -> bytes:
    k = os.environ.get("BODY_WITNESS_FP_KEY")
    if not k:
        raise RuntimeError("BODY_WITNESS_FP_KEY 未设置：身份指纹必须用独立密钥")
    return k.encode()


def ident_fp(*parts) -> str:
    msg = json.dumps([p or "" for p in parts], separators=(",", ":")).encode()
    return hmac.new(fp_key(), msg, hashlib.sha256).hexdigest()[:16]

def finalize(ident: Identity, *, cfg_account_claim: str | None,
             cfg_owner_claim: str | None) -> Identity:
    """把探测结果收敛成状态。冲突不覆盖，逐级给专门错误码。"""
    ident.claims = {"account": cfg_account_claim, "owner": cfg_owner_claim}

    # ⓪ 探测层已判死的硬状态（权限不足/不支持/探不到/配置相反/权威冲突）不再重算 ——
    #    否则 auth_failed / unsupported 会被下面的分级分支改写成 identity_unverified，
    #    把"权限不足"和"网络不通"抹成同一个词，排障方向就错了。
    if ident.error_code and ident.status in ("unreachable", "auth_failed",
                                             "unsupported", "config_mismatch",
                                             "critical_conflict",
                                             "identity_unverified"):
        ident.detail = dict(ident.detail or {})
        return ident

    # ① 服务端内部矛盾（两个权威源打架）→ critical，不是重试能解决的
    if ident.account_id and ident.owner_id and ident.account_id != ident.owner_id \
            and ident.owner_source == E2 and ident.account_source == E3:
        ident.status, ident.error_code = "critical_conflict", Err.AUTHORITY_IDENTITY_CONFLICT
        return ident

    # ② 配置声明 vs 服务端权威 → 不一致就报，绝不选一个信
    if cfg_account_claim and ident.account_id and cfg_account_claim != ident.account_id:
        ident.status, ident.error_code = ("config_mismatch", Err.CONFIG_IDENTITY_MISMATCH)
        return ident
    if cfg_owner_claim and ident.owner_id and cfg_owner_claim != ident.owner_id:
        ident.status, ident.error_code = ("config_mismatch", Err.CONFIG_OWNER_MISMATCH)
        return ident

    # ③ 分级
    if ident.account_id and ident.account_source == E3:
        ident.evidence_level, ident.status = E3, "verified"
    elif (ident.account_id and ident.account_source == E2
          and ident.owner_id and ident.owner_source == E2
          and ident.account_id == ident.owner_id):
        # 通用 S3（MinIO/moto/自建网关）没有更上一级的权威账户证明：
        # 服务端自身给出的所有者声明**内部自洽** → attested（E2），显式标注非 E3 权威。
        ident.evidence_level, ident.status = E2, "attested"
        ident.error_code = None
        ident.detail = dict(ident.detail or {})
        ident.detail["note"] = "E2 自洽（服务端所有者声明）→ attested，非 E3 权威"
        return ident
    elif cfg_account_claim and ident.account_id and cfg_account_claim == ident.account_id \
            and ident.account_source in (E1, E2):
        ident.evidence_level, ident.status = E2, "attested"     # 弱证据，显式标注
    else:
        ident.evidence_level, ident.status = E1, "identity_unverified"
        ident.error_code = ident.error_code or Err.IDENTITY_UNVERIFIED
    return ident


def countable(ident: Identity, *, min_evidence: str, fresh: bool) -> tuple[bool, str]:
    """计票闸门。默认 min_evidence=E3 → R2 没配 CLOUDFLARE_API_TOKEN 就不计票。"""
    if not fresh:
        return False, "IDENTITY_STALE"                  # 缓存过期不得计票
    if ident.status in ("verified", "attested"):
        # 身份本身是有效的，只是证据等级不够 → 给出**等级**原因（用规范错误码，
        # 别和告警表的键拼写不一致 —— 那会让"掉票原因"查不到对应的开单规则）
        if min_evidence == "E3" and ident.evidence_level != E3:
            return False, Err.EVIDENCE_BELOW_REQUIRED
        return True, "ok"
    return False, ident.error_code or ident.status
