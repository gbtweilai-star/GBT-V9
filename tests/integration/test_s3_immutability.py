# tests/integration/test_s3_immutability.py
import hashlib, json, os, uuid
import pytest
from tests.integration.conftest import TEST_PREFIX, guard_key, need, safe_error

pytestmark = pytest.mark.integration


class PreconditionFailed(Exception):
    pass

class OverwriteAllowed(Exception):
    pass


class S3WitnessProvider:
    """真实 provider 适配层（R2 / B2 共用；← 对齐点：换成你项目里现成的 R2 客户端包装）"""
    def __init__(self, name, endpoint, region, bucket, key_id, secret, provider):
        import boto3
        from botocore.config import Config
        self.name, self.bucket, self.provider = name, bucket, provider
        self.endpoint = endpoint
        self.key_id = key_id
        self.s3 = boto3.client(
            "s3", endpoint_url=endpoint, region_name=region,
            aws_access_key_id=key_id, aws_secret_access_key=secret,
            config=Config(retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=30,
                          signature_version="s3v4"))

    # ── 客户端同步实现（boto3 是同步的） ──
    def put_create_only_sync(self, key, payload: bytes) -> dict:
        guard_key(key)
        kw = {"Bucket": self.bucket, "Key": key, "Body": payload, "IfNoneMatch": "*"}
        try:
            return {"ok": True, "detail": self.s3.put_object(**kw)}
        except Exception as e:
            info = safe_error(e)
            if info["code"] in ("PreconditionFailed", "412", "ConditionalRequestConflict"):
                raise PreconditionFailed(info) from None
            raise

    def put_object_lock_sync(self, key, payload: bytes, retain_until) -> dict:
        guard_key(key)
        try:
            return {"ok": True, "detail": self.s3.put_object(
                Bucket=self.bucket, Key=key, Body=payload,
                ObjectLockMode="COMPLIANCE", ObjectLockRetainUntilDate=retain_until)}
        except Exception as e:
            return {"ok": False, "detail": safe_error(e)}

    def get_sync(self, key) -> bytes:
        r = self.s3.get_object(Bucket=self.bucket, Key=key)
        return r["Body"].read()

    def head_sync(self, key) -> dict:
        return self.s3.head_object(Bucket=self.bucket, Key=key)

    def delete_sync(self, key) -> dict:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "detail": safe_error(e)}

    # ── 能力探测：失败一律记 unknown，绝不记成 disabled ──
    def capabilities_sync(self) -> dict:
        cap = {}
        for label, fn in (("versioning", lambda: self.s3.get_bucket_versioning(Bucket=self.bucket)),
                          ("object_lock", lambda: self.s3.get_object_lock_configuration(Bucket=self.bucket)),
                          ("ownership", lambda: self.s3.get_bucket_ownership_controls(Bucket=self.bucket))):
            try:
                r = fn(); r.pop("ResponseMetadata", None); cap[label] = r
            except Exception as e:
                cap[label] = {"unknown": safe_error(e)}
        try:
            r = self.s3.list_buckets()
            cap["account_id"] = (r.get("Owner") or {}).get("ID")
            cap["account_id_raw_len"] = len(cap["account_id"] or "")
        except Exception as e:
            cap["account_id"] = None
            cap["account_error"] = safe_error(e)
        return cap

    def bucket_owner_sync(self) -> str | None:
        try:
            return (self.s3.list_buckets().get("Owner") or {}).get("ID")
        except Exception:
            return None


@pytest.fixture(scope="module")
def providers():
    import asyncio
    a = S3WitnessProvider(
        "r2-witness", need("WITNESS_IT_R2_ENDPOINT", "r2-witness"),
        os.getenv("WITNESS_IT_R2_REGION", "auto"), need("WITNESS_IT_R2_BUCKET", "r2-witness"),
        need("WITNESS_IT_R2_ACCESS_KEY_ID", "r2-witness"),
        need("WITNESS_IT_R2_SECRET_ACCESS_KEY", "r2-witness"), "cloudflare-r2")
    b = S3WitnessProvider(
        "b2-witness", need("WITNESS_IT_B2_ENDPOINT", "b2-witness"),
        need("WITNESS_IT_B2_REGION", "b2-witness"), need("WITNESS_IT_B2_BUCKET", "b2-witness"),
        need("WITNESS_IT_B2_ACCESS_KEY_ID", "b2-witness"),
        need("WITNESS_IT_B2_SECRET_ACCESS_KEY", "b2-witness"), "backblaze-b2")
    return {"r2-witness": a, "b2-witness": b}


@pytest.fixture(params=["r2-witness", "b2-witness"])
def provider(request, providers):
    return providers[request.param]


W = lambda p: __import__("asyncio").to_thread     # 同步调用包进线程避免阻塞


# ═══ 1. 不变式：同 key 写不同内容必须不可能 ═══
def test_same_key_different_content_is_impossible(provider, capsys):
    key = f"{TEST_PREFIX}/{uuid.uuid4().hex}"
    A, B = b'{"anchor":"A"}', b'{"anchor":"B"}'
    provider.put_create_only_sync(key, A)                 # 首次创建
    mechanism = None

    try:
        provider.put_create_only_sync(key, B)
    except PreconditionFailed as e:
        mechanism = "conditional_write"
    else:
        # 服务端忽略了 If-None-Match → 检查是否被别的东西挡住
        got = provider.get_sync(key)
        if got == B:
            raise AssertionError(
                f"{provider.name}: 同 key 不同内容被接受，且当前值已变 → overwrite_allowed")
        mechanism = "server_no_op"                        # 写了但没变，仍需记录

    assert provider.get_sync(key) == A, f"{provider.name}: 原内容已被改写"
    with capsys.disabled():
        print(f"[mechanism] {provider.name} key={safe_fp(key)} -> {mechanism}")


# ═══ 2. 幂等：同 key 同内容允许成功或 412，但不得损坏 ═══
def test_same_key_same_content_never_corrupts(provider):
    key = f"{TEST_PREFIX}/{uuid.uuid4().hex}"
    A = b'{"anchor":"same"}'
    provider.put_create_only_sync(key, A)
    try:
        provider.put_create_only_sync(key, A)
    except PreconditionFailed:
        pass
    assert provider.get_sync(key) == A
    assert hashlib.sha256(provider.get_sync(key)).hexdigest() == hashlib.sha256(A).hexdigest()


# ═══ 3. 读回一致（证据可复核） ═══
def test_readback_matches_bytes_and_digest(provider):
    key = f"{TEST_PREFIX}/{uuid.uuid4().hex}"
    payload = json.dumps({"seq": 42, "head_hash": "a" * 64},
                         sort_keys=True, separators=(",", ":")).encode()
    provider.put_create_only_sync(key, payload)
    got = provider.get_sync(key)
    h = provider.head_sync(key)
    assert got == payload
    assert hashlib.sha256(got).hexdigest() == hashlib.sha256(payload).hexdigest()
    # 版本 ID 只是旁证，不作为不可变保证（见坑 ③）
    assert "ETag" in h


# ═══ 4. 记录事实：本环境实际生效的机制（断言在下面一条） ═══
def test_record_capabilities(provider, capsys, tmp_path):
    cap = provider.capabilities_sync()
    cap["name"] = provider.name
    cap["endpoint_host_len"] = len((provider.endpoint or "").split("//")[-1].split("/")[0])
    cap["bucket_len"] = len(provider.bucket or "")
    (tmp_path / f"caps_{provider.name}.json").write_text(
        json.dumps(cap, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    with capsys.disabled():
        print(f"[capabilities] {provider.name}: {json.dumps(cap, ensure_ascii=False)}")


# ═══ 5. 锁环境下的删除：允许失败，但必须记录为 cleanup_retained ═══
def test_delete_records_locked_or_removed(provider, capsys):
    key = f"{TEST_PREFIX}/{uuid.uuid4().hex}"
    provider.put_create_only_sync(key, b'{"x":1}')
    r = provider.delete_sync(key)
    if r["ok"]:
        # 删成功：后续清理依赖生命周期策略；这里必须验证确实没了
        try:
            provider.get_sync(key)
            raise AssertionError("delete 报告成功但对象仍在")
        except Exception as e:
            assert safe_error(e)["code"] in ("NoSuchKey", "404"), safe_error(e)
    else:
        with capsys.disabled():
            print(f"[cleanup_retained] {provider.name}: {r['detail']}")   # 不关锁、不绕锁


# ═══ 6. 隔离守卫：测试永远碰不到生产前缀 ═══
def test_prefix_guard_blocks_production(provider):
    for bad in ("anchors/main/e1/000000000001-aa.json", "_it", f"{TEST_PREFIX}x/1"):
        with pytest.raises(AssertionError):
            provider.put_create_only_sync(bad, b"nope")


# ═══ 7. 要求不可变时的硬判定 ═══
@pytest.mark.skipif(os.getenv("WITNESS_IT_REQUIRE_IMMUTABLE", "0") != "1",
                    reason="默认只记录机制；置 WITNESS_IT_REQUIRE_IMMUTABLE=1 才硬判定")
def test_immutability_mechanism_present_when_required(provider):
    cap = provider.capabilities_sync()
    cond = _conditional_write_supported(provider)
    lock = bool((cap.get("object_lock") or {}).get("ObjectLockConfiguration"))
    assert cond or lock, (f"{provider.name}: 无条件写也无 Object Lock → "
                          f"不符合不可变见证要求 (capabilities={safe_fp(str(cap))})")


def _conditional_write_supported(provider) -> bool:
    key = f"{TEST_PREFIX}/{uuid.uuid4().hex}"
    provider.put_create_only_sync(key, b"A")
    try:
        provider.put_create_only_sync(key, b"B")
        return False
    except PreconditionFailed:
        return True
