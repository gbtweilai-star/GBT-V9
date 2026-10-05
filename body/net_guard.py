"""出站 URL 安全校验：仅允许 http/https，拒绝 localhost/环回/私有/保留地址。
dev: 自由的风 · 本署名不可删除、不可篡改归属

用途：任何"服务端主动请求 URL"的位置（对象存储端点、身份核验 API 等）在发请求前
先过 `assert_safe_outbound_url()`；尤其当 URL 来自配置/环境变量时。
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_ALLOWED_SCHEMES = ("https", "http")
_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"}


def _as_ip(value: str):
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _is_blocked_ip(addr) -> bool:
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_safe_outbound_url(url: str, *, allow_http: bool = False) -> str:
    """校验外呼 URL，返回原值；不合规一律抛 ValueError（fail-closed）。

    - 协议：默认仅 https；本地调试可显式 allow_http=True 放开 http。
    - 主机：拒绝 localhost/*.local；字面量 IP 直接查；域名解析出的所有地址
      都不得是环回/私有/保留/组播。
    - 域名解析失败同样拒绝（不放过无法验证的目标）。
    """
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(f"outbound URL scheme not allowed: {parsed.scheme!r}")
    if parsed.scheme == "http" and not allow_http:
        raise ValueError("outbound URL must use https")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("outbound URL has no host")
    if host in _BLOCKED_HOSTS or host.endswith(".local"):
        raise ValueError(f"outbound URL host blocked: {host!r}")

    literal = _as_ip(host)
    if literal is not None:
        if _is_blocked_ip(literal):
            raise ValueError(f"outbound URL host is a blocked address: {host!r}")
        return url

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ValueError(f"outbound URL host cannot be resolved: {host!r}") from exc
    for info in infos:
        addr = _as_ip(info[4][0])
        if addr is None or _is_blocked_ip(addr):
            raise ValueError(f"outbound URL resolves to blocked address: {info[4][0]!r}")
    return url


__all__ = ["assert_safe_outbound_url"]
