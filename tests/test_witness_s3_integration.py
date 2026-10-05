# tests/test_witness_s3_integration.py —— 真 S3 服务端集成测试（不可覆盖写 + 同账户拒绝）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 与 test_witness_integration.py 的分工：
#   那个文件连**云厂商**（B2/R2），需要主人自己的凭据，无凭据即 skip；
#   本文件连**一个真实的第三方 S3 服务端进程**（moto ThreadedMotoServer，真 HTTP）
#   —— 真协议、真网络、真条件写（If-None-Match → 412）、真 Owner 身份值。
# 断言的两件事与清单第 6 项一致：
#   ① 同 key 覆盖必须被拒，且原内容不变（不可覆盖写）
#   ② 同 provider 同 account 两把 key → same_credential_domain（不得算两个见证）
# 诚实标注：本地真服务端验证的是**客户端与协议语义**；云侧 Object Lock 的最终确认
#          仍需档案桶凭据（那条在 test_witness_integration.py 里，无凭据则 skip）。
# 凭据纪律：本文件不含任何凭据字面量 —— 测试用 key/secret 运行时随机生成，仅经环境变量传递。
import os
import secrets

import pytest

from body.identity_s3 import capabilities_s3, probe_s3
from body.witness_admin import probe_witness
from body.witness_registry import reject_duplicate_domains

moto_server = pytest.importorskip("moto.server", reason="未安装 moto，无法起真 S3 服务端")


@pytest.fixture(scope="module")
def s3_server():
    """真实 S3 服务端进程（随机端口，仅监听本机）。"""
    from moto.server import ThreadedMotoServer
    # 本地测试服务端凭据：运行时随机生成，绝不落盘、绝不复用（只经环境变量传递）
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "it-" + secrets.token_hex(4))
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", secrets.token_hex(16))
    srv = ThreadedMotoServer(port=0, verbose=False)
    srv.start()
    _host, port = srv.get_host_and_port()
    endpoint = f"http://127.0.0.1:{port}"           # moto 报 0.0.0.0，Windows 连不上
    yield endpoint
    srv.stop()


@pytest.fixture(scope="module")
def s3_client(s3_server):
    import boto3
    c = boto3.client("s3", endpoint_url=s3_server, region_name="us-east-1",
                     aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
                     aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"])
    c.create_bucket(Bucket="witness-bucket")
    return c


def _cfg(endpoint, bucket="witness-bucket", key="k-1"):
    """见证配置：凭据一律从环境变量取（本地测试端点的 secret 也是运行时生成的）。"""
    return {"witness_id": "local-s3", "kind": "s3", "provider": "s3",
            "endpoint": endpoint, "region": "us-east-1", "bucket": bucket,
            "access_key_id": key,
            "secret_access_key": os.environ["AWS_SECRET_ACCESS_KEY"],
            "kid": "local-s3/1", "immutable": False, "retain_days": 0,
            "cf_api_token": "", "min_evidence": "E2"}


# ═══ ① 不可覆盖写：真服务端必须拒绝同 key 覆盖，且原内容不变 ═══
def test_overwrite_is_refused_by_real_server(s3_server, s3_client):
    res = probe_witness(_cfg(s3_server), s3_client, dry_run=True)
    by_no = {s["step"]: s for s in res["steps"]}

    assert by_no[4]["ok"] is True, f"canary 条件写失败: {by_no[4]}"          # 写成功
    assert by_no[5]["ok"] is True, f"读回比对失败: {by_no[5]}"               # 读回一致
    assert by_no[6]["ok"] is True, f"覆盖未被拒 → 不可作不可变见证: {by_no[6]}"
    assert by_no[6]["code"] is None
    assert by_no[6]["detail"].get("original_unchanged") is True             # 原内容没被改
    assert by_no[7]["ok"] is True                                           # 清理完成


def test_probe_reports_immutability_honestly(s3_server, s3_client):
    """服务端没有 Object Lock：允许以条件写替代，但**必须显式说明**，不许含糊成"不可变"。"""
    res = probe_witness(_cfg(s3_server), s3_client, dry_run=True)
    s3step = {s["step"]: s for s in res["steps"]}[3]
    detail = s3step["detail"]
    assert detail.get("object_lock") is False
    assert "conditional_write" in detail
    assert "note" in detail and "条件写" in detail["note"]


# ═══ ② 身份来自真服务端 + 同账户两把 key 必须被拒 ═══
def test_identity_comes_from_real_server(s3_server, s3_client):
    ident = probe_s3(witness_id="local-s3", endpoint=s3_server,
                     bucket="witness-bucket", client=s3_client, provider="s3")
    assert ident.owner_id                                   # 真服务端给出的 Owner
    assert ident.account_id == ident.owner_id
    assert ident.status in ("attested", "verified")         # E2 → attested
    assert ident.evidence_level == "E2_resource_owner"


def test_same_account_two_keys_rejected(s3_server, s3_client):
    """同 provider 同 account 两把 key（不同桶）→ 必须报 same_credential_domain。"""
    import hashlib
    ident = probe_s3(witness_id="local-s3", endpoint=s3_server,
                     bucket="witness-bucket", client=s3_client, provider="s3")
    fp_key = hashlib.sha256(os.environ["AWS_SECRET_ACCESS_KEY"].encode()).digest()

    def _w(wid, key_id, bucket):
        return {"witness_id": wid, "provider": "s3", "endpoint": s3_server,
                "account_id": ident.account_id, "bucket_owner_id": ident.owner_id,
                "access_key_id": key_id, "bucket": bucket}

    # 同一账户、同一把 key → 拒绝
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains([_w("w1", "key-A", "b1"), _w("w2", "key-A", "b2")],
                                 fp_key)
    # 同一账户、两把不同 key → 账户域相同，同样拒绝（endpoint/桶不同不算独立）
    with pytest.raises(ValueError, match="same_credential_domain"):
        reject_duplicate_domains([_w("w1", "key-A", "b1"), _w("w2", "key-B", "b2")],
                                 fp_key)


def test_different_accounts_are_accepted(s3_server, s3_client):
    """不同账户 + 不同 provider 名 → 通过（真独立），这一条是正例对照。"""
    import hashlib
    fp_key = hashlib.sha256(b"fp").digest()

    def _w(wid, provider, account, key_id):
        return {"witness_id": wid, "provider": provider, "endpoint": s3_server,
                "account_id": account, "bucket_owner_id": account,
                "access_key_id": key_id, "bucket": "b"}

    reject_duplicate_domains([_w("a", "cloudflare-r2", "acct-1", "k1"),
                              _w("b", "backblaze-b2", "acct-2", "k2")], fp_key)


# ═══ 计数能力探测在真服务端上的读数 ═══
def test_capabilities_on_real_server(s3_server, s3_client):
    caps = capabilities_s3(s3_client, "witness-bucket")
    assert caps["conditional_write"].get("supported") is True      # 条件写被实测支持
    assert caps["object_lock"].get("supported") is False           # 未配置 → 如实报 False
