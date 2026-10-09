# core/tentacle_gui.py —— 加固触手（闭环 + 焦点锁 + 不盲打）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人令（2026-10-09）：「你调用触手不能完善嘛？」
# 实测出的三个根因，这里逐个治：
#   ① **焦点被抢**：LobeHub/设置窗会反复抢前台，SetForegroundWindow 常被 Windows 拒绝。
#      → 治法：ALT 解锁 + AttachThreadInput 绑前台线程（实测能把前台抢回来），
#        且**每次动作前都重新核对** GetForegroundWindow == 目标窗口，不是一次就算了。
#   ② **盲打会误入他窗**：一次 typewrite 几十个字符，中间被抢就全打错窗口。
#      → 治法：改用**剪贴板粘贴**（一次 Ctrl+V），且只在守卫通过后按；守卫不过就中止。
#   ③ **坐标会飘**：页面滚动/面板位移后，记住的坐标会点到别的元素。
#      → 治法：**先看图找元素**（按颜色找醒目的按钮）+ **点完回读验证**（截图比对变化）
#        + 没变化就在附近网格重试 —— 把"盲点"变成"闭环"。
from __future__ import annotations

import time
from pathlib import Path

import ctypes
import ctypes.wintypes as wt

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
TOPMOST, SWP_NOMOVE, SWP_NOSIZE, SWP_SHOWWINDOW = -1, 0x0002, 0x0001, 0x0040
VK_MENU = 0x12


def title_of(hwnd: int) -> str:
    n = u32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    b = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(hwnd, b, n + 1)
    return b.value


def find_window(kw: str, exclude: tuple = ()) -> int | None:
    hits = []
    EP = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(h, l):
        if u32.IsWindowVisible(h):
            t = title_of(h)
            if kw.lower() in t.lower() and not any(x.lower() in t.lower() for x in exclude):
                r = wt.RECT()
                u32.GetWindowRect(h, ctypes.byref(r))
                hits.append((int(h), (r.right - r.left) * (r.bottom - r.top)))
        return True
    u32.EnumWindows(EP(cb), 0)
    hits.sort(key=lambda x: -x[1])
    return hits[0][0] if hits else None


def front(hwnd: int, tries: int = 5) -> bool:
    """把窗口拉到前台并**确认**（ALT 解锁 + AttachThreadInput）。返回是否真的在前台。"""
    for _ in range(tries):
        if int(u32.GetForegroundWindow()) == hwnd:
            return True
        u32.keybd_event(VK_MENU, 0, 0, 0)
        time.sleep(0.04)
        u32.keybd_event(VK_MENU, 0, 2, 0)
        fg = u32.GetForegroundWindow()
        ft = u32.GetWindowThreadProcessId(fg, None)
        my = k32.GetCurrentThreadId()
        u32.AttachThreadInput(my, ft, True)
        try:
            u32.ShowWindow(hwnd, 9)
            u32.BringWindowToTop(hwnd)
            u32.SetWindowPos(hwnd, TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            u32.SetForegroundWindow(hwnd)
            u32.SetActiveWindow(hwnd)
            u32.SetFocus(hwnd)
        finally:
            u32.AttachThreadInput(my, ft, False)
        time.sleep(0.45)
    return int(u32.GetForegroundWindow()) == hwnd


def paste_text(text: str) -> bool:
    """把文本放进剪贴板（不按键）——配合 guarded 的 Ctrl+V 使用。"""
    try:
        u32.OpenClipboard(None)
        u32.EmptyClipboard()
        CF_UNICODETEXT = 13
        buf = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        h = k32.GlobalAlloc(0x0042, size)          # GMEM_MOVEABLE
        p = k32.GlobalLock(h)
        ctypes.memmove(p, buf, size)
        k32.GlobalUnlock(h)
        u32.SetClipboardData(CF_UNICODETEXT, h)
        return True
    except Exception:
        return False
    finally:
        u32.CloseClipboard()


def find_color_region(im, rgb, tol=48, roi=None, min_cells=6):
    """**看图找元素**：在截图里找指定颜色（按钮色）的最大连通块，返回其中心与包围盒。

    用网格聚类（不依赖 scipy）：把图降采样成格子，命中的格子做 BFS 连通，取最大块。
    """
    import numpy as np
    a = np.asarray(im.convert("RGB")).astype(int)
    if roi:
        x0, y0, x1, y1 = roi
        a = a[y0:y1, x0:x1]
        ox, oy = x0, y0
    else:
        ox, oy = 0, 0
    d = np.abs(a - np.array(rgb)).sum(axis=2)
    mask = d < tol
    step = 4
    gh, gw = a.shape[0] // step, a.shape[1] // step
    grid = np.zeros((gh, gw), bool)
    for gy in range(gh):
        for gx in range(gw):
            blk = mask[gy * step:(gy + 1) * step, gx * step:(gx + 1) * step]
            grid[gy, gx] = blk.mean() > 0.55
    seen = np.zeros_like(grid)
    best = None
    for gy in range(gh):
        for gx in range(gw):
            if not grid[gy, gx] or seen[gy, gx]:
                continue
            stack, cells = [(gy, gx)], []
            seen[gy, gx] = True
            while stack:
                cy, cx = stack.pop()
                cells.append((cy, cx))
                for ny, nx in ((cy+1, cx), (cy-1, cx), (cy, cx+1), (cy, cx-1)):
                    if 0 <= ny < gh and 0 <= nx < gw and grid[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(cells) >= min_cells and (best is None or len(cells) > len(best)):
                best = cells
    if not best:
        return None
    ys = [c[0] for c in best]
    xs = [c[1] for c in best]
    cy = (sum(ys) / len(ys)) * step + step / 2 + oy
    cx = (sum(xs) / len(xs)) * step + step / 2 + ox
    return {"中心": (int(cx), int(cy)), "格子": len(best),
            "包围盒": (int(min(xs) * step + ox), int(min(ys) * step + oy),
                       int((max(xs) + 1) * step + ox), int((max(ys) + 1) * step + oy))}


def diff_ratio(im_a, im_b) -> float:
    """两张截图的变化比例（判断"点了到底有没有反应"）。"""
    import numpy as np
    a = np.asarray(im_a.convert("L").resize((160, 90))).astype(int)
    b = np.asarray(im_b.convert("L").resize((160, 90))).astype(int)
    return float((np.abs(a - b) > 18).mean())


__all__ = ["title_of", "find_window", "front", "paste_text", "find_color_region", "diff_ratio"]
