# tests/test_body_routes.py —— 面板身体链/见证接口：翻页、游标篡改、换筛选复用、空态诚实
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用真路由函数 + 真 SQLite 身体库（走 panel.deps 适配器），不 mock SQL。
import asyncio

import pytest
from fastapi import HTTPException

from migrations.runner import apply_pending


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def panel(tmp_path, monkeypatch):
    """把身体库指到临时文件并重载面板依赖/路由，测试之间零污染。"""
    import importlib
    monkeypatch.setenv("GBT_DB_PATH", str(tmp_path / "panel.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    import panel.deps as deps
    import common.db as cdb
    import panel.routes.body as body_routes
    import panel.routes.body_witness as wit_routes

    importlib.reload(deps)
    importlib.reload(cdb)
    importlib.reload(body_routes)
    importlib.reload(wit_routes)
    run(apply_pending(deps.db))
    return deps.db, body_routes, wit_routes


def _seed_chain(db, n=5, event_type="scan"):
    import body.registry as REG
    names = ["p0.py", "p1.py", "p2.py", "p3.py", "p4.py"]
    for i in range(n):
        run(REG.register(db, event_type=event_type, actor="t1",
                         payload={"i": i},
                         targets=[{"path": names[i % len(names)],
                                   "after_hash": "h" + str(i)}]))


# ═══ 空态诚实：没有登记就是空列表 + 明确 dialect，不编数据 ═══
def test_chain_empty_is_honest(panel):
    _db, B, _W = panel
    out = run(B.body_chain(limit=10, db=_db))
    assert out["items"] == [] and out["has_more"] is False
    assert out["dialect"] == "sqlite"


def test_witnesses_empty_is_not_green(panel):
    _db, _B, W = panel
    out = run(W.witnesses(db=_db))
    q, probe = out["quorum"], out["probe"]
    assert q["valid"] == 0 and q["required"] == 2 and q["status"] == "unknown"
    assert probe["observed_at"] is None and probe["stale"] is True   # 无快照 → 必须 stale
    assert out["witnesses"] == [] and out["latest_sealed"] is None


# ═══ 翻页：不重不漏、按 seq 倒序 ═══
def test_chain_pagination_walks_all_without_overlap(panel):
    db, B, _W = panel
    _seed_chain(db, 5)
    seen, cursor, guard = [], None, 0
    while True:
        out = run(B.body_chain(cursor=cursor, limit=2, db=db))
        seen += [i["seq"] for i in out["items"]]
        cursor = out["next_cursor"]
        guard += 1
        if not out["has_more"] or not cursor or guard > 10:
            break
    assert seen == [5, 4, 3, 2, 1]                      # 倒序且一条不少、一条不重


# ═══ 游标篡改：签名不符必须 400（不静默跳页） ═══
def test_tampered_cursor_is_rejected(panel):
    db, B, _W = panel
    _seed_chain(db, 3)
    with pytest.raises(HTTPException) as ei:
        run(B.body_chain(cursor="AAAA", limit=2, db=db))
    assert ei.value.status_code == 400


def test_cursor_from_other_filter_is_rejected(panel):
    """换筛选复用旧游标 → 必须拒（否则筛选切换会静默跳页/漏页）。"""
    db, B, _W = panel
    _seed_chain(db, 3, event_type="scan")
    out = run(B.body_chain(limit=2, types="scan", db=db))
    cur = out["next_cursor"]
    assert cur
    with pytest.raises(HTTPException) as ei:
        run(B.body_chain(cursor=cur, limit=2, types="fix", db=db))
    assert ei.value.status_code == 400


def test_filter_only_returns_requested_types(panel):
    db, B, _W = panel
    import body.registry as REG
    _seed_chain(db, 2, event_type="scan")
    run(REG.register(db, event_type="fix", actor="t1", payload={"x": 1},
                     targets=[{"path": "p1.py", "after_hash": "h9"}]))
    out = run(B.body_chain(limit=10, types="fix", db=db))
    assert [i["event_type"] for i in out["items"]] == ["fix"]
    out2 = run(B.body_chain(limit=10, types="scan", db=db))
    assert all(i["event_type"] == "scan" for i in out2["items"])


# ═══ 单条下钻 + 覆盖读数 ═══
def test_chain_detail_includes_targets(panel):
    db, B, _W = panel
    _seed_chain(db, 1)
    one = run(B.body_chain_one(seq=1, db=db))
    assert one["registration"]["seq"] == 1
    assert one["targets"] and one["targets"][0]["path"] == "p0.py"


def test_coverage_reports_head_and_index(panel):
    db, B, _W = panel
    _seed_chain(db, 3)
    out = run(B.body_coverage(db=db))
    assert out["head"]["head_seq"] == 3 and out["head"]["total"] == 3
    assert out["head"]["n_scan"] == 3
    assert out["index"]["clean"] == 3                    # 责任页已进 body_files
    assert out["manifest"]["head_seq"] == 3


# ═══ 见证状态与覆盖区间：有真行才显示 ═══
def test_witness_rows_and_coverage_segments(panel):
    db, _B, W = panel
    run(db.execute(
        "INSERT INTO body_witness_status (witness_id, provider, kid, persisted_status,"
        " live_status, live_from_seq, backfilled_through_seq, consecutive_fail) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("archive-b", "backblaze-b2", "ab/1", "valid", "valid", 3, 2, 0)))
    out = run(W.witnesses(db=db))
    assert [w["witness_id"] for w in out["witnesses"]] == ["archive-b"]

    run(db.execute(
        "INSERT INTO body_anchors (anchor_uid, root_id, epoch, seq, head_hash,"
        " anchor_hash, kid, at, sealed) VALUES (?,?,?,?,?,?,?,?,1)",
        ("main/e1/3", "main", "e1", 3, "H3", "AH3", "ab/1",
         "2026-10-06T00:00:00Z")))
    run(db.execute(
        "INSERT INTO body_anchor_witnesses (anchor_uid, witness_id, seq, kid, status,"
        " head_hash, sig_ok) VALUES (?,?,?,?,?,?,1)",
        ("main/e1/3", "archive-b", 3, "ab/1", "valid", "H3")))
    # 再补一个"仅回填"的历史锚点（seq=1 <= backfilled_through_seq=2）→ 必须单独成段
    run(db.execute(
        "INSERT INTO body_anchors (anchor_uid, root_id, epoch, seq, head_hash,"
        " anchor_hash, kid, at, sealed) VALUES (?,?,?,?,?,?,?,?,1)",
        ("main/e1/1", "main", "e1", 1, "H1", "AH1", "ab/1",
         "2026-10-05T00:00:00Z")))
    run(db.execute(
        "INSERT INTO body_anchor_witnesses (anchor_uid, witness_id, seq, kid, status,"
        " head_hash, sig_ok) VALUES (?,?,?,?,?,?,1)",
        ("main/e1/1", "archive-b", 1, "ab/1", "valid", "H1")))
    cov = run(W.coverage(db=db))
    segs = cov["per_witness"][0]["segments"]
    kinds = {s["kind"] for s in segs}
    assert "backfill" in kinds and "live" in kinds       # 回填与实时必须分色分段
    back = next(s for s in segs if s["kind"] == "backfill")
    live = next(s for s in segs if s["kind"] == "live")
    assert (back["from"], back["to"]) == (1, 1)          # 回填段：不计实时 quorum
    assert (live["from"], live["to"]) == (3, 3)
    assert cov["required"] == 2
