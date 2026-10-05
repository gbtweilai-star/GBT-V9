# parity/isolation.py —— 隔离守卫：离线验收绝不允许碰生产
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律（任一不满足 → 直接拒绝运行，绝不静默降级）:
#   - 子进程环境里不得出现 DATABASE_URL（生产账本入口）
#   - offline 只允许 sqlite 且文件必须在 PARITY_TEMP_ROOT 内
#   - full 只允许 allowlist 内的 PG host（且库名以 parity_ 开头，bootstrap 已查）
#   - R2 bucket 不得与生产 R2_BUCKET 同名
#   - 离线模式禁止任何非本地 socket 连接（可安装运行时守卫）
import os
import socket
from pathlib import Path
from urllib.parse import urlsplit, unquote

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", ""}


class IsolationError(RuntimeError):
    """隔离红线被触碰 —— 必须立即停止。"""


class IsolationGuard:
    def __init__(self, *, parity_dsn: str, allowed_hosts=None, temp_root,
                 r2_bucket=None, prod_r2_bucket=None, prod_dsn=None):
        self.dsn = (parity_dsn or "").strip()
        self.allowed_hosts = {h.strip().lower()
                              for h in (allowed_hosts or []) if h and h.strip()}
        self.temp_root = Path(temp_root).resolve()
        self.r2_bucket = (r2_bucket or "").strip() or None
        self.prod_r2_bucket = (prod_r2_bucket
                               or os.environ.get("R2_BUCKET", "")).strip() or None
        self.prod_dsn = (prod_dsn or os.environ.get("DATABASE_URL", "")).strip() or None
        self._socket_installed = False

    # ── 静态断言：每次进入关键阶段都调 ──
    def assert_safe(self, phase: str, what: str) -> None:
        # ① 生产账本入口必须在子进程环境里被清除
        if os.environ.get("DATABASE_URL", "").strip():
            raise IsolationError(
                f"[{phase}/{what}] 子进程仍含 DATABASE_URL —— 拒绝运行")
        parts = urlsplit(self.dsn)
        scheme = parts.scheme.split("+")[0]
        if scheme not in {"sqlite", "postgres", "postgresql"}:
            raise IsolationError(f"[{phase}/{what}] 不支持的 DSN scheme: {scheme!r}")
        if scheme == "sqlite":
            db = Path(unquote(parts.path)).resolve()
            if str(db) == ":memory:" or self.temp_root not in db.parents:
                raise IsolationError(
                    f"[{phase}/{what}] 隔离库必须位于 {self.temp_root} 内: {db}")
        else:
            host = (parts.hostname or "").lower()
            if host not in self.allowed_hosts:
                raise IsolationError(
                    f"[{phase}/{what}] PG host {host!r} 不在 allowlist")
        # ② 验收桶不得与生产桶同名（对称防生产）
        if self.r2_bucket and self.prod_r2_bucket and \
                self.r2_bucket == self.prod_r2_bucket:
            raise IsolationError(
                f"[{phase}/{what}] 验收桶与生产 R2_BUCKET 同名 —— 拒绝运行")
        if phase in ("runtime", "operation", "cleanup") and \
                not self.temp_root.exists():
            raise IsolationError(f"[{phase}/{what}] temp_root 不存在")

    def assert_temp_path(self, path) -> Path:
        p = Path(path).resolve()
        if self.temp_root not in p.parents and p != self.temp_root:
            raise IsolationError(f"路径逃逸 temp_root: {p}")
        return p

    def assert_local_socket(self, host: str) -> None:
        """离线模式：只允许回环。"""
        if (host or "").strip().lower() not in _LOCAL_HOSTS:
            raise IsolationError(f"离线模式禁止连接非本地地址: {host!r}")

    # ── 运行时守卫：offline 下任何非本地 socket 连接直接抛 ──
    def install_socket_guard(self):
        if self._socket_installed:
            return
        orig = socket.socket.connect

        def guarded(self_sock, address, *a, **kw):
            host = address[0] if isinstance(address, tuple) else str(address)
            self.assert_local_socket(host)
            return orig(self_sock, address, *a, **kw)

        socket.socket.connect = guarded
        self._socket_installed = True


__all__ = ["IsolationGuard", "IsolationError"]
