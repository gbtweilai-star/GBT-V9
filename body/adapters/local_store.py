"""本地内容寻址工件存储。"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import tempfile
from pathlib import Path
from typing import AsyncIterator


class LocalArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    @staticmethod
    def _validate_ref(ref: str) -> None:
        if len(ref) != 64 or any(c not in "0123456789abcdef" for c in ref):
            raise ValueError("ref must be a lowercase SHA-256 hex digest")

    def _path(self, ref: str) -> Path:
        self._validate_ref(ref)
        return self.root / ref[:2] / ref[2:]

    def _put_sync(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".stage-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as f:                    # 写完 fsync 再原子改名
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_name)

    async def put_verified(self, ref: str, content: bytes) -> None:
        self._validate_ref(ref)
        if hashlib.sha256(content).hexdigest() != ref:
            raise ValueError("artifact_digest_mismatch")      # 内容寻址真校验
        await asyncio.to_thread(self._put_sync, self._path(ref), content)

    async def get_bytes(self, ref: str) -> bytes:
        return await asyncio.to_thread(self._path(ref).read_bytes)

    async def iter_bytes(self, ref: str, chunk_size: int = 262_144) -> AsyncIterator[bytes]:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        path = self._path(ref)
        f = await asyncio.to_thread(path.open, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(f.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

    async def delete_local_and_r2(self, ref: str) -> None:
        path = self._path(ref)

        def unlink() -> None:
            with contextlib.suppress(FileNotFoundError):
                path.unlink()

        await asyncio.to_thread(unlink)
