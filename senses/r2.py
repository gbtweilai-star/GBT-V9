# senses/r2.py —— 统一 R2 层：真 boto3 客户端 · 离线模拟 · 出站 URL 校验
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 三种模式（自动选择）：
#   1. R2_SIM_DIR 设置        → 本地目录模拟 S3（离线全链路可跑，真做归档/回放/校验）
#   2. CLOUDFLARE_ACCOUNT_ID 设置 → 真 R2（S3 兼容），发请求前 host 校验（仅 https、拒绝环回/私有/保留）
#   3. 都没有                  → 抛 R2Unavailable（调用方降级，不许假装成功）
import hashlib
import os
import shutil
from pathlib import Path

from body.net_guard import assert_safe_outbound_url


class R2Unavailable(RuntimeError):
    """R2 未配置或不可用；调用方必须显式降级而不是静默继续。"""


def r2_endpoint() -> str:
    account = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if not account:
        raise R2Unavailable("CLOUDFLARE_ACCOUNT_ID is not set")
    endpoint = f"https://{account}.r2.cloudflarestorage.com"
    assert_safe_outbound_url(endpoint)          # 仅 https + 非环回/私有/保留
    return endpoint


class _SimObject:
    """模拟 head_object 的最小返回面。"""

    def __init__(self, size: int) -> None:
        self._size = size

    def __getitem__(self, key: str):
        if key == "ContentLength":
            return self._size
        raise KeyError(key)


class LocalSimClient:
    """本地目录模拟 S3（仅 upload_file / download_file / head_object / delete_object）。"""

    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        safe = Path(str(key).replace("\\", "/"))
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"unsafe object key: {key!r}")
        p = self.root / str(bucket) / safe
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def upload_file(self, filename, bucket, key, ExtraArgs=None):  # noqa: N803
        shutil.copyfile(filename, self._path(bucket, key))

    def download_file(self, bucket, key, filename):  # noqa: N803
        src = self._path(bucket, key)
        if not src.exists():
            raise FileNotFoundError(f"sim object missing: {bucket}/{key}")
        shutil.copyfile(src, filename)

    def head_object(self, Bucket=None, Key=None):  # noqa: N803
        p = self._path(Bucket or "", Key or "")
        if not p.exists():
            raise FileNotFoundError(f"sim object missing: {Bucket}/{Key}")
        return _SimObject(p.stat().st_size)

    def delete_object(self, Bucket=None, Key=None):  # noqa: N803
        self._path(Bucket or "", Key or "").unlink(missing_ok=True)


def r2_client():
    """返回 (client, mode)。mode ∈ {sim, r2}；未配置时抛 R2Unavailable。"""
    sim_dir = os.getenv("R2_SIM_DIR")
    if sim_dir:
        return LocalSimClient(sim_dir), "sim"
    if os.getenv("CLOUDFLARE_ACCOUNT_ID"):
        import boto3
        from botocore.config import Config

        client = boto3.client(
            "s3",
            endpoint_url=r2_endpoint(),
            region_name="auto",
            aws_access_key_id=os.getenv("CLOUDFLARE_R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("CLOUDFLARE_R2_SECRET_ACCESS_KEY", ""),
            config=Config(retries={"max_attempts": 5, "mode": "adaptive"},
                          max_pool_connections=16),
        )
        return client, "r2"
    raise R2Unavailable("R2 未配置：设 CLOUDFLARE_ACCOUNT_ID 或用 R2_SIM_DIR 跑离线模拟")


def r2_bucket() -> str:
    return os.getenv("R2_BUCKET_NAME", "tentacle-archive")


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


__all__ = ["R2Unavailable", "LocalSimClient", "r2_client", "r2_endpoint",
           "r2_bucket", "sha256_file"]
