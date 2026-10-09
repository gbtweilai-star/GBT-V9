# core/gui_grant.py —— 主人授权令牌：权限由你签发，签了就没有做不到的动作
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径（主人 2026-10-06）：借鉴 Mano-P 的纯视觉路线，但不抄它的实现 ——
#   · 纯视觉 = 只靠像素与键鼠，不依赖任何 APP 可以关掉的无障碍接口（不被护栏挡）
#   · 授权 = 主人签发的令牌是**唯一闸门**：签了就能做任何动作（含删除/转账/卸载这类
#     高风险原语），没签就在执行前停下等你点头。
#   · 硬编码关键词黑名单不再是"老板"——它只用来【标注风险等级】，不作为否决权。
#
# 诚实标注（这条我不改口径）：令牌授权的是"用人类的方式操作本机软件"，
# 不是绕过操作系统/硬件级安全机制（提权、驱动、EDR）——那些不是我们能"实现"的东西，
# 也不该假装能做到。除此之外，本机任何 APP 的操作都在触手能力范围内。
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid

GRANT_TTL = int(os.environ.get("GUI_GRANT_TTL", "1800"))
GRANT_ISSUER = os.environ.get("GUI_GRANT_ISSUER", "主人")
_SCOPE_ALL = "*"


def _secret() -> bytes:
    """签名密钥：env 优先，其次**落盘持久**（core/local_secret）。

    ★2026-10-08 修「假未授权」：原先没 env 就进程内临时生成 —— 面板一个进程、
    触手/CLI 另一个进程各生成一把，跨进程令牌必然验签失败。现在落 state/gui_grant.secret，
    所有进程共用一把（env 仍然优先）。密钥本体绝不进源码。
    """
    from core.local_secret import local_secret
    return local_secret("gui_grant", "GUI_GRANT_SECRET")["key"]


def ephemeral() -> bool:
    """true = 这把密钥**只在当前进程有效**（env/落盘都拿不到）。"""
    from core.local_secret import is_persistent
    return not is_persistent("gui_grant", "GUI_GRANT_SECRET")


class GrantError(RuntimeError):
    pass


class Grant:
    """一次操作授权：作用域 + 有效期 + 签名。一次签发可覆盖一个任务的全部步骤。"""

    def __init__(self, *, scope=(_SCOPE_ALL,), ttl=GRANT_TTL, note="",
                 issuer=GRANT_ISSUER, primitives=None, apps=None, max_steps=None):
        self.data = {
            "grant_id": uuid.uuid4().hex[:16],
            "issuer": issuer,
            "issued_at": int(time.time()),
            "expires_at": int(time.time()) + int(ttl),
            "scope": list(scope or [_SCOPE_ALL]),
            "primitives": list(primitives) if primitives else [_SCOPE_ALL],
            "apps": list(apps) if apps else [_SCOPE_ALL],
            "max_steps": int(max_steps) if max_steps else None,
            "note": str(note)[:200],
        }
        self.data["sig"] = self.sign(self.data)

    # ── 签名 ──
    @staticmethod
    def sign(payload: dict) -> str:
        body = json.dumps({k: v for k, v in payload.items() if k != "sig"},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()

    @property
    def id(self) -> str:
        return self.data["grant_id"]

    def token(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, sort_keys=True)

    # ── 校验：fail closed ──
    @classmethod
    def verify(cls, token) -> tuple[bool, str, dict]:
        if token is None:
            return False, "no_grant（没有主人授权：高风险动作在执行前停下等确认）", {}
        data = token
        if isinstance(token, str):
            try:
                data = json.loads(token)
            except Exception:                                # noqa: BLE001
                return False, "grant_unparsable", {}
        if not isinstance(data, dict) or not data.get("sig"):
            return False, "grant_missing_signature", {}
        if data["sig"] != cls.sign(data):
            return False, "grant_bad_signature（令牌被篡改）", data
        if int(data.get("expires_at", 0)) < int(time.time()):
            return False, "grant_expired（授权已过期，请主人重新签发）", data
        return True, "ok", data

    @classmethod
    def verify_action(cls, token, *, primitive: str, app: str | None = None,
                      step: int | None = None) -> tuple[bool, str, dict]:
        """动作级校验：原语与 APP 是否在授权范围内，步数是否超限。"""
        ok, why, data = cls.verify(token)
        if not ok:
            return ok, why, data
        prims = data.get("primitives") or [_SCOPE_ALL]
        if _SCOPE_ALL not in prims and primitive not in prims:
            return False, f"grant_scope_denied（授权不含原语 {primitive}）", data
        apps = data.get("apps") or [_SCOPE_ALL]
        if app and _SCOPE_ALL not in apps and app not in apps:
            return False, f"grant_app_denied（授权不含应用 {app}）", data
        cap = data.get("max_steps")
        if cap and step and step > int(cap):
            return False, f"grant_step_limit（授权最多 {cap} 步）", data
        return True, "ok", data


def issue(*, scope=(_SCOPE_ALL,), ttl=GRANT_TTL, note="", primitives=None, apps=None,
          max_steps=None, issuer=GRANT_ISSUER) -> str:
    """签发令牌（主人用）。返回可直接传下去的 token 字符串。"""
    return Grant(scope=scope, ttl=ttl, note=note, primitives=primitives, apps=apps,
                 max_steps=max_steps, issuer=issuer).token()


__all__ = ["Grant", "GrantError", "issue", "ephemeral", "GRANT_TTL", "GRANT_ISSUER"]
