# core/eyewitness.py —— 目击证词：按动作类型选对眼睛（修"用错眼⇒频繁拒动"的真因）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「400ms 这个数她是对的，但她的 400ms 几乎不会触发（眼是 24ms 的流）；
#   我拿按需截图的眼去配 400ms ⇒ 频繁拒动 ⇒ 真的限制了她的能力发挥。」
#
# 口径（三条）：
#   ① 桌面动作（鼠标/键盘）→ 要**桌面流眼**；流不新鲜就**当场 mss 抓一张**（帧龄≈0）
#   ② 浏览器动作 → 要**浏览器自己的截图**（现抓，帧龄≈0），**不许拿桌面流当浏览器眼**
#   ③ 不需要眼的动作（模型/文件/进程/扫描）→ 不要眼，直接放行，不许因"眼"拒动
#   拒动原因必须分三种：眼没起来 / 两种来源都抓不到 / 抓到了但太旧（罕见）
from __future__ import annotations

import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAX_AGE_MS = 400.0
NO_EYE_KINDS = ("model", "file", "process", "scan", "memory", "read")


def _stream_look() -> dict:
    from core import vision_loop as VL
    return VL.eyes("main").look() or {}


def _grab_desktop() -> dict:
    """当场抓一张桌面帧（帧龄≈0）。"""
    try:
        import mss
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "为什么": "mss 不可用: %s" % type(e).__name__}
    t0 = time.time()
    try:
        with mss.mss() as sct:
            mon = sct.monitors[0]
            img = sct.grab({"left": 0, "top": 0, "width": min(640, mon["width"]), "height": min(360, mon["height"])})
            n = len(getattr(img, "rgb", b""))
        return {"ok": True, "来源": "按需截图(桌面)", "帧龄ms": round((time.time() - t0) * 1000, 1), "字节": n}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "为什么": "抓桌面失败: %s" % type(e).__name__}


def _grab_browser() -> dict:
    """当场抓一张浏览器页（帧龄≈0）——浏览器动作必须用这只眼。"""
    try:
        from core import browser_plug as BP
        st = BP._BROWSER or {}
        page = st.get("page")
        if page is None:
            return {"ok": False, "为什么": "浏览器没起来（先 open 一次）"}
        t0 = time.time()
        page.screenshot()  # 现抓一帧（不落盘）
        return {"ok": True, "来源": "按需截图(浏览器)", "帧龄ms": round((time.time() - t0) * 1000, 1)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "为什么": "抓浏览器失败: %s" % type(e).__name__}


def witness(kind: str = "desktop", *, max_age_ms: float = DEFAULT_MAX_AGE_MS) -> dict:
    """目击证词：给出**这次动作该用的眼**的新鲜度读数；能抓就抓，抓不到才拒。"""
    k = (kind or "desktop").lower()
    if k in NO_EYE_KINDS:
        return {"ok": True, "要眼": False, "来源": "无需眼（%s）" % k, "帧龄ms": 0.0}
    # ① 先用流（快）：流新鲜就直接用
    look = _stream_look()
    age = look.get("帧龄ms")
    if age is not None and age <= max_age_ms and k == "desktop":
        return {"ok": True, "要眼": True, "来源": "实时流(桌面)", "帧龄ms": age, "流": look}
    # ② 流不新鲜/不适用 → **当场抓**（这是修频繁拒动的关键）
    grab = _grab_browser() if k == "browser" else _grab_desktop()
    if grab.get("ok"):
        return {"ok": True, "要眼": True, "来源": grab.get("来源"), "帧龄ms": grab.get("帧龄ms"), "流": look}
    # ③ 两条都不行 → 如实拒动，说清是哪一种
    why = ("眼没起来（流无帧，且%s）" % grab.get("为什么")) if age is None else \
          ("两种来源都抓不到（流帧龄 %sms，且%s）" % (age, grab.get("为什么")))
    return {"ok": False, "要眼": True, "来源": "无", "帧龄ms": age, "拒动原因": why}


def self_check() -> dict:
    return {"桌面": witness("desktop"), "浏览器": witness("browser"), "无需眼": witness("model"),
            "口径": "按动作类型选对眼；流不新鲜就当场抓；抓不到才拒，并说清原因"}


__all__ = ["DEFAULT_MAX_AGE_MS", "NO_EYE_KINDS", "witness", "self_check"]
