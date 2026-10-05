# tests/test_digital_human.py —— 只读工具 HTTP 面 + 数字人实时行 + 见证探测接线
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用真路由函数 + 真 SQLite 身体库（panel.deps 适配器），不 mock SQL。
import asyncio
import json

import pytest
from fastapi import HTTPException

from migrations.runner import apply_pending


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    import importlib
    monkeypatch.setenv("GBT_DB_PATH", str(tmp_path / "panel.db"))
    monkeypatch.setenv("BODY_WITNESS_FP_KEY", "k" * 32)
    monkeypatch.setenv("BODY_WITNESS_REQUIRED_EXTERNAL", "2")
    monkeypatch.setenv("BODY_SINGLE_WORKER", "1")   # 与面板默认一致（SQLite 单进程）
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import panel.deps as deps
    import common.db as cdb
    import panel.routes.body_tools as tools_routes
    import panel.routes.digital_human as dh

    importlib.reload(deps)
    importlib.reload(cdb)
    importlib.reload(tools_routes)
    importlib.reload(dh)
    run(apply_pending(deps.db))
    return deps.db, tools_routes, dh


def test_tools_list_matches_registry(panel):
    _db, T, _D = panel
    out = run(T.tools_list())
    assert set(out["names"]) == {"media.capture", "scan.coverage", "media.queue",
                                "get_witness_snapshot"}
    assert "禁止自行计算" in out["system_rule"]


def test_snapshots_route_reports_unknown_before_sampling(panel):
    _db, T, _D = panel
    out = run(T.snapshots(request=_FakeReq(), db=_db))
    body = json.loads(out.body)
    assert set(body) == {"devour", "scan", "queue"}
    for v in body.values():
        assert v["unknown_reason"] == "no_snapshot"
        assert "无法确认" in v["safe_sentence"]


def test_snapshot_etag_revalidates(panel):
    _db, T, _D = panel
    first = run(T.snapshots(request=_FakeReq(), db=_db))
    etag = first.headers["etag"]
    second = run(T.snapshots(request=_FakeReq(headers={"if-none-match": etag}), db=_db))
    assert second.status_code == 304                    # 可复用但必须回源验 stale


def test_unknown_domain_and_call_route(panel):
    _db, T, _D = panel
    with pytest.raises(HTTPException):
        run(T.snapshot_one("nope", db=_db))
    res = run(T.call("media.queue", T.ToolCall(session_id="s1"), db=_db))
    assert res["domain"] == "queue" and "无法确认" in res["safe_sentence"]


def test_call_route_audits_to_read_tool_audit(panel):
    _db, T, _D = panel
    run(T.call("media.queue", T.ToolCall(session_id="session-42"), db=_db))
    rows = run(_db.fetch_all(
        "SELECT session_id, tool FROM read_tool_audit ORDER BY rowid DESC LIMIT 1"))
    assert rows[0]["session_id"] == "session-42"


# ═══ 数字人页：实时行 / 工具问询 / 工具清单 ═══
def test_digital_human_witness_line_is_honest(panel):
    _db, _T, D = panel
    D.LEDGER_IMPORT = _db
    out = run(D.witness_line(db=_db))
    assert out["unknown_reason"] == "no_snapshot"
    assert "无法确认" in out["safe_sentence"]           # 没快照时绝不说"0 个见证"


def test_digital_human_ask_speaks_safe_sentence(panel):
    _db, _T, D = panel
    D.LEDGER_IMPORT = _db
    said = []

    class Bus:
        async def submit(self, sid, text, *, priority, source, event_ids=None):
            said.append((sid, text, source))

    req = _FakeReq(app=_FakeApp(bus=Bus()))
    out = run(D.ask("get_witness_snapshot", D.Ask(session_id="witness", say=True), req))
    assert out["tool"] == "get_witness_snapshot"
    assert said and said[0][1] == out["safe_sentence"]   # 念的就是转述，不另编话
    assert said[0][2] == "dialog"


def test_digital_human_publish_fans_out_to_subscribers(panel):
    _db, _T, D = panel
    q = asyncio.Queue(maxsize=4)
    D.BUS_SUBSCRIBERS.setdefault(D.SESSION, []).append(q)
    try:
        D.publish(D.SESSION, {"type": "speaking", "text": "见证 2/2 正常"})
        assert q.get_nowait()["text"] == "见证 2/2 正常"
        # 队列满不阻塞：丢最旧、保最新
        for i in range(6):
            D.publish(D.SESSION, {"type": "alert", "text": f"t{i}"})
        got = [q.get_nowait()["text"] for _ in range(q.qsize())]
        assert got[-1] == "t5" and "t0" not in got
    finally:
        D.BUS_SUBSCRIBERS[D.SESSION].remove(q)


# ═══ 探测接线：掉票 → 快照 + outbox + 播报（端到端，用假见证）═══════
def test_probe_once_records_vote_loss_and_queues_voice(panel, monkeypatch):
    """端到端：身份 E3 + 内容一致 → 2/2；w2 降到 E2（要求 E3）→ 掉票 + 播报原因。"""
    from body import witness_runtime as WR
    from body.identity import E2, E3, Identity
    _db, _T, _D = panel
    run(_db.execute("""INSERT INTO body_witness_status (witness_id, provider)
        VALUES ('w1','s3-compatible'), ('w2','s3-compatible')"""))

    def ident(wid, acct, level):
        return Identity(witness_id=wid, provider="s3-compatible",
                        endpoint_host="s3.local", bucket="b",
                        account_id=acct, account_source=level,
                        owner_id=acct, owner_source=level,
                        evidence_level=level,
                        status="verified" if level == E3 else "attested")

    def runtime(level_w2):
        return [WR.WitnessRuntime("w1", provider=_Provider(),
                                  cfg={"provider": "s3-compatible", "kid": "k1"},
                                  client=None),
                WR.WitnessRuntime("w2", provider=_Provider(),
                                  cfg={"provider": "s3-compatible", "kid": "k2"},
                                  client=None)]

    async def _load(*a, **kw):
        return runtime(None)

    monkeypatch.setattr(WR, "load_witnesses", _load)
    monkeypatch.setattr(WR.WitnessRuntime, "probe_identity",
                        lambda self: ident(self.witness_id, "acct-" + self.witness_id, E3))
    said = []

    class Bus:
        async def submit(self, sid, text, *, priority, source, event_ids=None):
            said.append(text)

    app = _FakeApp(ledger=_db, bus=Bus())
    # ① 首轮：2/2 健康 → 不开口
    first = run(WR.probe_once(app))
    assert first["votes"] == 2 and first["status"] == "healthy"
    assert said == [] and run(_db.fetch_all("SELECT * FROM witness_voice_outbox")) == []

    # ② w2 身份降到 E2（要求 E3）→ 掉票，原因必须说出来
    #    身份结论是按 TTL 缓存的（不每轮打供应商），模拟降级要显式失效缓存
    WR.clear_cache()
    monkeypatch.setattr(WR.WitnessRuntime, "probe_identity",
                        lambda self: ident(self.witness_id, "acct-" + self.witness_id,
                                           E2 if self.witness_id == "w2" else E3))
    out = run(WR.probe_once(app))
    assert out["votes"] == 1 and out["status"] == "degraded"
    snap = run(_db.fetch_one("SELECT valid_count, required, status FROM witness_snapshot WHERE id=1"))
    assert (snap["valid_count"], snap["required"]) == (1, 2)
    kinds = {r["kind"] for r in run(_db.fetch_all("SELECT kind FROM witness_voice_outbox"))}
    assert {"vote_lost", "quorum_low"} <= kinds
    # 合并窗口默认 2 秒（避免连续跳变念成一串）；这里显式关掉窗口验证句子内容
    run(WR.flush_voice(app, merge_window=0, degraded_cooldown=0))
    assert said and "1/2" in said[0] and "证据等级不足" in said[0]   # 身体自己说出掉票原因
    assert run(_db.fetch_one("SELECT state FROM witness_voice_outbox WHERE kind='vote_lost'")) \
        ["state"] == "spoken"
    row = run(_db.fetch_one("SELECT identity_error_code, vote_eligible "
                            "FROM body_witness_status WHERE witness_id='w2'"))
    assert row["identity_error_code"] == "EVIDENCE_BELOW_REQUIRED" and row["vote_eligible"] == 0


class _Provider:
    """假见证存储：LATEST 锚点对象读得回、自哈希自洽（内容维度 valid）。"""

    name = "w"

    async def get(self, key):
        return json.dumps({"ns": "anchor", "root_id": "main", "epoch": "e1", "seq": 1,
                           "head_hash": "h1", "prev_anchor_hash": None, "kid": "k1",
                           "at": "2026-01-01T00:00:00Z"}).encode()


class _FakeApp:
    def __init__(self, *, ledger=None, bus=None, witnesses=None, multi=None):
        self.state = type("S", (), {})()
        self.state.ledger = ledger
        self.state.voice_bus = bus
        self.state.witnesses = witnesses or []
        self.state.anchor_multi = multi
        self.state.config_generation = "g1"


class _FakeReq:
    def __init__(self, *, headers=None, app=None):
        self.headers = headers or {}
        self.app = app or _FakeApp()
        self._app2 = self.app

    async def is_disconnected(self):
        return True
