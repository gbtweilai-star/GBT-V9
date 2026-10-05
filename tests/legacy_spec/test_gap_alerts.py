# tests/test_gap_alerts.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.alerts import AlertManager
from senses.gap_alerts import GapAlertBridge, GapEvent


class FakeVoice:
    def __init__(self): self.said = []
    def say_alert(self, a): self.said.append(a); return {"queued": True}


@pytest.fixture
def bridge(ledger):
    am = AlertManager(ledger)
    v = FakeVoice()
    b = GapAlertBridge(ledger, am, voice=v, poll_interval=0.05)
    return b, am, v


def test_gap_fires_critical_once_per_tentacle(bridge, ledger):
    b, am, v = bridge
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time()))
    b.on_gap_committed(GapEvent("g2","t1",200,200,3,time.time()))
    b._drain_once()
    # 同一触手两条 gap → 只播一次
    assert len(v.said) == 1
    assert v.said[0]["level"] == "critical"
    # 累计缺帧 = 1 + 3
    assert v.said[0]["value"] == 4


def test_gap_idempotent(bridge):
    b, am, v = bridge
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time()))
    b._drain_once()
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time()))  # 重复
    b._drain_once()
    assert len(v.said) == 1                     # 不重复播
    assert b._drain_once() == 0                 # outbox 已空


def test_no_recover_on_timeout(bridge, ledger):
    b, am, v = bridge
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time()))
    b._drain_once()
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM alert_state WHERE alert_key='devour.gap:t1'")
        assert cur.fetchone()[0] == "active"     # 不会自己恢复


def test_recover_only_on_verified_segment(bridge, ledger):
    b, am, v = bridge
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time()))
    b._drain_once()
    res = b.on_verified_clean_segment("t1", trace_id="tr1", segment_id="s9")
    assert res.transition == "recovered"
    with ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT state FROM alert_state WHERE alert_key='devour.gap:t1'")
        assert cur.fetchone()[0] == "normal"


def test_new_episode_can_fire_again(bridge):
    b, am, v = bridge
    b.on_gap_committed(GapEvent("g1","t1",100,100,1,time.time())); b._drain_once()
    b.on_verified_clean_segment("t1")
    b.on_gap_committed(GapEvent("g2","t1",300,300,2,time.time())); b._drain_once()
    assert len(v.said) == 2                     # 新 episode 可再播
