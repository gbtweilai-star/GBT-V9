# tests/test_mic.py —— MicCapture：关键词冷却 / 丢段计数 / 转写落库 / 隐私
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time
import pytest
from tests.fakes import FakeBrain, FakeVoiceStudio, FakeVoice


@pytest.fixture
def mic_env(monkeypatch, tmp_path):
    """装好假 sounddevice + 假 VoiceStudio，隔离音频目录"""
    monkeypatch.setenv("MIC_AUDIO_DIR", str(tmp_path / "mic"))   # 留档不污染工程目录
    import senses.mic as M
    import senses.voice as V

    class FakeSD:
        class InputStream:
            def __init__(self, **kw): self.kw = kw
            def start(self): pass
            def stop(self): pass
            def close(self): pass
        @staticmethod
        def query_devices(kind=None): return {"name": "FakeMic"}
    monkeypatch.setattr(M, "sd", FakeSD)

    fake_http = FakeVoiceStudio(text="紧急情况请立即处理")
    monkeypatch.setattr(V, "_post", fake_http.post)
    monkeypatch.setattr(M, "sd", FakeSD)
    # MicCapture._process 内部 `from senses.voice import _post` → 也已被替换
    return fake_http


def _seg(seq=0, ms=1000):
    """造一个假音频段：16kHz 单声道 int16"""
    from senses.mic import Segment, SAMPLE_RATE
    pcm = b"\x00\x00" * (SAMPLE_RATE * ms // 1000)
    now = time.time()
    return Segment(seq=seq, start_ts=now - ms / 1000, end_ts=now, pcm=pcm)


# ═══ 关键词匹配（纯函数，先验基础）═══
def test_keyword_normalize_and_match():
    from senses.mic import normalize, match_keywords, DEFAULT_KEYWORDS
    assert normalize("紧急！") == "紧急"                 # 去标点
    assert normalize("ＥＲＲＯＲ") == "error"           # NFKC + 折叠
    hits = match_keywords("这里发生了紧急情况", DEFAULT_KEYWORDS)
    assert any(h["keyword"] == "紧急" and h["level"] == "critical" for h in hits)
    assert match_keywords("一切正常", DEFAULT_KEYWORDS) == []


# ═══ 转写落库 ═══
def test_mic_process_writes_transcript(ledger, mic_env):
    from senses.mic import MicCapture
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain())
    seg = _seg(seq=0)
    mic._process(seg)
    rows = ledger.conn.execute(
        "SELECT seq,text,status,keywords FROM mic_segments").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 0
    assert rows[0][1] == "紧急情况请立即处理"
    assert rows[0][2] == "done"
    assert "紧急" in (rows[0][3] or "")                 # 关键词被记录


def test_mic_transcribe_failure_recorded(ledger, mic_env):
    """ASR 失败 → 记 failed + 原因，不影响其他段"""
    from senses.mic import MicCapture
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain())
    mic_env.fail_next = 1
    mic._process(_seg(seq=0))
    r = ledger.conn.execute(
        "SELECT status,error FROM mic_segments WHERE seq=0").fetchone()
    assert r[0] == "failed" and "VoiceStudio" in r[1]
    assert mic.stats["failed"] == 1


# ═══ 关键词冷却 ═══
def test_mic_keyword_cooldown(ledger, mic_env):
    """同一关键词 30 秒内只触发一次"""
    from senses.mic import MicCapture
    brain = FakeBrain()
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=brain)
    hits = [{"keyword": "紧急", "level": "critical", "action": "alert"}]
    seg = _seg(seq=0)
    mic._trigger(seg, "紧急情况", hits)
    assert mic.stats["keywords"] == 1
    assert len(brain.asks) == 1                          # 上报了大脑
    mic._trigger(seg, "又紧急了", hits)                  # 冷却期内
    assert mic.stats["keywords"] == 1                    # 没再触发
    assert len(brain.asks) == 1
    # 手动把冷却时间拨回过去，应可再次触发
    mic._cooldown["紧急"] = time.time() - 31
    mic._trigger(seg, "第三次紧急", hits)
    assert mic.stats["keywords"] == 2


def test_mic_keyword_critical_sends_voice_receipt(ledger, mic_env):
    """critical 关键词额外触发语音回执"""
    from senses.mic import MicCapture
    voice = FakeVoice()
    mic = MicCapture("t1-ear", ledger, voice, brain=FakeBrain())
    mic._trigger(_seg(seq=1), "紧急",
                 [{"keyword": "紧急", "level": "critical", "action": "alert"}])
    assert voice.enqueued                                # 有回执
    assert voice.enqueued[0]["priority"] == 0            # critical 最高优先级
    assert "紧急" in voice.enqueued[0]["text"]


def test_mic_keyword_callback_fired(ledger, mic_env):
    """on_keyword 回调被调用（面板通知钩子）"""
    from senses.mic import MicCapture
    fired = []
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain(),
                     on_keyword=lambda seg, text, hits: fired.append((seg.seq, hits)))
    mic._trigger(_seg(seq=7), "紧急",
                 [{"keyword": "紧急", "level": "critical", "action": "alert"}])
    assert fired and fired[0][0] == 7


# ═══ 丢段计数（背压）═══
def test_mic_queue_full_counts_drop(ledger, mic_env):
    """队列打满 → _emit 记 dropped，不抛异常"""
    from senses.mic import MicCapture
    import numpy as np
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain(), queue_size=2)
    frames = [np.zeros(320, dtype=np.int16) for _ in range(3)]
    mic._emit(frames)                                     # 第1条入队
    mic._emit(frames)                                     # 第2条入队
    mic._emit(frames)                                     # 第3条溢出
    assert mic.stats["segments"] == 2
    assert mic.stats["dropped"] == 1
    # 过载被记进 mic_events
    ev = ledger.conn.execute(
        "SELECT kind FROM mic_events WHERE kind='dropped'").fetchall()
    assert ev


def test_mic_seq_monotonic(ledger, mic_env):
    """每段 seq 单调递增，无重复"""
    from senses.mic import MicCapture
    import numpy as np
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain(), queue_size=10)
    frames = [np.zeros(320, dtype=np.int16)]
    for _ in range(5):
        mic._emit(frames)
    # 队列元素就是 Segment 对象（见 senses/mic._emit）
    got = [mic.q.get().seq for _ in range(5)]
    assert got == [0, 1, 2, 3, 4]                          # seq 单调递增无重复
    assert mic._seq == 5


# ═══ 隐私：默认不留音频 ═══
def test_mic_drops_temp_audio_by_default(ledger, mic_env, tmp_path, monkeypatch):
    """默认不保留原始音频：处理完临时文件即删"""
    import senses.mic as M
    from senses.mic import MicCapture
    monkeypatch.setattr(M, "Path", __import__("pathlib").Path)   # 保持真实 Path
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain())
    assert mic.keep_audio is False
    mic._process(_seg(seq=0))
    # 目录不存在或为空
    assert not mic._audio_dir.exists() or not list(mic._audio_dir.glob("*.wav"))


def test_mic_keeps_audio_when_enabled(ledger, mic_env):
    """显式开启 keep_audio 才留档"""
    from senses.mic import MicCapture
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain(),
                     keep_audio=True)
    assert mic.keep_audio is True
    mic._process(_seg(seq=0))
    assert list(mic._audio_dir.glob("*.wav"))             # 留了档


# ═══ 检索 ═══
def test_mic_search_by_keyword(ledger, mic_env):
    from senses.mic import MicCapture
    mic = MicCapture("t1-ear", ledger, FakeVoice(), brain=FakeBrain())
    mic._process(_seg(seq=0))
    hits = mic.search("紧急")
    assert hits and hits[0]["seq"] == 0
    assert mic.search("不存在的词") == []
