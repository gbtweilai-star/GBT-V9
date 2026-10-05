# audio/queue.py —— 语音任务队列适配：委托本工程持久化队列（media/queue.py）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 对齐点：parity/operations.py 的 voice.synthetic_transcript 期望 (ledger, tts, ...)
# 本适配器把语音任务登记到本工程唯一的持久化队列（media.queue.JobQueue），
# 不引入第二套调度器；tts 只作为合成回调，不入库。
from media.queue import JobQueue, ensure_tables


class VoiceQueue:
    """语音队列：stage='voice' 的 JobQueue 视图 + 可选 TTS 回调"""

    def __init__(self, ledger, tts=None, *, lease_sec=300, **kw):
        ensure_tables(ledger)
        self.led, self.tts = ledger, tts
        self.q = JobQueue(ledger, lease_sec=lease_sec)

    def enqueue(self, project_id, text=None, *, priority=0, max_attempts=3,
                **params):
        p = dict(params or {})
        if text is not None:
            p["text"] = text
        return self.q.enqueue(project_id, "voice", "media.tts.gen", p,
                              priority=priority, max_attempts=max_attempts)

    def synthesize(self, text):
        """合成回调（假 TTS 在 parity/fakes.py 提供）；不可用如实抛错。"""
        if self.tts is None:
            raise RuntimeError("VoiceQueue 未注入 TTS 适配器")
        return self.tts.speak(text)

    def __getattr__(self, name):
        return getattr(self.q, name)
