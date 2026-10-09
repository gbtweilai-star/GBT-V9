# tests/test_feeds_emotion.py —— 真实事件源 → 情绪 → 播报/告警；覆盖率回归；文静台湾腔
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖主人 2026-10-06 清单里可离线验证的部分：
#   ① 真实快照源解析 payload_json（吞噬丢帧 / 扫描覆盖 / 队列积压 / 死信）
#   ② 快照不可用不报 0（宁缺勿假）；毛利率式的"覆盖率由真实计数推导"
#   ③ 覆盖率回归指标：与上一次比下降（正数=回归），符号与等级正确
#   ④ EdgeGate 只在边沿触发一次
#   ⑤ EmotionFeeder：事件 → director.notify（带播报文本）+ body_alerts 留痕
#   ⑥ 文静台湾腔：默认口音 + SSML 参数（慢一点、柔一点、句尾软收）
import asyncio
import json
import time

import pytest

from body.event_feeds import (EdgeGate, SnapshotJsonFeed, CoverageSnapshotFeed,
                              default_feeds, default_rules, message_for)
from body.prosody import DEFAULT_STYLE, STYLES, choose_style, render


def run(coro):
    return asyncio.run(coro)


class FakeDB:
    """假身体库：按查询目标返回快照行或覆盖率历史。"""

    def __init__(self, snapshots=None, coverage=None):
        self.snapshots = snapshots or []
        self.coverage = coverage or []
        self.alerts = []

    async def fetch_all(self, sql, params=()):
        if "body_read_snapshots" in sql:
            return self.snapshots
        return self.coverage

    async def record_alert(self, kind, payload=None, level="warning", **kw):
        self.alerts.append({"kind": kind, "payload": payload, "level": level})
        return "alert-id"

    # 除读方法之外的一切调用（情绪/关系落库等）兜成异步空操作：假库只喂读数
    def __getattr__(self, name):
        if name == "dialect":                      # 方言是真值，不能被兜成函数
            return "sqlite"
        async def _noop(*args, **kwargs):
            return 0
        return _noop


def _snap(domain, payload, at="2026-10-06T00:00:00Z"):
    return {"domain": domain, "payload_json": json.dumps(payload), "observed_at": at}


# ═══ ① 真实源解析 ═══
def test_snapshot_json_feed_reads_real_payloads():
    db = FakeDB(snapshots=[
        _snap("devour", {"coverage": "observed", "gaps": 3, "frames": 100}),
        _snap("queue", {"coverage": "observed", "depth": {"queued": 260, "dead": 6},
                        "wait": {"oldest_runnable_age": 300}}),
        _snap("scan", {"coverage": "observed",
                       "index": {"clean": 90, "dirty": 5, "missing": 5}}),
    ])
    reads = {r.key: r for r in run(SnapshotJsonFeed(default_rules()).read(db))}
    assert reads["devour:dropped_frames"].value == 3
    assert reads["queue:queue_depth"].value == 260 and reads["queue:queue_depth"].level == "crit"
    assert reads["queue:dlq_count"].value == 6 and reads["queue:dlq_count"].level == "crit"
    assert reads["queue:oldest_wait_s"].value == 300
    assert reads["scan:unscanned_pages"].value == 5
    # 覆盖率由真实计数推导：90 / (90+5+5) = 90%
    assert reads["scan:coverage_percent"].value == 90.0
    assert reads["scan:coverage_percent"].level == "ok"      # 阈值 80 → 90 是 ok


def test_unavailable_snapshot_is_skipped_not_zero():
    """快照不可用时**不报 0**（0 会被误读成"没丢帧/没积压"）。"""
    db = FakeDB(snapshots=[_snap("queue", {"coverage": "unavailable", "error": "no_ledger"})])
    reads = run(SnapshotJsonFeed(default_rules()).read(db))
    assert [r.key for r in reads if r.key.startswith("queue:")] == []


# ═══ ②③ 覆盖率回归（正数=变差）═══
def test_coverage_regression_metric_sign_and_level():
    db = FakeDB(coverage=[
        {"backend": "py", "overall_percent": 84.5, "threshold": 80.0,
         "generated_epoch": 200, "commit_sha": "new"},
        {"backend": "py", "overall_percent": 88.0, "threshold": 80.0,
         "generated_epoch": 100, "commit_sha": "old"},
    ])
    reads = {r.key: r for r in run(CoverageSnapshotFeed().read(db))}
    drop = reads["scan:py:drop"]
    assert drop.value == 3.5                                  # 88 → 84.5 = 掉了 3.5 点
    assert drop.level in ("warn", "crit")                     # 超过默认 2.0 → 报警
    assert drop.evidence["from_commit"] == "old" and drop.evidence["to_commit"] == "new"
    say, severity = message_for(drop, drop.level)
    assert "回归" in say and severity in ("warning", "critical")


def test_coverage_improvement_is_not_a_regression():
    db = FakeDB(coverage=[
        {"backend": "py", "overall_percent": 91.0, "threshold": 80.0,
         "generated_epoch": 200, "commit_sha": "new"},
        {"backend": "py", "overall_percent": 85.0, "threshold": 80.0,
         "generated_epoch": 100, "commit_sha": "old"},
    ])
    reads = {r.key: r for r in run(CoverageSnapshotFeed().read(db))}
    assert reads["scan:py:drop"].value == -6.0
    assert reads["scan:py:drop"].level == "ok"                # 提升不是回归


def test_single_snapshot_has_no_regression_reading():
    db = FakeDB(coverage=[{"backend": "py", "overall_percent": 70.0, "threshold": 80.0,
                           "generated_epoch": 1, "commit_sha": "solo"}])
    reads = run(CoverageSnapshotFeed().read(db))
    assert [r.key for r in reads] == ["scan:py"]              # 没有上一版就不编回归


def test_default_feeds_use_real_sources():
    names = [type(f).__name__ for f in default_feeds()]
    assert names == ["SnapshotJsonFeed", "CoverageSnapshotFeed"]


# ═══ ④ 边沿门 ═══
def test_edge_gate_fires_once_per_edge():
    rule = [r for r in default_rules() if r.metric == "queue_depth"][0]
    gate = EdgeGate()
    from body.event_feeds import Reading
    hot = Reading("queue", "queue:depth", 260, rule.level_for(260), rule)
    assert gate.decide(hot, time.time()) == "crit"            # 第一次 → 事件
    assert gate.decide(hot, time.time() + 1) is None          # 同一边沿 → 不重复念
    cool = Reading("queue", "queue:depth", 0, rule.level_for(0), rule)
    assert gate.decide(cool, time.time() + 200) == "ok"       # 恢复 → 一次恢复事件


# ═══ ⑤ 情绪喂料：事件 → 通知 + 播报 + 告警留痕 ═══
class FakeDirector:
    def __init__(self, db):
        self.db = db
        self.calls = []

    async def notify(self, kind, severity="info", **kw):
        self.calls.append({"kind": kind, "severity": severity, **kw})
        return None


def test_emotion_feeder_notifies_and_records_alert():
    from body.emotion_feeder import EmotionFeeder
    db = FakeDB(snapshots=[
        _snap("queue", {"coverage": "observed", "depth": {"queued": 300, "dead": 0},
                        "wait": {"oldest_runnable_age": 10}}),
    ])
    director = FakeDirector(db)
    feeder = EmotionFeeder(director, db=db, feeds=[SnapshotJsonFeed(default_rules())])
    events = run(feeder.poll_once())
    kinds = {e["kind"] for e in events}
    assert "queue_backlog" in kinds                            # 真实事件进了情绪/播报链路
    assert director.calls and director.calls[0]["say"]         # 带播报文案
    assert director.calls[0]["purpose"] == "alert"
    assert [a["kind"] for a in db.alerts] == ["queue_backlog"]  # 面板告警留痕
    assert db.alerts[0]["level"] in ("warning", "critical")


# ═══ ⑥ 文静台湾腔 ═══
def test_wenjing_taiwan_is_default_voice():
    assert DEFAULT_STYLE == "文静台湾腔"
    assert "文静台湾腔" in STYLES
    assert choose_style("平", 0.0, "report") == "文静台湾腔"    # 无情绪时用默认口音
    st = STYLES["文静台湾腔"]
    assert st.rate < 1.0 and st.pitch > 0 and st.volume < 0.9  # 慢一点、柔一点
    assert "喔。" in st.softeners and "好不好？" in st.softeners  # 台湾腔软收尾


def test_render_uses_taiwan_prosody_and_softens_tail():
    r = render("三段扫描已经完成，发现两处漏洞。", "文静台湾腔", intensity=0.2)
    assert r.rate < 1.0 and r.pitch > 0
    assert "prosody" in r.ssml
    r2 = render("三段扫描已经完成，发现两处漏洞", "文静台湾腔", intensity=0.2)
    assert any(s.rstrip("。") in r2.text for s in STYLES["文静台湾腔"].softeners) or \
        r2.text.endswith(tuple(s for s in STYLES["文静台湾腔"].softeners))
