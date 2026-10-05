# tests/test_repair_panel.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, pytest
from panel.alerts import AlertManager
from senses.gap_alerts import GapAlertBridge
from senses.repair import RepairPlanner, RepairWorker, GapContext, RepairResult


def _worker(ledger, **kw):
    gb = GapAlertBridge(ledger, AlertManager(ledger), poll_interval=99)
    return RepairWorker(ledger, gap_bridge=gb, **kw)


def test_repair_detail_shape(ledger):
    from audit.ledger_factory import make_ledger
    from panel.panel_api import repair_detail
    led = ledger
    rw = _worker(led, writeback=lambda g: RepairResult(
        status="restored", restored=g.missing, artifact="buf://g1", artifact_sha="ab"*32))
    rw.enqueue(GapContext("g1","t1", trace_id="tr1", frame_start=100,
                          frame_end=104, missing=5, cause_hint="writeback_failed"))
    rw.run_once()
    # 直接调用端点函数
    import panel.panel_api as api
    api.router.repair = rw
    api.router.ledger = led
    out = repair_detail("g1")
    d = out["data"]
    assert d["job"]["cause_text"].startswith("已采到")
    assert d["attempts"][0]["attempt_no"] == 1
    assert d["attempts"][0]["restored"] == 5
    assert d["verdict"][0] == "ok"
    assert "已补齐 5 帧" in d["verdict"][1]


def test_permanent_reason_visible(ledger):
    import panel.panel_api as api
    rw = _worker(ledger)
    rw.enqueue(GapContext("g2","t1", trace_id="tr1", missing=3,
                          cause_hint="live_passed"))
    api.router.repair = rw; api.router.ledger = ledger
    d = api.repair_detail("g2")["data"]
    assert d["job"]["cause_level"] == "bad"
    assert "永久丢失" in d["job"]["cause_text"]
    assert d["verdict"][0] == "bad" and "永久丢失 3 帧" in d["verdict"][1]
    assert d["attempts"] == []            # 不可补 → 不执行、无尝试


def test_resample_verdict_not_claim_restored(ledger):
    import panel.panel_api as api
    rw = _worker(ledger, resample=lambda g: RepairResult(status="resampled",
                                                        resampled=g.missing))
    rw.enqueue(GapContext("g3","t1", trace_id="tr1", missing=7,
                          cause_hint="not_captured"))
    rw.run_once()
    api.router.repair = rw; api.router.ledger = ledger
    d = api.repair_detail("g3")["data"]
    assert d["verdict"][0] == "warn"
    assert "原缺帧仍记永久缺失" in d["verdict"][1]
    assert "已补齐" not in d["verdict"][1]     # 绝不冒充补齐


def test_dead_letter_verdict(ledger):
    import panel.panel_api as api
    rw = _worker(ledger, max_attempts=1, base_backoff=0,
                 writeback=lambda g: RepairResult(status="failed", error="io 错"))
    rw.enqueue(GapContext("g4","t1", trace_id="tr1", missing=2,
                          cause_hint="writeback_failed"))
    rw.run_once()
    api.router.repair = rw; api.router.ledger = ledger
    d = api.repair_detail("g4")["data"]
    assert d["job"]["state"] == "dead_letter"
    assert d["verdict"][0] == "bad" and "死信" in d["verdict"][1]


def test_repair_detail_unknown_gap(ledger):
    import panel.panel_api as api
    api.router.repair = _worker(ledger); api.router.ledger = ledger
    d = api.repair_detail("不存在")["data"]
    assert d["job"] is None and d["attempts"] == []
