# parity/trace.py —— 用 ContextVar 把 token 透进真实写入路径
import contextvars

PROBE_TOKEN: contextvars.ContextVar[str | None] = \
    contextvars.ContextVar("parity_probe_token", default=None)

def bind_token(token: str):
    return PROBE_TOKEN.set(token)

def unbind(handle) -> None:
    PROBE_TOKEN.reset(handle)
