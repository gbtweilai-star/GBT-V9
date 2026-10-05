# tests/fakes.py —— 假大脑 / 假屏幕 / 假鼠标键盘
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import numpy as np

class FakeBrain:
    """确定性大脑：按 system 提示词关键词返回脚本化结果"""
    def __init__(self, plan=None, fix=None, action=None):
        self._plan = plan or {"plan": ["写文件"], "files": [], "tests": "", "risks": []}
        self._fix = fix or {"diagnosis": "", "patch": [], "explain": ""}
        self._action = action or {"done": True}
        self.calls, self.asks = [], []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        sys_txt = messages[0]["content"] if messages else ""
        if "软件工程师" in sys_txt and "修复" not in sys_txt:
            return dict(self._plan)
        if "修复" in sys_txt:
            return dict(self._fix)
        if "操作规划器" in sys_txt:
            return dict(self._action)
        return {}

    def ask(self, tid, target, detail):
        self.asks.append((tid, target, detail))
        return {"verdict": "ok", "cmd": "continue", "hint": ""}

    def god_view(self): return {}

class FakeDevourer:
    """假吞噬器：_grab 返回合成帧，不碰真实屏幕"""
    def __init__(self, h=60, w=80):
        self.h, self.w = h, w
        self.frames = 0
    def _grab(self):
        self.frames += 1
        return np.full((self.h, self.w, 3), self.frames % 255, dtype=np.uint8)

class FakePyAutoGUI:
    """假鼠标键盘：记录动作，不做真实操作"""
    FAILSAFE = True
    def __init__(self): self.actions = []
    def click(self, *a, **k): self.actions.append(("click", a, k))
    def typewrite(self, *a, **k): self.actions.append(("type", a, k))
    def hotkey(self, *a, **k): self.actions.append(("hotkey", a, k))
    def scroll(self, *a, **k): self.actions.append(("scroll", a, k))
    def moveTo(self, *a, **k): self.actions.append(("moveTo", a, k))
    def dragTo(self, *a, **k): self.actions.append(("dragTo", a, k))


# ── 语音假件（tests/test_voice.py / test_mic.py 用）──
class FakeVoiceStudio:
    """假 VoiceStudio HTTP：记录请求，返回可控响应"""
    def __init__(self, audio=b"FAKEAUDIO", text="转写结果"):
        self.audio, self.text = audio, text
        self.calls = []
        self.fail_next = 0

    def post(self, path, data=None, files=None, timeout=120, raw=False):
        self.calls.append({"path": path, "data": data, "raw": raw})
        if self.fail_next > 0:
            self.fail_next -= 1
            raise RuntimeError("VoiceStudio 不可达: simulated")
        if raw:
            return self.audio
        return {"text": self.text}


class FakeVoice:
    """假 TTS 适配器：记录入队，不做真实合成"""
    def __init__(self):
        self.timeout = 5
        self.enqueued = []

    def enqueue(self, text, event_id=None, priority=1, voice="default"):
        self.enqueued.append({"text": text, "event_id": event_id,
                              "priority": priority})
        return {"queued": True, "event_id": event_id}
