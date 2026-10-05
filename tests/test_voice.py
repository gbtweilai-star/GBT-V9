# tests/test_voice.py —— VoiceAdapter：队列去重 / 背压丢弃 / 合成缓存 / 降级
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from tests.fakes import FakeBrain, FakeVoiceStudio


@pytest.fixture
def vs(monkeypatch):
    """把 senses.voice._post 换成假 HTTP，并隔离缓存目录"""
    import senses.voice as V
    fake = FakeVoiceStudio()
    monkeypatch.setattr(V, "_post", fake.post)
    monkeypatch.setattr(V, "CACHE_DIR", V.CACHE_DIR.__class__("/tmp/vs_cache_test"))
    return fake


# ═══ 队列去重 ═══
def test_tts_dedup_same_event_id(ledger, vs):
    """同一 event_id 只入队一次，重复计数"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    r1 = va.enqueue("告警来了", event_id="evt-1", priority=0)
    r2 = va.enqueue("告警来了", event_id="evt-1", priority=0)
    assert r1["queued"] is True
    assert r2["queued"] is False and r2["reason"] == "duplicate"
    assert va.stats["deduped"] == 1
    assert va.q.qsize() == 1                       # 队列里只有一条


def test_tts_dedup_by_episode(ledger, vs):
    """say_alert 用 episode 做去重键：同一 episode 不重复播报"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    alert = {"level": "critical", "label": "锁等待", "value": 3,
             "unit": "个", "episode": "ep-abc"}
    a = va.say_alert(alert)
    b = va.say_alert(alert)
    assert a["queued"] is True
    assert b["queued"] is False                    # 同 episode 去重
    assert va.stats["deduped"] == 1


def test_tts_summary_bucket_dedup(ledger, vs):
    """摘要用 5 分钟时间桶去重：同一桶内多次调用只播一次"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    stats = {"total_targets": 10, "aggregate": {"vuln": 2, "blocked": 1}}
    va.say_summary(stats)
    va.say_summary(stats)
    assert va.stats["deduped"] == 1
    assert va.q.qsize() == 1


# ═══ 背压：队列满丢低优先级，不阻塞 ═══
def test_tts_queue_full_drops_and_counts(ledger, vs):
    """队列打满时 enqueue 不抛，丢弃并计数"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain(), queue_size=2)
    assert va.enqueue("a", event_id="e1")["queued"] is True
    assert va.enqueue("b", event_id="e2")["queued"] is True
    r = va.enqueue("c", event_id="e3")             # 第3条溢出
    assert r["queued"] is False and r["reason"] == "queue_full"
    assert va.stats["dropped"] == 1


def test_tts_priority_order(ledger, vs):
    """PriorityQueue 按优先级出队：critical(0) 先于 summary(2)"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    va.enqueue("摘要", event_id="s1", priority=2)
    va.enqueue("严重告警", event_id="c1", priority=0)
    va.enqueue("普通", event_id="n1", priority=1)
    order = [va.q.get()[2].text for _ in range(3)]
    assert order == ["严重告警", "普通", "摘要"]


# ═══ 合成缓存 ═══
def test_tts_synthesize_cache_hit(ledger, vs, tmp_path, monkeypatch):
    """同文本+音色二次合成走缓存，不再发 HTTP"""
    import senses.voice as V
    from senses.voice import VoiceAdapter
    monkeypatch.setattr(V, "CACHE_DIR", tmp_path)   # 用真临时目录
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    f1 = va.synthesize("你好世界", voice="default", fmt="mp3")
    n1 = len(vs.calls)
    f2 = va.synthesize("你好世界", voice="default", fmt="mp3")
    assert f1 == f2                                 # 同一缓存文件
    assert len(vs.calls) == n1                      # 第二次没发请求
    assert f1.read_bytes() == b"FAKEAUDIO"


def test_tts_synthesize_failure_propagates(ledger, vs, tmp_path, monkeypatch):
    """VoiceStudio 挂了 → 抛异常，由队列消费端记账（不吞）"""
    import senses.voice as V
    from senses.voice import VoiceAdapter
    monkeypatch.setattr(V, "CACHE_DIR", tmp_path)
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    vs.fail_next = 1
    with pytest.raises(RuntimeError):
        va.synthesize("会失败", voice="default")


# ═══ 便捷封装 ═══
def test_tts_say_verdict_only_for_attention(ledger, vs):
    """大脑裁决播报：abort/fix 才回执，continue 不打扰"""
    from senses.voice import VoiceAdapter
    va = VoiceAdapter(ledger=ledger, brain=FakeBrain())
    va.say_verdict({"verdict": "危险", "hint": "停止", "cmd": "abort"}, "a.py")
    assert va.q.qsize() == 1
    # 验证内容包含裁决信息
    _, _, job = va.q.get()
    assert "危险" in job.text
