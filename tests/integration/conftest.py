# tests/integration/conftest.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import hashlib, hmac, json, logging, os, uuid
import pytest

logging.getLogger("boto3").setLevel(logging.CRITICAL)          # ★关掉 wire/debug 日志
logging.getLogger("botocore").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)

REQUIRE_IT     = os.getenv("WITNESS_IT_REQUIRED", "0") == "1"   # 发布 gate 置 1
RUN_ID         = os.getenv("WITNESS_IT_RUN_ID") or uuid.uuid4().hex[:12]
TEST_PREFIX    = os.getenv("WITNESS_IT_PREFIX", f"_it/{RUN_ID}")
_STATUS: dict[str, dict] = {}                                   # 会话级 verified / not_verified


def need(env, label):
    """缺凭据 → skip 并登记 not_verified；REQUIRE_IT=1 时直接 fail。"""
    v = os.getenv(env)
    if v:
        _STATUS.setdefault(label, {"required": [], "missing": []})["required"].append(env)
        return v
    if REQUIRE_IT:
        pytest.fail(f"WITNESS_IT_REQUIRED=1 但缺少 {env}：integration gate 不允许跳过")
    _STATUS.setdefault(label, {"required": [], "missing": []})["missing"].append(env)
    pytest.skip(f"{env} 未设置 → not_verified（这不是通过）")


def safe_error(exc) -> dict:
    """白名单字段，绝不 str(exc)：boto3 异常常带 endpoint / 请求信息 / headers。"""
    resp = getattr(exc, "response", {}) or {}
    return {"type": type(exc).__name__,
            "code": (resp.get("Error") or {}).get("Code", "unknown"),
            "status": (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")}


def safe_fp(*parts) -> str:
    key = os.getenv("BODY_WITNESS_FP_KEY", "it-fp-key").encode()
    return hmac.new(key, json.dumps(parts, separators=(",", ":")).encode(),
                    hashlib.sha256).hexdigest()[:10]


def guard_key(key: str):
    """测试只允许在本次 run 的专用前缀下写；碰生产前缀直接炸。"""
    assert key.startswith(TEST_PREFIX + "/"), f"key outside test prefix: {safe_fp(key)}"
    assert not key.startswith("anchors/"), "拒绝触碰生产锚点前缀"


def pytest_sessionfinish(session, exitstatus):
    """把 verified / not_verified 写成产物 —— skip 不许消失在绿色里。"""
    out = os.getenv("WITNESS_IT_REPORT", "witness_integration_status.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"run_id": RUN_ID, "test_prefix": TEST_PREFIX,
                   "require_it": REQUIRE_IT, "providers": _STATUS}, f,
                  ensure_ascii=False, indent=2, sort_keys=True)
