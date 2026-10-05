# body/witness_registry.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: external 不手填, 由身份探测 + 指纹判定; 指纹重复 → 拒绝计入 quorum;
#       凭据只从 env/文件读, 任何日志只输出指纹前 8 位, 绝不输出原文
from __future__ import annotations
import hashlib, hmac, json, os
from urllib.parse import urlparse

def fp(key: bytes, *parts: str) -> str:
    msg = json.dumps(parts, separators=(",", ":"), ensure_ascii=True).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()

def redact(s: str | None) -> str:
    return f"<{len(s)}B:{hashlib.sha256((s or '').encode()).hexdigest()[:8]}>" if s else "<unset>"

def witness_fingerprints(w: dict, fp_key: bytes) -> dict:
    """account_id / bucket_owner_id 必须由 provider API 探测，探测不到就拒绝。"""
    need = ("provider", "endpoint", "account_id", "bucket_owner_id",
            "access_key_id", "bucket")
    missing = [k for k in need if not w.get(k)]
    if missing:
        raise ValueError(f"identity_probe_incomplete:{missing}")
    if w["account_id"] != w["bucket_owner_id"]:
        raise ValueError("bucket_owner_account_mismatch")     # 桶不属于该账户
    host = (urlparse(w["endpoint"]).hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("invalid_endpoint")
    return {
        "account_domain":    fp(fp_key, "account",    w["provider"], w["account_id"]),
        "credential_domain": fp(fp_key, "credential", w["provider"], w["account_id"],
                                w["access_key_id"]),
        "config":            fp(fp_key, "config", w["provider"], host, w["account_id"],
                                w["access_key_id"], w["bucket_owner_id"], w["bucket"]),
    }

def reject_duplicate_domains(witnesses: list[dict], fp_key: bytes):
    """同账户/同凭据的两把凭据不可以算两个见证 —— 这是防自欺的核心一行。"""
    seen = {}
    for w in witnesses:
        f = witness_fingerprints(w, fp_key)
        w["_fp"] = f
        for kind in ("account_domain", "credential_domain"):
            if f[kind] in seen:
                raise ValueError(
                    f"same_credential_domain:{w['witness_id']}=={seen[f[kind]]} ({kind})")
            seen[f[kind]] = w["witness_id"]

def quorum(records, *, target, keyring, revoked_kids, live_from_seq, required=2):
    """按 witness_id 去重计票；同一 witness 的新旧 kid 最多一票；
       回填的历史记录不计实时见证（按 live_from_seq 卡）。

    ★ 同 seq 不同 head_hash **不得静默忽略** —— 那是唯一真正要命的信号，
      必须进 conflicts 并把状态置 critical（人工裁决），而不是当作"这票不算"。
    区分两类"不算票"：
      invalid    —— 签名验不过 / kid 已撤销：只丢这一票，不是篡改信号（不升 critical）
      conflicting —— 同 seq 哈希不符：真冲突，立刻 critical
    """
    valid, conflicts, invalid = set(), set(), set()
    seq_key = (target["root_id"], target["epoch"], target["seq"])
    for r in records:
        if (r["root_id"], r["epoch"], r["seq"]) != seq_key:
            continue                                    # 别的序号/纪元：不参与本次计票
        if r["seq"] < live_from_seq.get(r["witness_id"], 1):
            continue                                    # 事后回填 ≠ 当时见证
        if r["kid"] in revoked_kids or not keyring.verify(r["kid"], r):
            invalid.add(r["witness_id"]); continue      # 签名无效/密钥已撤销 → 丢票
        if r.get("head_hash") != target["head_hash"]:
            conflicts.add(r["witness_id"]); continue    # ★同 seq 哈希冲突 → critical
        valid.add(r["witness_id"])                      # 同 witness 多 kid 只加一次
    status = ("critical" if conflicts else
              "healthy" if len(valid) >= required else "degraded")
    return {"valid_witnesses": sorted(valid), "conflicting": sorted(conflicts),
            "invalid": sorted(invalid), "count": len(valid), "required": required,
            "status": status}
