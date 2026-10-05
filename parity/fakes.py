# parity/fakes.py —— 确定性 Fake（只替换外部依赖，不伪造读数结论）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: Fake 记录"发生了什么"，由 probe 从账本/文件独立核验；
#       不抛随机性、不依赖时钟漂移；行为可预测、可断言

from __future__ import annotations
import asyncio, hashlib, json
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────
# ① FakeBrain：先错后对（coder.fake_fix_loop 用）
#    对齐点: 你的 Brain 调用接口——如果是 `await brain.complete(prompt)` 或
#    `await brain.ask(messages)`，改下面的 decide() 一个方法即可
# ─────────────────────────────────────────────
@dataclass
class FakeBrainCall:
    kind: str          # "plan" | "fix" | "route" | ...
    prompt: str
    attempt_no: int


class FakeBrainFirstFailThenPass:
    """第一次给错答案（触发 fix_loop 重试），第二次给对。全程记录。"""

    def __init__(self, *, task_desc: str = "fix failing test"):
        self.task_desc = task_desc
        self.calls: list[FakeBrainCall] = []
        self._fix_attempts = 0

    async def decide(self, kind: str, prompt: str, **kw) -> dict:
        """← 对齐点：改成你 brain/decision.router 的真实方法签名。"""
        self.calls.append(FakeBrainCall(kind=kind, prompt=prompt,
                                        attempt_no=self._fix_attempts + 1))
        if kind == "fix":
            self._fix_attempts += 1
            if self._fix_attempts == 1:
                return {"ok": False, "patch": "BROKEN",          # 第一次故意错
                        "reason": "fake_first_fail"}
            return {"ok": True, "patch": "FIXED", "reason": None}
        if kind == "plan":
            return {"ok": True,
                    "steps": [{"step": 1, "action": "edit", "target": "a.py"}]}
        return {"ok": True}

    # 兼容三种常见接口形态，任选其一与项目对齐：
    async def complete(self, prompt: str, **kw) -> str:      # ← 对齐点 A
        r = await self.decide("fix", prompt, **kw)
        return r.get("patch", "")

    async def ask(self, messages: list[dict], **kw) -> dict:  # ← 对齐点 B
        return await self.decide(
            "fix" if kw.get("kind") == "fix" else "plan",
            json.dumps(messages, ensure_ascii=False), **kw)

    @property
    def fix_attempt_count(self) -> int:
        return self._fix_attempts


# ─────────────────────────────────────────────
# ② FakeTTS：确定性音频 bytes（voice 读数用）
#    对齐点: 你的 TTS 适配器接口
# ─────────────────────────────────────────────
class FakeTTS:
    """同样输入 → 同样 bytes；供 artifact/SHA-256 断言。"""

    def __init__(self):
        self.spoken: list[str] = []

    async def speak(self, text: str) -> bytes:
        self.spoken.append(text)
        return hashlib.sha256(text.encode("utf-8")).digest()   # 确定性 32 bytes


# ─────────────────────────────────────────────
# ③ FakePyAutoGUI：只记录，绝不碰真实桌面
#    对齐点: actuator 里 import pyautogui 的位置——改成注入
# ─────────────────────────────────────────────
@dataclass
class FakeAction:
    kind: str                       # click | type | key | move
    args: tuple
    kwargs: dict


class FakePyAutoGUI:
    def __init__(self):
        self.actions: list[FakeAction] = []
        self.screen_size = (1920, 1080)

    def click(self, x=None, y=None, **kw):
        self.actions.append(FakeAction("click", (x, y), kw))

    def typewrite(self, text: str, **kw):
        self.actions.append(FakeAction("type", (text,), kw))

    typewrite.__doc__ = "alias: write"
    write = typewrite

    def press(self, key: str, **kw):
        self.actions.append(FakeAction("key", (key,), kw))

    def moveTo(self, x, y, **kw):
        self.actions.append(FakeAction("move", (x, y), kw))

    def size(self):
        return self.screen_size

    def sequence(self) -> list[tuple[str, tuple]]:
        return [(a.kind, a.args) for a in self.actions]


# ─────────────────────────────────────────────
# ④ FakeGPUMutex：真异步锁 + 互斥记录
#    对齐点: sched/queue.py 的 GPU 互斥接口
# ─────────────────────────────────────────────
class FakeGPUMutex:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.events: list[tuple[str, int]] = []      # (action, run_id)
        self._current: int | None = None
        self.max_concurrent = 0
        self._active = 0

    def for_run(self, run_id: int):
        return _MutexHandle(self, run_id)


class _MutexHandle:
    def __init__(self, m: FakeGPUMutex, run_id: int):
        self._m, self._run_id = m, run_id

    async def __aenter__(self):
        await self._m._lock.acquire()
        self._m._active += 1
        self._m.max_concurrent = max(self._m.max_concurrent, self._m._active)
        self._m.events.append(("acquire", self._run_id))
        self._m._current = self._run_id
        return self

    async def __aexit__(self, *exc):
        self._m._active -= 1
        self._m.events.append(("release", self._run_id))
        self._m._current = None
        self._m._lock.release()
        return False


# ─────────────────────────────────────────────
# ⑤ 合成转写段（voice.synthetic_transcript 用）
#    对齐点: VoiceAdapter 的输入段类型
# ─────────────────────────────────────────────
@dataclass
class SyntheticTranscript:
    text: str
    seq: int
    dropped: bool = False      # 丢段计数：True 表示模拟 upstream 丢了一段

    def as_dict(self) -> dict:
        return {"text": self.text, "seq": self.seq, "dropped": self.dropped}


def synthetic_transcripts(script: list[str]) -> list[SyntheticTranscript]:
    """seq 连续，可插入 dropped=True 模拟丢段。"""
    return [SyntheticTranscript(text=t, seq=i) for i, t in enumerate(script)]
