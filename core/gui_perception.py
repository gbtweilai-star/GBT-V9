# core/gui_perception.py —— 屏幕感知层（蒸馏自 Mano-P：端侧 GUI-VLA 的"看"）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏要点（Mano-P 的纯视觉路线 → V9 的分层感知）：
#   ① 把屏幕变成模型能读的结构：元素清单（UIA 无障碍树 + OCR 文字块，带序号与矩形）
#   ② Set-of-Marks：把编号画回图上，供视觉模型"指着说"（有 VLM 时用，没有就只用清单）
#   ③ 端侧私有：GUI_LOCAL_ONLY=1（默认）时截图绝不外发；外发只允许"文字清单"且需显式关闭
# 纪律：探不到就说探不到（UIA/OCR 不可用 → notes 里写明），绝不编造元素
from __future__ import annotations
from core.swallow import swallow as _swallow

import io
import os
import time
from dataclasses import dataclass, asdict, field

# ── 隐私闸门 ──
# 老板口径（2026-10-06）：主脑与 100 根触手共用【统一密钥网关】，GUI 规划也走这条主线，
# 所以允许外发的是【文字元素清单】；【截图永远本地】——那是私有 AI 的底线。
def local_only() -> bool:
    """图片是否锁死在设备内。默认锁死（GUI_LOCAL_ONLY=1）。"""
    return os.environ.get("GUI_LOCAL_ONLY", "1") != "0"


def allow_text_out() -> bool:
    """文字元素清单能否发给统一密钥网关。默认允许；GUI_ALLOW_TEXT_OUT=0 可完全断网。"""
    return os.environ.get("GUI_ALLOW_TEXT_OUT", "1") != "0"


@dataclass
class Element:
    idx: int                     # 1 起的编号（Set-of-Marks / 模型引用用）
    name: str
    role: str = ""
    rect: list = field(default_factory=list)   # [x, y, w, h]（屏幕坐标）
    source: str = "uia"                        # uia | ocr
    value: str = ""
    enabled: bool = True

    @property
    def center(self) -> list:
        if len(self.rect) != 4:
            return []
        x, y, w, h = self.rect
        return [int(x + w / 2), int(y + h / 2)]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["center"] = self.center
        return d


def _iou(a: list, b: list) -> float:
    """两个矩形的交并比（[x,y,w,h]）。用来把 OCR 与 UIA 的同一控件去重。"""
    if len(a) != 4 or len(b) != 4:
        return 0.0
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1, y1 = max(ax, bx), max(ay, by)
    x2, y2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x2 <= x1 or y2 <= y1:
        return 0.0
    inter = (x2 - x1) * (y2 - y1)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


# ═══════════ ① 截图 ═══════════
def capture(path=None):
    """截当前屏幕。mss(最快) → PIL.ImageGrab → pyautogui，全不可用则抛异常。"""
    img = None
    try:
        import mss                                  # type: ignore
        from PIL import Image
        _mss = getattr(mss, "MSS", None) or mss.mss   # 新版用 mss.MSS，旧版 mss.mss
        with _mss() as sct:
            shot = sct.grab(sct.monitors[0])
            img = Image.frombytes("RGB", shot.size, shot.rgb)
    except Exception:
        img = None
    if img is None:
        try:
            from PIL import ImageGrab
            img = ImageGrab.grab()
        except Exception:
            img = None
    if img is None:
        try:
            import pyautogui
            img = pyautogui.screenshot()
        except Exception as exc:                     # noqa: BLE001
            raise RuntimeError(f"截图不可用（mss/ImageGrab/pyautogui 都没成）：{exc}") from exc
    if path:
        img.save(str(path))
    return img


# ═══════════ ② 元素：UIA 无障碍树 ═══════════
UIA_ROLES = ("Button", "Edit", "Hyperlink", "MenuItem", "ListItem", "TabItem",
             "CheckBox", "RadioButton", "ComboBox", "Text", "TreeItem", "ToolBar",
             "Document", "Window")


def elements_from_uia(limit: int = 80, extra_roles: tuple = ()) -> tuple[list[Element], str]:
    """Windows UI Automation 取可交互控件。返回 (元素, 备注)。非 Windows/失败 → 空表 + 原因。"""
    try:
        import uiautomation as auto                       # type: ignore
    except Exception as exc:                              # noqa: BLE001
        return [], f"uia_import_failed:{type(exc).__name__}"
    roles = set(UIA_ROLES) | set(extra_roles)
    out: list[Element] = []
    try:
        root = auto.GetRootControl()
        stack = [(root, 0)]
        seen_rects: list[list] = []
        while stack and len(out) < limit:
            ctrl, depth = stack.pop()
            if depth > 12:
                continue
            try:
                children = ctrl.GetChildren()
            except Exception:
                children = []
            for c in children:
                try:
                    name = (c.Name or "").strip()
                    ctype = getattr(c, "ControlTypeName", "") or ""
                    r = c.BoundingRectangle
                    rect = [int(r.left), int(r.top), int(r.width), int(r.height)]
                    off = bool(getattr(c, "IsOffscreen", False))
                    if (name and ctype in roles and rect[2] > 0 and rect[3] > 0 and not off):
                        if not any(_iou(rect, s) > 0.85 for s in seen_rects):
                            seen_rects.append(rect)
                            out.append(Element(idx=0, name=name[:120], role=ctype,
                                               rect=rect, source="uia", enabled=True))
                except Exception:
                    continue
                stack.append((c, depth + 1))
    except Exception as exc:                              # noqa: BLE001
        return [], f"uia_walk_failed:{type(exc).__name__}"
    return out, "ok"


# ═══════════ ③ 元素：OCR 文字块（UIA 拿不到时的兜底）═══════════
def elements_from_ocr(img, limit: int = 80) -> tuple[list[Element], str]:
    try:
        import pytesseract                                # type: ignore
        from pytesseract import Output
    except Exception as exc:                              # noqa: BLE001
        return [], f"tesseract_import_failed:{type(exc).__name__}"
    try:
        data = pytesseract.image_to_data(img, output_type=Output.DICT)
    except Exception as exc:                              # noqa: BLE001
        return [], f"tesseract_run_failed:{type(exc).__name__}"
    out: list[Element] = []
    seen: set = set()
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if len(text) < 2:
            continue
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1.0
        if conf < 40:
            continue
        key = (text.lower(), data["left"][i] // 8, data["top"][i] // 8)
        if key in seen:
            continue
        seen.add(key)
        out.append(Element(idx=0, name=text[:120], role="Text",
                           rect=[int(data["left"][i]), int(data["top"][i]),
                                 int(data["width"][i]), int(data["height"][i])],
                           source="ocr"))
        if len(out) >= limit:
            break
    return out, "ok"


# ═══════════ ④ 合并 + 编号（读序）═══════════
def merge_elements(uia: list[Element], ocr: list[Element], *, max_n: int = 60) -> list[Element]:
    """UIA 优先；OCR 只补 UIA 没框住的区域。按 (上→下, 左→右) 排号。"""
    merged = list(uia)
    for o in ocr:
        if any(_iou(o.rect, u.rect) > 0.6 for u in merged):
            continue
        merged.append(o)
    merged.sort(key=lambda e: (e.rect[1] // 12 if len(e.rect) == 4 else 0,
                               e.rect[0] if len(e.rect) == 4 else 0))
    merged = merged[:max_n]
    for i, e in enumerate(merged, 1):
        e.idx = i
    return merged


# ═══════════ ⑤ Set-of-Marks 标注图（给 VLM"指着说"）═══════════
def annotate(img, elements: list[Element]):
    """把编号画回图上。返回 PNG bytes；PIL 不可用则返回 None（不假装画出来了）。"""
    try:
        from PIL import ImageDraw
        canvas = img.copy()
        d = ImageDraw.Draw(canvas)
        for e in elements:
            if len(e.rect) != 4:
                continue
            x, y, w, h = e.rect
            d.rectangle([x, y, x + w, y + h], outline=(255, 64, 64), width=2)
            d.rectangle([x, max(0, y - 14), x + 26, y], fill=(255, 64, 64))
            d.text((x + 5, max(0, y - 13)), str(e.idx), fill=(255, 255, 255))
        buf = io.BytesIO()
        canvas.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return None


# ═══════════ ⑥ 给模型看的文字清单 ═══════════
def screen_text(elements: list[Element], *, max_n: int = 60) -> str:
    lines = []
    for e in elements[:max_n]:
        addr = f"@({e.center[0]},{e.center[1]})" if e.center else ""
        val = f" = {e.value[:60]!r}" if e.value else ""
        lines.append(f"[{e.idx}] {e.role or e.source}: {e.name}{val} {addr}")
    return "\n".join(lines)


# ═══════════ ⑦ 屏幕几何与坐标换算（纯视觉必须精确落到像素）═══════════
def screen_geometry() -> dict:
    """屏幕几何 + DPI 缩放。视觉给的坐标是**图像像素**，pyautogui 要的是**逻辑坐标**，
    高 DPI（125%/150%）下两者不同 —— 不换算就会点偏。
    """
    geom = {"left": 0, "top": 0, "width": 0, "height": 0, "dpr": 1.0, "source": "unknown"}
    try:
        import mss
        _mss = getattr(mss, "MSS", None) or mss.mss
        with _mss() as sct:
            m = sct.monitors[0]
            geom.update(left=m["left"], top=m["top"], width=m["width"], height=m["height"],
                        source="mss")
    except Exception:
        try:
            import pyautogui
            w, h = pyautogui.size()
            geom.update(width=w, height=h, source="pyautogui")
        except Exception as e:
            _swallow(__file__, e)
    try:                                    # Windows: 系统 DPI（每显示器缩放不同则退化为 1.0）
        import ctypes
        dpi = ctypes.windll.user32.GetDpiForSystem()
        if dpi:
            geom["dpr"] = round(float(dpi) / 96.0, 4)
    except Exception as e:
        _swallow(__file__, e)
    return geom


def to_screen(x, y, geom: dict = None) -> list:
    """图像像素坐标 → 逻辑屏幕坐标（pyautogui 口径）。"""
    g = geom or screen_geometry()
    dpr = g.get("dpr") or 1.0
    return [int(round(x / dpr)) + int(g.get("left") or 0),
            int(round(y / dpr)) + int(g.get("top") or 0)]


def from_screen(x, y, geom: dict = None) -> list:
    """逻辑屏幕坐标 → 图像像素坐标。"""
    g = geom or screen_geometry()
    dpr = g.get("dpr") or 1.0
    return [int(round((x - int(g.get("left") or 0)) * dpr)),
            int(round((y - int(g.get("top") or 0)) * dpr))]


# ═══════════ ⑧ 一次完整感知 ═══════════
def perceive(*, max_elements: int = 60, capture_fn=None, uia_fn=None,
             ocr_fn=None, want_marks: bool = True,
             vision_only: bool = False) -> dict:
    """截图 + 元素 + 编号图 + 屏幕几何。

    vision_only=True → **完全不读无障碍树**（有些 APP 会屏蔽 UIA，纯视觉才不被护栏挡住），
    元素只来自 OCR/像素；模型可以直接给坐标，由 to_screen() 精确落到鼠标。
    任何一路失败都写进 notes，不编数据。
    """
    notes: list[str] = []
    geom = screen_geometry()
    img = None
    try:
        img = (capture_fn or capture)()
    except Exception as exc:                              # noqa: BLE001
        notes.append(f"capture_failed:{type(exc).__name__}:{exc}")
    size = list(getattr(img, "size", ())) if img is not None else []

    uia: list[Element] = []
    if vision_only:
        notes.append("vision_only:未读无障碍树（纯视觉）")
    else:
        uia, uia_note = (uia_fn or elements_from_uia)()
        if uia_note != "ok":
            notes.append(f"uia:{uia_note}")
    ocr: list[Element] = []
    if img is not None:
        ocr, ocr_note = (ocr_fn or elements_from_ocr)(img)
        if ocr_note != "ok":
            notes.append(f"ocr:{ocr_note}")
    else:
        notes.append("ocr:skipped_no_image")

    elements = merge_elements(uia, ocr, max_n=max_elements)
    marks = annotate(img, elements) if (img is not None and want_marks) else None
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "size": size, "geometry": geom, "vision_only": bool(vision_only),
            "elements": [e.as_dict() for e in elements],
            "text": screen_text(elements), "marks_png": marks,
            "sources": {"uia": len(uia), "ocr": len(ocr)},
            "notes": notes, "local_only": local_only()}


def assert_may_send(what: str, *, image: bool = False) -> None:
    """外发闸门：图片永远不许出设备；文字清单要 GUI_ALLOW_TEXT_OUT≠0。"""
    if image and local_only():
        raise PermissionError(f"GUI_LOCAL_ONLY=1（私有模式）：拒绝把 {what}（截图）发到设备外；"
                              f"确需外发请显式设 GUI_LOCAL_ONLY=0")
    if not image and not allow_text_out():
        raise PermissionError(f"GUI_ALLOW_TEXT_OUT=0：拒绝把 {what} 发到设备外")
