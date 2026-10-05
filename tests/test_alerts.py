# tests/test_alerts.py —— 告警状态机：边沿触发/不刷屏/探测失败不误报恢复
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time


class Probe:
    """可脚本化的假探针"""
    def __init__(self):
        self.seq = []
        self.i = 0
    def push(self, **kw): self.seq.append(kw)
    def __call__(self):
        if self.i >= len(self.seq): return {"backend_ok": True}
        v = self.seq[self.i]; self.i += 1
        return v


def _events(ledger, transition):
    return ledger.conn.execute(
        "SELECT alert_key,level FROM alert_events WHERE transition=?",
        (transition,)).fetchall()


def test_alert_edge_triggered_not_respammed(ledger):
    """持续超标只写一条 fired，不是每 tick 一条"""
    from panel.alerts import AlertManager
    p = Probe()
    for v in (72, 91, 94, 96, 95, 93):               # 从 91 起一路超标
        p.push(backend_ok=True, pool_util=v, lock_waits=0)
    am = AlertManager(ledger, p, recover_confirm=1)
    for _ in range(6):
        am.tick()
    fired = [e for e in _events(ledger, "fired") if e[0] == "pool_util"]
    assert len(fired) == 1                           # 6 次探测，只 1 条日志
    assert fired[0][1] == "warning"


def test_alert_hysteresis_recovery(ledger):
    """恢复迟滞：91 触发 → 88 不恢复 → 84 才恢复"""
    from panel.alerts import AlertManager
    p = Probe()
    for v in (91, 88, 88, 84):
        p.push(backend_ok=True, pool_util=v, lock_waits=0)
    am = AlertManager(ledger, p, recover_confirm=1)
    for _ in range(4):
        am.tick()
    fired = [e for e in _events(ledger, "fired") if e[0] == "pool_util"]
    rec = [e for e in _events(ledger, "recovered") if e[0] == "pool_util"]
    assert len(fired) == 1
    assert len(rec) == 1                             # 只在 ≤85 时恢复一次
    assert am.overall() == "normal"


def test_alert_probe_failure_no_fake_recovery(ledger):
    """探测失败不得伪造恢复：保持 ACTIVE，不写 recovered"""
    from panel.alerts import AlertManager
    p = Probe()
    p.push(backend_ok=True, pool_util=95, lock_waits=0)     # 触发
    p.push(backend_ok=False)                                # 探测失败
    p.push(backend_ok=False)                                # 继续失败
    p.push(backend_ok=True, pool_util=96, lock_waits=0)     # 恢复探测仍超标
    am = AlertManager(ledger, p, recover_confirm=1)
    for _ in range(4):
        am.tick()
    rec = [e for e in _events(ledger, "recovered") if e[0] == "pool_util"]
    fired = [e for e in _events(ledger, "fired") if e[0] == "pool_util"]
    assert rec == []                                 # 没恢复
    assert len(fired) == 1                           # 也没重复触发
    assert any(a["key"] == "pool_util" for a in am.active())


def test_alert_lock_waits_is_critical(ledger):
    """锁等待 → critical，overall 变红"""
    from panel.alerts import AlertManager
    p = Probe()
    p.push(backend_ok=True, pool_util=10, lock_waits=3)
    am = AlertManager(ledger, p, recover_confirm=1)
    am.tick()
    fired = _events(ledger, "fired")
    assert any(e[0] == "lock_waits" and e[1] == "critical" for e in fired)
    assert am.overall() == "critical"


def test_alert_duplicate_event_blocked(ledger):
    """同一 episode+transition 只落一行（UNIQUE 防重复）"""
    from panel.alerts import AlertManager
    p = Probe()
    p.push(backend_ok=True, pool_util=95, lock_waits=0)
    am = AlertManager(ledger, p, recover_confirm=1)
    am.tick()
    before = ledger.conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    # 强行重放同一次触发（模拟并发/重试）
    ep = am._state["pool_util"]["episode"]
    am._fire("pool_util", ep, 95,
             {"level": "warning", "label": "连接池占用", "threshold": 90, "unit": "%"},
             am._state["pool_util"])
    after = ledger.conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    assert after == before                            # 没有多出一行
