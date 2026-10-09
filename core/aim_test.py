# core/aim_test.py —— 真视觉确认：绿点靶（Tk 窗）→ 眼视觉搜索 → 手打中 → 靶变红 = 命中
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令：视觉要打磨到射击游戏级 —— 不只要"眼快手准"，还要**打中确认**（真视觉证据链）。
# 用法：
#   python core/aim_test.py --target          # 靶窗（子进程跑）：绿点随机移动，被点中变红
#   python core/aim_test.py --trials 15       # 实测：眼找绿点 → 手移动+点击 → 复抓看红 = 命中
import argparse, json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))     # ★ 修：当脚本直接跑时（python core/aim_test.py）也能 import core
LEDGER = ROOT / "state" / "aim_test.jsonl"

RADIUS = 46


def run_target(seed_seconds: int = 90) -> None:
    """靶：一个置顶小窗画绿点；被点中变红 0.5s 后再换位置变绿。"""
    import random, tkinter as tk
    top = tk.Tk()
    top.title("GBT-target")
    top.attributes("-topmost", True)
    top.overrideredirect(True)
    size = RADIUS * 2 + 24
    canvas = tk.Canvas(top, width=size, height=size, bg="black", highlightthickness=0)
    canvas.pack()
    state = {"cx": size // 2, "cy": size // 2, "hit": False}

    def draw() -> None:
        canvas.delete("all")
        color = "#ff2020" if state["hit"] else "#00ff44"
        canvas.create_oval(state["cx"] - RADIUS, state["cy"] - RADIUS, state["cx"] + RADIUS, state["cy"] + RADIUS, fill=color)

    def place() -> None:
        sw, sh = top.winfo_screenwidth(), top.winfo_screenheight()
        x = random.randint(60, max(61, sw - size - 60))
        y = random.randint(60, max(61, sh - size - 60))
        top.geometry("%dx%d+%d+%d" % (size, size, x, y))
        state["hit"] = False
        draw()

    def on_click(_e) -> None:
        state["hit"] = True
        draw()
        top.after(500, place)

    canvas.bind("<Button-1>", on_click)
    top.bind("<Button-1>", on_click)
    place()
    end = time.time() + seed_seconds
    def tick() -> None:
        if time.time() > end:
            top.destroy(); return
        top.after(250, tick)
    tick()
    top.mainloop()


def _sct():
    import mss
    return mss.mss()


def find_color(img, want: str, tol: int = 60):
    """在帧里找目标色的质心（不做 API 取值，纯视觉搜索）。"""
    raw = bytes(getattr(img, "raw", b"")) if hasattr(img, "raw") else None
    if raw:
        import mss.tools
        bgra = img.bgra if hasattr(img, "bgra") else img.raw
    else:
        bgra = img.bgra
    w, h = img.width, img.height
    tgt = {"green": (0x00, 0xFF, 0x44), "red": (0xFF, 0x20, 0x20)}[want]
    sx = sy = n = 0
    step = 2
    for y in range(0, h, step):
        row = y * w * 4
        for x in range(0, w, step):
            i = row + x * 4
            b, g, r = bgra[i], bgra[i + 1], bgra[i + 2]
            if abs(r - tgt[0]) < tol and abs(g - tgt[1]) < tol and abs(b - tgt[2]) < tol:
                sx += x; sy += y; n += 1
    return {"x": sx / n, "y": sy / n, "像素": n} if n else None


def trials(n: int = 15) -> dict:
    from core import eye_hand as EH
    sct = _sct()
    mon = sct.monitors[0]
    region = {"left": 0, "top": 0, "width": mon["width"], "height": mon["height"]}
    hits, miss = 0, 0
    find_ms, act_ms, verify_ms, loop_ms = [], [], [], []
    for _ in range(n):
        t0 = time.time()
        img = sct.grab(region)
        t1 = time.time()
        g = find_color(img, "green")
        if not g:
            time.sleep(0.4)
            miss += 1
            continue
        EH.move(int(g["x"]), int(g["y"]))
        EH.click()
        t2 = time.time()
        time.sleep(0.12)
        img2 = sct.grab(region)
        t3 = time.time()
        r = find_color(img2, "red")
        ok = bool(r)
        hits += 1 if ok else 0
        miss += 0 if ok else 1
        find_ms.append((t1 - t0) * 1000); act_ms.append((t2 - t1) * 1000)
        verify_ms.append((t3 - t2) * 1000); loop_ms.append((t3 - t0) * 1000)
        time.sleep(0.25)

    def pct(a, p):
        a = sorted(a)
        return round(a[min(len(a) - 1, int(len(a) * p / 100))], 2) if a else None

    rec = {"n": n, "命中": hits, "未命中": miss, "命中率": round(hits / max(1, n) * 100, 1),
           "眼找目标ms": {"p50": pct(find_ms, 50), "p95": pct(find_ms, 95)},
           "手移动+点击ms": {"p50": pct(act_ms, 50), "p95": pct(act_ms, 95)},
           "复抓确认ms": {"p50": pct(verify_ms, 50), "p95": pct(verify_ms, 95)},
           "总闭环ms": {"p50": pct(loop_ms, 50), "p95": pct(loop_ms, 95)},
           "口径": "眼纯视觉搜索绿点质心 → 手 Win32 移动+左键 → 复抓看变红 = 命中；不取任何 API 坐标"}
    rec["判"] = ("✅ 射击级（命中率≥90% 且 p50≤80ms）" if rec["命中率"] >= 90 and (rec["总闭环ms"]["p50"] or 999) <= 80 else "❌ 未达标")
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **rec}, ensure_ascii=False) + chr(10))
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", action="store_true")
    ap.add_argument("--trials", type=int, default=15)
    a = ap.parse_args()
    if a.target:
        run_target(); return 0
    tgt = subprocess.Popen([sys.executable, str(Path(__file__)), "--target"])
    time.sleep(2.2)
    try:
        r = trials(a.trials)
        print("=== 真视觉确认（绿点靶）===")
        for k in ("命中率", "眼找目标ms", "手移动+点击ms", "复抓确认ms", "总闭环ms"):
            print("  %-14s %s" % (k, json.dumps(r[k], ensure_ascii=False) if isinstance(r[k], dict) else str(r[k]) + ("%" if k == "命中率" else "")))
        print("  判:", r["判"])
    finally:
        try:
            tgt.terminate()
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
