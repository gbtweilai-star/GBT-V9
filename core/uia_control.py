# core/uia_control.py —— 触手穿透层：被遮挡/最小化的窗口照样操控（Win32 句柄级）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）："触手可以无视遮挡物直接使用触手穿透过去，操作遮挡物后面的东西。"
#
# 软件里的真实现 = **句柄级操控**：Windows 每个窗口都有句柄（HWND），
#   不把窗口带到前台、不需要它可见，就能直接：
#     枚举全部窗口 → 读标题/类名/文本 → 移动/缩放/恢复 → 向指定窗口发按键与命令。
#   这是 Windows 无障碍/自动化接口的官方能力，不是破解；配合 consent 闸门使用。
#   （控件级更深的穿透可后续挂 pywinauto/UIA；本模块零依赖、ctypes 直调。）
import ctypes
import ctypes.wintypes as wt
import json
import os
import time
from pathlib import Path

from core import hooks as H
from core import page_control as PC
from core.swallow import swallow as _swallow

user32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None
IS_WIN = user32 is not None
JOURNAL = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).joinpath(
    "state", "uia_control.jsonl")

WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
WM_CLOSE = 0x0010


def _requires_windows() -> None:
    H.gate("Windows 平台", IS_WIN, "句柄级操控只在 Windows 上可用")


def consent_needed(fn):
    """每个动作都过同意闸门 + 落审计（追加式）。"""
    def wrapper(*args, **kw):
        _requires_windows()
        if not PC.consent()["授权"]:
            _log({"kind": "refused", "action": fn.__name__, "args": str(args)[:120],
                  "why": "未授权"})
            return {"ok": False, "reason": "未授权：在语音对讲页开「允许她操作页面」后重试",
                    "需要授权": True}
        r = fn(*args, **kw)
        _log({"kind": "action", "action": fn.__name__, "args": str(args)[:160],
              "result": str(r)[:240], "ok": bool(r.get("ok", True))})
        return r
    return wrapper


def _log(rec: dict) -> None:
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), **rec}, ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)



def windows(*, visible_only: bool = False) -> list:
    """穿透枚举：**包括被遮挡、被最小化的窗口**（这就是"无视遮挡物"的第一层）。"""
    _requires_windows()
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def _cb(hwnd, _l):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        ln = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(ln + 1)
        user32.GetWindowTextW(hwnd, title, ln + 1)
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        t = title.value.strip()
        if t:
            out.append({"hwnd": int(hwnd), "标题": t, "类名": cls.value,
                        "可见": bool(user32.IsWindowVisible(hwnd)),
                        "最小化": bool(user32.IsIconic(hwnd))})
        return True
    user32.EnumWindows(_cb, 0)
    return out


@consent_needed
def read_window(title_or_hwnd) -> dict:
    """穿透读取：窗口在前台与否都行，直接读它的标题与文本（WM_GETTEXT）。"""
    h = _find(title_or_hwnd)
    if not h:
        return {"ok": False, "reason": f"找不到窗口：{title_or_hwnd}"}
    ln = user32.SendMessageW(h, WM_GETTEXTLENGTH, 0, 0)
    buf = ctypes.create_unicode_buffer(ln + 1)
    user32.SendMessageW(h, WM_GETTEXT, ln + 1, buf)
    return {"ok": True, "hwnd": h, "文本": buf.value[:2000],
            "口径": "句柄级读取：不需要窗口可见或在前台"}


@consent_needed
def restore_window(title_or_hwnd) -> dict:
    """把最小化的窗口恢复（不抢前台焦点）。"""
    h = _find(title_or_hwnd)
    if not h:
        return {"ok": False, "reason": f"找不到窗口：{title_or_hwnd}"}
    user32.ShowWindow(h, 4)          # SW_SHOWNOACTIVATE：显示但不抢焦点
    return {"ok": True, "hwnd": h, "动作": "已恢复（不抢焦点）"}


@consent_needed
def move_window(title_or_hwnd, x: int, y: int, w: int, h_: int) -> dict:
    h = _find(title_or_hwnd)
    if not h:
        return {"ok": False, "reason": f"找不到窗口：{title_or_hwnd}"}
    user32.MoveWindow(h, int(x), int(y), int(w), int(h_), True)
    return {"ok": True, "hwnd": h, "动作": f"移到 {x},{y} 尺寸 {w}×{h_}"}


@consent_needed
def send_text(title_or_hwnd, text: str) -> dict:
    """向指定窗口直接发送文本（不经剪贴板、不抢焦点）。"""
    # 视觉钉死：动手前必须有新鲜取景（主人令：禁传统瞎子操作）
    from core.senses_gate import require_eye as _re
    _eye = _re()
    if not _eye.get("ok"):
        return {"ok": False, "拒动": True, "在哪一步": "①眼", "读数": _eye}
    h = _find(title_or_hwnd)
    if not h:
        return {"ok": False, "reason": f"找不到窗口：{title_or_hwnd}"}
    import ctypes as C
    for ch in str(text or ""):
        C.windll.user32.SendMessageW(h, 0x0102, C.windll.user32.VkKeyScanW(ord(ch)) & 0xFF, 0)
    return {"ok": True, "hwnd": int(h), "已发送": len(str(text or ""))}


@consent_needed
def close_window(title_or_hwnd) -> dict:
    h = _find(title_or_hwnd)
    if not h:
        return {"ok": False, "reason": f"找不到窗口：{title_or_hwnd}"}
    user32.SendMessageW(h, WM_CLOSE, 0, 0)
    return {"ok": True, "hwnd": int(h), "动作": "已发关闭（等应用自己收尾）"}


def _find(title_or_hwnd):
    if isinstance(title_or_hwnd, int):
        return int(title_or_hwnd)
    want = str(title_or_hwnd).strip().lower()
    for w in windows():
        if want in w["标题"].lower():
            return int(w["hwnd"])          # 直接回 int 句柄（ctypes 传参即可，别包 HWND 对象）
    return None


def status() -> dict:
    n = len(windows()) if IS_WIN else 0
    return {"平台": "Windows" if IS_WIN else "非 Windows", "可见+遮挡窗口": n,
            "能力": ["穿透枚举（含最小化/被遮挡）", "句柄级读文本", "恢复（不抢焦点）",
                     "移动/缩放", "直发文本", "关闭"],
            "闸门": "全部动作过同意闸门 + 追加式审计（state/uia_control.jsonl）",
            "口径": "句柄级=官方自动化接口，不破解、不绕风控；控件级深穿透可后续挂 UIA"}


__all__ = ["windows", "read_window", "restore_window", "move_window", "send_text",
           "close_window", "status"]
