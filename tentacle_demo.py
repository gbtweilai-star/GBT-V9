"""最小触手：截图 → 差分钩子 → LLM 抽帧 → 键鼠执行 → 验证"""
import time, io, json, hashlib
import mss, numpy as np
from PIL import Image
import pyautogui

pyautogui.FAILSAFE = True  # 鼠标甩到左上角可紧急中止

# ── 契约（对应上面的 TentacleContract）──
CONTRACT = {
    "id": "t1-desktop-eye",
    "limits": {"frameRate": 2, "timeoutMs": 5000},
    "hooks": [{"type": "region_diff", "params": {"threshold": 0.03},
               "action": "capture_frame", "cooldownMs": 2000}],
}

class Tentacle:
    def __init__(self, contract):
        self.c = contract
        self.last_hash, self.last_fire = None, 0

    # ── 眼睛：capture ──
    def capture(self, region=None):
        with mss.mss() as s:
            mon = region or s.monitors[1]
            shot = s.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return img

    # ── 钩子：region_diff，只钩有用的帧 ──
    def hook(self, frame) -> bool:
        now = time.time()
        if now - self.last_fire < self.c["hooks"][0]["cooldownMs"] / 1000:
            return False
        h = hashlib.md5(np.array(frame.resize((64, 36))).tobytes()).hexdigest()
        if h == self.last_hash:
            return False                     # 没变化 → 丢弃，不进 LLM
        self.last_hash, self.last_fire = h, now
        return True

    # ── 手：act（坐标兜底路径）──
    def act(self, x, y, click="left"):
        pyautogui.click(x, y, button=click)

    # ── 验证：act 后回读确认 ──
    def verify(self, before_hash) -> bool:
        return hashlib.md5(np.array(self.capture().resize((64, 36))).tobytes()).hexdigest() != before_hash

# ── 主循环：观察 → 钩子过滤 → （此处接 LLM）→ 执行 ──
if __name__ == "__main__":
    t = Tentacle(CONTRACT)
    print("触手 t1 已连接，按 Ctrl+C 停止…")
    while True:
        frame = t.capture()
        if t.hook(frame):                    # 只有变化的帧才会走到这里
            frame.save(f"frames/{int(time.time()*1000)}.png")
            # TODO: 在这里把 frame 交给触手配置的 VLM，
            #       拿回 {x, y, action} 再调 t.act() + t.verify()
            print(f"[hook] 检测到画面变化，帧已存档")
        time.sleep(1 / CONTRACT["limits"]["frameRate"])
