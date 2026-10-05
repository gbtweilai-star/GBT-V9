# panel/talk.py —— 数字人语音对讲后端（ASR → 主脑 → TTS）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 链路: 浏览器麦克风分段 → ASR 转写 → 字幕上屏 → 主脑 → TTS 回放
# 约束: 麦克风需 localhost 或 HTTPS + 用户授权; 不落原始音频
import asyncio, json, base64
from fastapi import WebSocket, WebSocketDisconnect

class TalkSession:
    def __init__(self, brain, voice, ledger, tid="talk"):
        self.brain, self.voice, self.led, self.tid = brain, voice, ledger, tid

    async def handle(self, ws: WebSocket):
        await ws.accept()
        await ws.send_json({"type": "state", "state": "idle"})
        try:
            while True:
                msg = await ws.receive_json()
                if msg["type"] == "audio":                    # base64 wav 分段
                    await ws.send_json({"type": "state", "state": "listening"})
                    text = await asyncio.to_thread(self._asr, msg["data"])
                    if not text:
                        await ws.send_json({"type": "state", "state": "idle"}); continue
                    await ws.send_json({"type": "subtitle", "who": "user", "text": text})
                    await ws.send_json({"type": "state", "state": "thinking"})
                    reply = await asyncio.to_thread(
                        self.brain.chat, [{"role": "user", "content": text}], None, 3, False)
                    await ws.send_json({"type": "subtitle", "who": "ai", "text": reply})
                    await ws.send_json({"type": "state", "state": "speaking"})
                    audio = await asyncio.to_thread(self._tts, reply)
                    await ws.send_json({"type": "speak", "audio": audio})
                    await ws.send_json({"type": "state", "state": "idle"})
                elif msg["type"] == "text":
                    reply = await asyncio.to_thread(
                        self.brain.chat, [{"role": "user", "content": msg["text"]}], None, 3, False)
                    await ws.send_json({"type": "subtitle", "who": "ai", "text": reply})
                    audio = await asyncio.to_thread(self._tts, reply)
                    await ws.send_json({"type": "speak", "audio": audio})
        except WebSocketDisconnect:
            pass

    def _asr(self, b64):
        from senses.voice import _post
        import tempfile, os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(base64.b64decode(b64)); p = f.name
        try:
            r = _post("/audio/transcriptions", files={"file": ("s.wav", open(p, "rb").read())})
            return (r or {}).get("text", "").strip()
        finally:
            os.unlink(p)

    def _tts(self, text):
        from senses.voice import _post
        try:
            data = _post("/audio/speech", {"input": text, "voice": "default"}, raw=True)
            return base64.b64encode(data).decode()
        except Exception:
            return ""
