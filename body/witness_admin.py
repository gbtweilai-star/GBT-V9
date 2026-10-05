# body/witness_admin.py —— 见证注册 CLI：7 步 dry-run + 注册（独立账户第二见证）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 凭据只从环境变量读；日志一律 redact()（长度 + 8 位哈希），绝不回显原文
#   - 只在专用测试 bucket/prefix 操作；默认删 canary，**绝不删 bucket**
#     （删桶必须显式 --delete-disposable-bucket，且该桶必须是本次创建的空临时桶）
#   - 七步按序探测，报告**第一处失败**；任一步失败 → 不得注册
#   - 对外请求先过 net_guard（仅 https，拒绝环回/私网/保留地址）
#   - 对 R2：无 Object Lock → 明确 immutability_not_enabled（不得当不可变见证）
import argparse
import hashlib
import json
import os
import sys
import time
import uuid

from body import identity as ID
from body.identity_b2 import probe_b2
from body.identity_r2 import capabilities_r2, probe_r2
from body.identity_s3 import capabilities_s3, probe_s3
from body.net_guard import assert_safe_outbound_url
from body.witness_registry import redact

CANARY_PREFIX = os.environ.get("BODY_WITNESS_CANARY_PREFIX", "_canary")


class Step:
    def __init__(self, no, name):
        self.no, self.name = no, name
        self.ok = None
        self.code = None
        self.detail = {}

    def done(self, ok, code=None, **detail):
        self.ok, self.code, self.detail = bool(ok), code, detail
        return self

    def as_dict(self):
        return {"step": self.no, "name": self.name, "ok": self.ok,
                "code": self.code, "detail": self.detail}


# ═══════════ 配置：全部来自环境变量（代码里不出现任何凭据）═══════════
def load_witness_config(witness_id: str) -> dict:
    key = witness_id.upper().replace("-", "_")
    pre = "BODY_WITNESS_" + key + "_"
    cfg = {
        "witness_id": witness_id,
        "kind": os.environ.get(pre + "KIND", "s3"),
        "provider": os.environ.get(pre + "PROVIDER", ""),
        "endpoint": os.environ.get(pre + "ENDPOINT", ""),
        "region": os.environ.get(pre + "REGION", "auto"),
        "bucket": os.environ.get(pre + "BUCKET", ""),
        "access_key_id": os.environ.get(pre + "ACCESS_KEY_ID", ""),
        "secret_access_key": os.environ.get(pre + "SECRET_ACCESS_KEY", ""),
        "kid": os.environ.get(pre + "KID", ""),
        "immutable": os.environ.get(pre + "IMMUTABLE", "0") == "1",
        "retain_days": int(os.environ.get(pre + "RETAIN_DAYS", "0") or 0),
        "cf_api_token": os.environ.get("CLOUDFLARE_API_TOKEN", ""),
        "min_evidence": os.environ.get("BODY_WITNESS_MIN_EVIDENCE", "E3"),
    }
    missing = [k for k in ("provider", "endpoint", "bucket", "access_key_id",
                           "secret_access_key") if not cfg[k]]
    if missing:
        raise ValueError(f"见证 {witness_id} 配置不全（缺 {missing}）；"
                         f"凭据只从环境变量读，不要写进代码")
    assert_safe_outbound_url(cfg["endpoint"])          # 仅 https + 拒绝环回/私网
    return cfg


def make_client(cfg):
    """S3 兼容客户端。R2 与 B2 用同一套 boto3 入口，凭据只从 cfg（=env）来。"""
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3", endpoint_url=cfg["endpoint"], region_name=cfg["region"],
        aws_access_key_id=cfg["access_key_id"],
        aws_secret_access_key=cfg["secret_access_key"],
        config=Config(retries={"max_attempts": 3, "mode": "standard"},
                      connect_timeout=10, read_timeout=30))


def _code(exc) -> str:
    resp = getattr(exc, "response", None) or {}
    return str((resp.get("Error") or {}).get("Code") or type(exc).__name__)


# ═══════════ 七步 dry-run ═══════════
def probe_witness(cfg: dict, client, *, dry_run=True) -> dict:
    """按序探测并报告第一处失败。dry_run 下不写任何持久对象（除 canary 试探）。"""
    steps: list[Step] = []
    ident = None
    caps = {}

    # ① 身份探测 + 列桶
    s1 = Step(1, "identity_and_list")
    try:
        if cfg["provider"] == "cloudflare-r2":
            ident = probe_r2(witness_id=cfg["witness_id"], endpoint=cfg["endpoint"],
                             bucket=cfg["bucket"], client=client,
                             cf_api_token=cfg["cf_api_token"] or None)
        elif cfg["provider"] == "backblaze-b2":
            ident = probe_b2(witness_id=cfg["witness_id"], endpoint=cfg["endpoint"],
                             bucket=cfg["bucket"], key_id=cfg["access_key_id"],
                             app_key=cfg["secret_access_key"], client=client)
        elif cfg["provider"] in ("s3", "s3-compatible", "minio", "generic-s3"):
            ident = probe_s3(witness_id=cfg["witness_id"], endpoint=cfg["endpoint"],
                             bucket=cfg["bucket"], client=client,
                             provider=cfg["provider"])
        else:
            s1.done(False, "provider_unimplemented", provider=cfg["provider"])
            steps.append(s1)
            return _finish(cfg, steps, None, caps)
        client.list_objects_v2(Bucket=cfg["bucket"], MaxKeys=1)
        s1.done(True, None, identity_status=ident.status,
                account=redact(ident.account_id), source=ident.account_source)
    except Exception as e:
        s1.done(False, "identity_or_list_permission_failed", error=_code(e))
        steps.append(s1)
        return _finish(cfg, steps, None, caps)
    steps.append(s1)

    # ② 所有权：身份账户 与 桶 owner 必须一致（不一致/未知 → 不计票）
    s2 = Step(2, "ownership")
    if not ident.account_id:
        s2.done(False, "ownership_unverified", identity_status=ident.status)
    elif ident.owner_id and ident.owner_id != ident.account_id:
        s2.done(False, "ownership_unverified",
                account=redact(ident.account_id), owner=redact(ident.owner_id))
    else:
        s2.done(True, None, account=redact(ident.account_id),
                owner=redact(ident.owner_id), owner_source=ident.owner_source)
    steps.append(s2)

    # ③ 不可变性：Object Lock / 保留策略 / 版本控制
    s3 = Step(3, "immutability")
    try:
        if cfg["provider"] == "cloudflare-r2":
            caps = capabilities_r2(client, cfg["bucket"])
            lock = caps.get("object_lock", {})
            if lock.get("supported"):
                s3.done(True, None, **{"object_lock": "supported"})
            else:
                s3.done(False, "immutability_not_enabled",
                        reason=lock.get("reason") or lock.get("unknown") or "unsupported",
                        note="R2 无 Object Lock：不可作不可变见证（可作普通见证）")
        elif cfg["provider"] in ("s3", "s3-compatible", "minio", "generic-s3"):
            caps = capabilities_s3(client, cfg["bucket"])
            lock = caps.get("object_lock", {})
            cw = caps.get("conditional_write", {})
            if lock.get("supported"):
                s3.done(True, None, object_lock=True)
            else:
                # 没有 Object Lock 时，条件写（If-None-Match）是唯一的不可覆盖保证
                ok = bool(cw.get("supported"))
                s3.done(ok, None if ok else "immutability_not_enabled",
                        object_lock=False,
                        conditional_write=cw.get("reason"),
                        note="以条件写替代 Object Lock；若连条件写都不支持 → 不可作不可变见证")
        else:
            try:
                ol = client.get_object_lock_configuration(Bucket=cfg["bucket"])
                enabled = (ol.get("ObjectLockConfiguration", {})
                             .get("ObjectLockEnabled") == "Enabled")
                s3.done(enabled, None if enabled else "immutability_not_enabled",
                        object_lock=enabled)
            except Exception as e:
                code = _code(e)
                if cfg["immutable"]:
                    s3.done(False, "immutability_not_enabled", error=code)
                else:
                    s3.done(True, None, note=f"未声明不可变，Object Lock 探测={code}")
    except Exception as e:
        s3.done(False, "immutability_not_enabled", error=_code(e))
    steps.append(s3)

    # ④ 写 canary（条件创建：对象已存在 → 拒绝）
    canary_key = f"{CANARY_PREFIX}/{uuid.uuid4().hex}.bin"
    payload = b"canary-" + uuid.uuid4().hex.encode()
    s4 = Step(4, "canary_write")
    try:
        client.put_object(Bucket=cfg["bucket"], Key=canary_key, Body=payload,
                          IfNoneMatch="*")
        s4.done(True, None, key=canary_key, bytes=len(payload))
    except Exception as e:
        s4.done(False, "canary_write_failed", error=_code(e))
        steps.append(s4)
        return _finish(cfg, steps, ident, caps)
    steps.append(s4)

    # ⑤ 读回 + 字节数/SHA-256 比对
    s5 = Step(5, "canary_readback")
    try:
        body = client.get_object(Bucket=cfg["bucket"], Key=canary_key)["Body"].read()
        ok = (len(body) == len(payload)
              and hashlib.sha256(body).hexdigest() == hashlib.sha256(payload).hexdigest())
        s5.done(ok, None if ok else "canary_readback_mismatch",
                read_bytes=len(body), sha8=hashlib.sha256(body).hexdigest()[:8])
    except Exception as e:
        s5.done(False, "canary_readback_mismatch", error=_code(e))
    steps.append(s5)

    # ⑥ 同 key 不同内容必须被拒（不可覆盖性的实测）
    s6 = Step(6, "overwrite_refused")
    try:
        client.put_object(Bucket=cfg["bucket"], Key=canary_key,
                          Body=b"different-content", IfNoneMatch="*")
        s6.done(False, "overwrite_allowed", key=canary_key,
                note="同 key 覆盖被接受 → 不得作为不可变见证")
    except Exception as e:
        code = _code(e)
        if code in ("PreconditionFailed", "412"):
            try:
                body = client.get_object(Bucket=cfg["bucket"], Key=canary_key)["Body"].read()
                unchanged = body == payload
                s6.done(unchanged, None if unchanged else "overwrite_allowed",
                        http_code=code, original_unchanged=unchanged)
            except Exception as e2:
                s6.done(False, "canary_readback_mismatch", error=_code(e2))
        else:
            s6.done(None, "overwrite_probe_unknown", error=code,
                    note="无法判定覆盖语义 → 不可作为不可变见证（可作普通见证）")
    steps.append(s6)

    # ⑦ 清理 canary（被保留策略拒绝 → 记 cleanup_retained_by_lock，不关锁不删桶）
    s7 = Step(7, "cleanup")
    try:
        client.delete_object(Bucket=cfg["bucket"], Key=canary_key)
        s7.done(True, None, key=canary_key)
    except Exception as e:
        code = _code(e)
        if "retention" in code.lower() or "lock" in code.lower():
            s7.done(True, "cleanup_retained_by_lock", error=code,
                    note="由保留策略到期清理；不动锁、不删桶")
        else:
            s7.done(False, "cleanup_failed", error=code)
    steps.append(s7)
    return _finish(cfg, steps, ident, caps)


def _finish(cfg, steps, ident, caps) -> dict:
    failed = [s for s in steps if s.ok is False]
    hard = [s for s in failed if s.code not in ("overwrite_probe_unknown",)]
    return {
        "witness_id": cfg["witness_id"], "provider": cfg["provider"],
        "ok": not hard, "steps": [s.as_dict() for s in steps],
        "first_failure": (hard[0].as_dict() if hard else None),
        "identity": ({"status": ident.status, "evidence": ident.evidence_level,
                      "error_code": ident.error_code,
                      "account": redact(ident.account_id),
                      "owner": redact(ident.owner_id)} if ident else None),
        "capabilities": caps,
        "at": time.time(),
    }


# ═══════════ 注册：七步全绿后才落库（live_from_seq 口径）═══════════
async def register_witness(db, cfg: dict, probe: dict, *, live_from_seq="auto",
                           approve_by="", backfilled_through_seq=None) -> dict:
    """把见证登记进 body_witness_status。

    live_from_seq 口径：**auto = 当前链头 + 1** —— 只有它加入之后的锚才算它独立见证；
    更早的历史只能 backfill（填 backfilled_through_seq），回填不追溯增加历史 quorum。
    """
    if not probe.get("ok"):
        raise ValueError("七步 dry-run 未全绿，拒绝注册；先修复 first_failure")
    head = (await db.fetch_all(
        "SELECT seq FROM registration ORDER BY seq DESC LIMIT 1") or [{"seq": 0}])[0]
    head_seq = int(head["seq"] or 0)
    live = head_seq + 1 if live_from_seq == "auto" else int(live_from_seq)
    ident = probe.get("identity") or {}
    now = time.time()
    from datetime import datetime, timezone
    at = datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds")
    await db.execute(
        "INSERT INTO body_witness_status "
        "(witness_id, provider, kid, persisted_status, live_status, "
        " last_verified_at, last_ok_at, live_from_seq, backfilled_through_seq, "
        " consecutive_fail, consecutive_ok, last_error) "
        "VALUES (?,?,?,?,?,?,?,?,?,0,0,?) "
        "ON CONFLICT (witness_id) DO UPDATE SET "
        " provider=EXCLUDED.provider, kid=EXCLUDED.kid, "
        " persisted_status=EXCLUDED.persisted_status, "
        " live_status=EXCLUDED.live_status, last_verified_at=EXCLUDED.last_verified_at, "
        " last_ok_at=EXCLUDED.last_ok_at, live_from_seq=EXCLUDED.live_from_seq, "
        " backfilled_through_seq=EXCLUDED.backfilled_through_seq, last_error=NULL",
        (cfg["witness_id"], cfg["provider"], cfg["kid"],
         ident.get("status") or "unknown", "valid", at, at, live,
         backfilled_through_seq, None))
    return {"witness_id": cfg["witness_id"], "live_from_seq": live,
            "backfilled_through_seq": backfilled_through_seq,
            "identity_status": ident.get("status"), "approve_by": approve_by,
            "at": at, "head_seq": head_seq}


# ═══════════ CLI ═══════════
def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="witness_admin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("probe-witness", help="七步 dry-run 探测")
    p1.add_argument("--witness-id", required=True)
    p1.add_argument("--dry-run", action="store_true", default=True)
    p2 = sub.add_parser("register", help="注册见证（需七步全绿）")
    p2.add_argument("--witness-id", required=True)
    p2.add_argument("--live-from-seq", default="auto")
    p2.add_argument("--backfilled-through-seq", type=int, default=None)
    p2.add_argument("--approve-by", required=True)
    p2.add_argument("--delete-disposable-bucket", action="store_true",
                    help="仅允许删除本次创建的空临时桶（强烈不建议）")
    a = ap.parse_args(argv)

    cfg = load_witness_config(a.witness_id)
    if cfg["bucket"] == os.environ.get("R2_BUCKET", ""):
        print("拒绝：目标桶与生产 R2_BUCKET 同名", file=sys.stderr)
        return 2
    client = make_client(cfg)

    if a.cmd == "probe-witness":
        res = probe_witness(cfg, client, dry_run=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        if not res["ok"]:
            print(f"✗ 第一处失败：{res['first_failure']}", file=sys.stderr)
            return 1
        print("✓ 七步全绿，可执行 register")
        return 0

    # register：先跑七步，全绿才落库
    res = probe_witness(cfg, client, dry_run=True)
    if not res["ok"]:
        print(f"✗ 七步未全绿，拒绝注册：{res['first_failure']}", file=sys.stderr)
        return 1
    if a.delete_disposable_bucket:
        print("注意：--delete-disposable-bucket 只允许删本次创建的空临时桶；"
              "本实现不提供自动删桶，请人工确认后再操作", file=sys.stderr)
        return 2
    import asyncio
    from body.adapters.sqlite_db import SqliteDb
    dsn = os.environ.get("BODY_DB", "sqlite:///var/body.db")

    async def _run():
        db = await SqliteDb.open(dsn)
        return await register_witness(db, cfg, res,
                                      live_from_seq=a.live_from_seq,
                                      approve_by=a.approve_by,
                                      backfilled_through_seq=a.backfilled_through_seq)
    out = asyncio.run(_run())
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
