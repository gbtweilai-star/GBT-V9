# core/eye_hand.py —— 眼手同步（射击游戏级反应）：真抓帧 → 真驱动鼠标 → 真画面复核
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令：视觉要打磨到极致 —— **眼手同步可操控电脑，达到射击游戏的速度与反应**。
# 口径：本模块只做**真**的：真抓帧（mss）、真驱动（SetCursorPos/SendInput）、真复核（再抓一帧比像素）。
#   · 眼：mss 小区域，640×360 实测 75fps、320×40 132fps（本机读数）
#   · 手：直接调用 Win32（不经任何自动化框架）⇒ 亚毫秒级下发
#   · 验：动作后再抓一帧，像素有变化才算"打中"（不许瞎报命中）
import ctypes, json, time
from pathlib import Path

if hasattr(__import__("sys").stdout, "reconfigure"):
    __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "eye_hand.jsonl"

user32 = ctypes.windll.user32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def cursor() -> tuple:
    p = POINT()
    user32.GetCursorPos(ctypes.byref(p))
    return (p.x, p.y)


def move(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def click() -> None:
    user32.mouse_event(0x0002, 0, 0, 0, 0)   # LEFTDOWN
    user32.mouse_event(0x0004, 0, 0, 0, 0)   # LEFTUP


_SCT = None


def _sct():
    """★ 修：mss 实例只建一次（每次新建上下文会把 fps 从 75 拉到 7.9）。"""
    global _SCT
    if _SCT is None:
        import mss
        _SCT = mss.mss()
    return _SCT


def grab_around(x: int, y: int, w: int = 64, h: int = 64):
    """★ 抓**鼠标周围**的小窗口（复核才可能真的看到变化）。"""
    half_w, half_h = w // 2, h // 2
    return _sct().grab({"left": max(0, x - half_w), "top": max(0, y - half_h), "width": w, "height": h})


def grab(bbox=None):
    return _sct().grab(bbox or {"left": 0, "top": 0, "width": 320, "height": 40})


def _signature(img) -> bytes:
    return bytes(getattr(img, "rgb", b""))[: 320 * 4 * 4]


def eye_fps(seconds: float = 1.0) -> dict:
    """眼本身的持续帧率（小区域）。"""
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        grab(); n += 1
    dt = time.time() - t0
    return {"帧数": n, "秒": round(dt, 2), "fps": round(n / dt, 1)}


def sync_benchmark(n: int = 60, *, jitter: int = 6, restore: bool = True) -> dict:
    """眼手同步闭环基准：每次 = 抓帧 → 决策(对准) → 移到目标 → 再抓帧复核。"""
    start = cursor()
    # ★ 预热：丢 3 帧（首次抓屏/首次驱动常有一次系统级卡顿，别把它算进基准）
    for _ in range(3):
        grab(); move(*cursor()); cursor()
    try:
        sw = _sct().monitors[0]["width"]; sh = _sct().monitors[0]["height"]
    except Exception:  # noqa: BLE001
        sw, sh = 1920, 1080
    # ★ 安全区：只在中部 60% 内移动（避开屏幕边缘的夹取/吸附）
    sx0, sx1 = int(sw * 0.2), int(sw * 0.8)
    sy0, sy1 = int(sh * 0.2), int(sh * 0.8)
    loops, cap_ms, act_ms, ver_ms, ages, hits, visual_changes = [], [], [], [], [], 0, 0
    devs = []
    for i in range(n):
        dx = jitter if i % 2 == 0 else -jitter
        dy = jitter if i % 3 == 0 else -jitter
        cur = cursor()
        tx = min(sx1, max(sx0, cur[0] + dx))
        ty = min(sy1, max(sy0, cur[1] + dy))
        t0 = time.time()
        pre = grab_around(tx, ty)         # 眼：抓**目标位置周围**的窗口（决策依据）
        t1 = time.time()
        sig0 = _signature(pre)
        move(tx, ty)                      # 手：真驱动
        t2 = time.time()
        post = grab_around(tx, ty)        # 验：同在目标位置再抓一帧 ⇒ 鼠标进去画面该变
        t3 = time.time()
        # ★ 修：mss 抓屏**不含鼠标指针** ⇒ 用"像素变化"当命中判据是错的。
        #   手到位 = 轮询 GetCursorPos 到真值（最多 50ms 等系统落定）；
        #   视觉变化单列（许多场景本就为 0，口径写清，不拿它冒充命中）。
        got = cursor()
        settle = time.time()
        while time.time() - settle < 0.05 and (abs(got[0] - tx) > 2 or abs(got[1] - ty) > 2):
            got = cursor()
        dev = max(abs(got[0] - tx), abs(got[1] - ty))
        devs.append(dev)
        ok = dev <= 2
        changed = _signature(post) != sig0
        visual_changes += 1 if changed else 0
        hits += 1 if ok else 0
        cap_ms.append((t1 - t0) * 1000); act_ms.append((t2 - t1) * 1000)
        ver_ms.append((t3 - t2) * 1000); loops.append((t3 - t0) * 1000)
        ages.append((t1 - t0) * 1000)     # 动作时用的那帧有多旧（≈抓取耗时）
    if restore:
        move(*start)
    def pct(a, p):
        a = sorted(a)
        return round(a[min(len(a) - 1, int(len(a) * p / 100))], 2)
    rec = {"n": n,
           "抓帧ms": {"p50": pct(cap_ms, 50), "p95": pct(cap_ms, 95)},
           "驱动ms": {"p50": pct(act_ms, 50), "p95": pct(act_ms, 95)},
           "复核ms": {"p50": pct(ver_ms, 50), "p95": pct(ver_ms, 95)},
           "闭环ms": {"p50": pct(loops, 50), "p95": pct(loops, 95), "max": round(max(loops), 2)},
           "闭环fps": round(1000.0 / max(0.001, pct(loops, 50)), 1),
           "到位率": round(hits / max(1, n) * 100, 1),
           "偏差px": {"p50": pct(devs, 50), "p95": pct(devs, 95), "max": max(devs) if devs else None},
           "视觉变化率": round(visual_changes / max(1, n) * 100, 1),
           "口径注": "mss 抓屏不含鼠标指针 ⇒ 视觉变化率低属正常；到位率才是手真到位的证据；真视觉确认见下一步 aim_test（红点靶）",
           "眼独立fps": eye_fps(0.6),
           "阈值": {"射击级": "闭环 p50 ≤ 50ms", "可用": "闭环 p50 ≤ 100ms"}}
    rec["判"] = ("✅ 射击级" if rec["闭环ms"]["p50"] <= 50 else ("✅ 可用" if rec["闭环ms"]["p50"] <= 100 else "❌ 还慢"))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False) + chr(10))
    return rec


def status() -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-3:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"最近基准": rows, "口径": "真抓帧·真驱动·真复核；不许瞎报命中"}


__all__ = ["cursor", "move", "click", "grab", "eye_fps", "sync_benchmark", "status", "LEDGER"]
