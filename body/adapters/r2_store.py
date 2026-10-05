"""本地 + S3/R2 工件存储。
铁律：删除顺序由调用方保证（先 claim 为 deleting 再调 delete）。
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, AsyncIterator

from .local_store import LocalArtifactStore


async def _call(method, *args, **kwargs):
    """兼容同步 boto3 与异步客户端。"""
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class R2ArtifactStore:
    def __init__(self, local: LocalArtifactStore, s3_client: Any, bucket: str) -> None:
        self.local, self.s3_client, self.bucket = local, s3_client, bucket

    async def put_verified(self, ref: str, content: bytes) -> None:
        await self.local.put_verified(ref, content)           # 先落本地（内容寻址仲裁）
        await _call(self.s3_client.put_object, Bucket=self.bucket, Key=ref, Body=content)

    async def get_bytes(self, ref: str) -> bytes:
        try:
            return await self.local.get_bytes(ref)
        except FileNotFoundError:
            response = await _call(self.s3_client.get_object, Bucket=self.bucket, Key=ref)
            body = response["Body"]
            try:
                return await _call(body.read)
            finally:
                close = getattr(body, "close", None)
                if close is not None:
                    await _call(close)

    async def iter_bytes(self, ref: str, chunk_size: int = 262_144) -> AsyncIterator[bytes]:
        async for chunk in self.local.iter_bytes(ref, chunk_size):
            yield chunk

    async def delete_local_and_r2(self, ref: str) -> None:
        await self.local.delete_local_and_r2(ref)
        await _call(self.s3_client.delete_object, Bucket=self.bucket, Key=ref)
