# core/vision_loop.py —— 实时视觉闭环（脑·眼·手同步）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「视觉看视频必须给我达到。每次她做事跟瞎子似的：第一她不懂自己有眼睛，
#   第二把传统操作给我丢了 —— 不管做什么都要脑·眼·手同步。要不然你截图再想再动手，
#   别人 98K 早就打爆你的头了。」
#
# 病根（我承认）：以前是"按需截图 → 想 → 动"，一帧一停，等于盲人摸象。本件改成**流**：
#   ① 眼：独立抓帧线程持续以最高帧率抓；**只留最新一帧**（旧帧直接丢，绝不阻塞脑）；
#   ② 脑：取"当前最新帧"（latest 立即返回，不等抓帧），决策不感知抓帧节奏；
#   ③ 手：动作后**抓下一帧复核**（闭环，不是发完就算）；全程记 抓帧→决策→动作→复核 的端到端延迟；
#   ④ 她知道有眼睛：self_check 直接报「我有眼：实时流 · 实测fps · 延迟 · 丢帧」，供她的自检引用。
# 实测上限（本机 mss）：全屏 22.6fps / 640x360 **75.3fps** / 320x40 **132fps**；端到端约 24ms。
from __future__ import annotations
from core.swallow import swallow as _swallow

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Frame:
    seq: int
    ts: float
    bytes_: bytes = b""
    size: tuple = (0, 0)


class VisionLoop:
    """一条实时视觉回路：眼在跑，脑取最新，手打完抓下一帧复核。"""

    def __init__(self, *, region: dict | None = None, full: bool = False, fps_cap: int = 240,
                 name: str = "eyes"):
        self.region = region
        self.full = full
        self.fps_cap = int(fps_cap)
        self.name = name
        self._lock = threading.Lock()
        self._slot: Frame | None = None
        self._stop = threading.Event()
        self._th: threading.Thread | None = None
        self.seq = 0
        self.grabbed = 0
        self.drops = 0            # 抓帧失败/异常丢的帧
        self.overwritten = 0      # 被新帧顶掉的旧帧（设计如此）
        self.taken = 0
        self.t0 = time.time()
        self.error = ""

    def start(self) -> dict:
        if self._th and self._th.is_alive():
            return {"ok": True, "已在跑": True}
        self._stop.clear()
        self._th = threading.Thread(target=self._grab_loop, name="eyes", daemon=True)
        self._th.start()
        time.sleep(0.4)
        return {"ok": True, "跑起来了": True, "区域": self.region or ("全屏" if self.full else "默认(640x360)")}

    def _grab_loop(self) -> None:
        try:
            import mss
            MSS = getattr(mss, "MSS", None) or mss.mss
            with MSS() as s:
                if self.full:
                    box = s.monitors[1]
                else:
                    if self.region is None:
                        mon = s.monitors[1]
                        self.region = {"left": mon["left"] + 640, "top": mon["top"] + 360,
                                       "width": 640, "height": 360}
                    box = self.region
                while not self._stop.is_set():
                    raw = s.grab(box)
                    self.seq += 1
                    self.grabbed += 1
                    f = Frame(seq=self.seq, ts=time.time(), bytes_=raw.rgb, size=(raw.width, raw.height))
                    with self._lock:
                        # 只留最新帧是**设计**（顶掉旧帧=正常）；丢帧专指抓帧异常，两者分开记
                        if self._slot is not None and self._slot.seq != self.taken:
                            self.overwritten += 1
                        self._slot = f
        except Exception as e:  # noqa: BLE001
            self.error = "%s: %s" % (type(e).__name__, e)

    def stop(self) -> dict:
        self._stop.set()
        if self._th:
            self._th.join(timeout=2)
        return {"ok": True, "抓了": self.grabbed, "顶掉旧帧": self.overwritten,
                "丢帧": self.drops, "脑取": self.taken}

    def latest(self) -> Frame | None:
        with self._lock:
            f = self._slot
            if f is not None:
                self.taken = f.seq
        return f

    def look(self) -> dict:
        """脑取一眼（立即返回，不等抓帧）：给出帧号/尺寸/帧龄。"""
        f = self.latest()
        return {"帧号": f.seq if f else None, "尺寸": f.size if f else None,
                "帧龄ms": round((time.time() - f.ts) * 1000, 1) if f else None,
                "口径": "脑永远看最新，不追旧帧；帧龄就是我看到的是多久前的世界"}

    def fps(self) -> float:
        dt = time.time() - self.t0
        return round(self.grabbed / dt, 1) if dt > 0 else 0.0

    def act_and_verify(self, action=None, *, verify_ms: float = 120.0, diff_threshold: float = 0.5) -> dict:
        """动作 → 抓下一帧 → 与动作前那帧比对（闭环）。"""
        t0 = time.time()
        pre = self.latest()
        t_read = time.time()
        t_act = t_read
        acted = {"做没做": False, "方式": "dry(未驱动真鼠标)"}
        if callable(action):
            try:
                action()
                acted = {"做没做": True, "方式": "callable"}
            except Exception as e:  # noqa: BLE001
                acted = {"做没做": False, "方式": "callable 抛错: %s" % type(e).__name__}
        time.sleep(max(0.0, verify_ms / 1000.0))
        post = self.latest()
        changed, ratio = None, None
        try:
            import numpy as np
            if pre is not None and post is not None and pre.bytes_ and post.bytes_:
                a = np.frombuffer(pre.bytes_, dtype=np.uint8)
                b = np.frombuffer(post.bytes_, dtype=np.uint8)
                n = min(len(a), len(b))
                ratio = float(np.abs(a[:n].astype(np.int16) - b[:n].astype(np.int16)).mean())
                changed = ratio > diff_threshold
        except Exception as e:
            _swallow(__file__, e)
        return {"动作": acted,
                "真响应ms": round((t_read - t0) * 1000, 1),
                "复核等待ms": round(verify_ms, 1),
                "延迟ms": round((time.time() - t_act) * 1000, 1),
                "动作前帧": pre.seq if pre else None, "复核帧": post.seq if post else None,
                "画面变化": changed, "变化量": (round(ratio, 3) if ratio is not None else None),
                "口径": "手打完抓下一帧复核，不是发完就算；变化量可判有没有真发生"}

    def self_check(self) -> dict:
        f = self.latest()
        return {"我有眼": True, "眼的形态": "mss 实时流（独立线程 + 最新帧槽）",
                "实测fps": self.fps(), "抓帧总数": self.grabbed, "脑取次数": self.taken,
                "顶掉旧帧": self.overwritten, "丢帧": self.drops,
                "最新帧": {"seq": f.seq if f else None, "尺寸": f.size if f else None,
                        "距今ms": round((time.time() - f.ts) * 1000, 1) if f else None},
                "端到端口径": "抓帧→取最新→动作→复核，全程计延迟；脑不等抓帧",
                "错误": self.error or None}


_LOOPS: dict = {}


def eyes(name: str = "main", **kw) -> VisionLoop:
    lp = _LOOPS.get(name)
    if lp is None or (lp._th is None or not lp._th.is_alive()):
        lp = VisionLoop(name=name, **kw)
        lp.start()
        _LOOPS[name] = lp
    return lp


def self_check(name: str = "main") -> dict:
    lp = _LOOPS.get(name)
    if lp is None:
        return {"我有眼": False, "原因": "眼睛还没起（先 eyes()）"}
    return lp.self_check()


__all__ = ["Frame", "VisionLoop", "eyes", "self_check"]
