# core/local_secret.py —— 本机签名密钥：env 优先，其次**落盘持久**（state/<name>.secret）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 逐行复核）：gui_grant.py:26-32 与 tentacle_fleet.py:364-367 都是
#   「没 env 就 secrets.token_hex(16) 进程内临时生成」—— 而面板是一个进程、触手/CLI 是另一个进程，
#   两个进程各生成一把 ⇒ **跨进程令牌必然验签失败**，主人明明签了授权却报「未授权」（假未授权）。
# 口径（边界写清楚）：这是**本机 HMAC 签名密钥**，不是任何第三方凭据；
#   · env 仍然优先（GUI_GRANT_SECRET / GBT_ORDER_SECRET）；
#   · 没 env 就落盘 state/<name>.secret（state/ 已在 .gitignore，权限尽力收紧到 0600）；
#   · 盘也写不了才退回进程内临时，并让调用方**看得见** source=memory（不假装持久）。
from __future__ import annotations

import os
import secrets
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
SECRET_DIR = Path(os.environ.get("V9_SECRET_DIR", str(ROOT / "state")))
_CACHE: dict = {}


def local_secret(name: str, env_name: str) -> dict:
    """→ {"key": bytes, "source": "env"|"file"|"memory", "path": str}"""
    v = os.environ.get(env_name)
    if v:
        return {"key": v.encode(), "source": "env", "path": ""}
    cached = _CACHE.get(name)
    if cached is not None:
        return cached
    p = SECRET_DIR / f"{name}.secret"
    s = ""
    try:
        if p.is_file():
            s = p.read_text(encoding="utf-8").strip()
        if not s:
            s = secrets.token_hex(16)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(s, encoding="utf-8")
            try:
                os.chmod(p, 0o600)
            except OSError as e:
                _swallow(__file__, e)

    except OSError:
        s = s or secrets.token_hex(16)          # 盘写不了 → 进程内，并如实标 memory
    out = {"key": s.encode(),
           "source": ("memory" if not p.is_file() else "file"),
           "path": str(p)}
    _CACHE[name] = out
    return out


def is_persistent(name: str, env_name: str) -> bool:
    """密钥是否**跨进程可用**（env 或落盘都算）。false = 只在当前进程里有效。"""
    return local_secret(name, env_name)["source"] != "memory"


__all__ = ["local_secret", "is_persistent", "SECRET_DIR"]
