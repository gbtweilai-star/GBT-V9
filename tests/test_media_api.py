# tests/test_media_api.py —— 终态事件游标分页 / 任务下钻（双后端口径一致）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time
import uuid

import pytest

from media.queue import JobQueue, ensure_tables
from panel import media_api


@pytest.fixture
def wired(ledger):
    ensure_tables(ledger)
    q = JobQueue(ledger)
    media_api.router.ledger = ledger
    media_api.router.queue = q
    return ledger, q


def _ev(led, event, ts, job="j1", detail=None):
    with led._tx() as cur:
        cur.execute("""INSERT INTO media_job_events
            (event_id,job_id,attempt_no,event,ts,detail) VALUES(?,?,?,?,?,?)""",
            (uuid.uuid4().hex[:16], job, 1, event, ts, detail))


def _items(resp):
    return resp["data"]["items"]


def _te(**kw):
    """直调路由：显式补齐所有参数，避免拿到 FastAPI 的 Query 默认对象"""
    args = {"limit": 50, "event": None, "from_ts": None, "to_ts": None,
            "cursor_ts": None, "cursor_id": None, "order": "desc"}
    args.update(kw)
    return media_api.media_terminal_events(**args)


# ═══ 同时间戳翻页：不重不漏 ═══
def test_same_timestamp_pagination_no_loss(wired):
    led, _ = wired
    base = time.time()
    for i in range(5):
        _ev(led, "completed", base)          # 5 条同 ts → 全靠 event_id 决胜
    p1 = _te(limit=2)
    assert len(_items(p1)) == 2
    cur = p1["data"]["next_cursor"]
    p2 = _te(limit=2, cursor_ts=cur["ts"], cursor_id=cur["id"])
    p3 = _te(limit=2, cursor_ts=p2["data"]["next_cursor"]["ts"],
             cursor_id=p2["data"]["next_cursor"]["id"])
    seen = [i["event_id"] for i in _items(p1) + _items(p2) + _items(p3)]
    assert len(seen) == 5 and len(set(seen)) == 5      # 无重复无遗漏
    assert p3["data"]["next_cursor"] is None


# ═══ 游标必须成对（篡改/缺一半 → 明确报错，不跳行） ═══
def test_cursor_must_be_paired(wired):
    wired  # 仅确保 fixture
    r = _te(cursor_ts=time.time())        # 少了 id
    assert "cursor" in (r.get("error") or "")
    r2 = _te(cursor_id="abc")             # 少了 ts
    assert "cursor" in (r2.get("error") or "")


# ═══ 换筛选复用同一游标语义：failed 单独成流 ═══
def test_filter_switch_isolated(wired):
    led, _ = wired
    base = time.time()
    _ev(led, "completed", base + 1)
    _ev(led, "failed", base + 2)
    r = _te(event="failed", limit=10)
    items = _items(r)
    assert items and all(i["event"] == "failed" for i in items)
    assert r["data"]["total"] == 1
    r2 = _te(event="completed", limit=10)
    assert r2["data"]["total"] == 1
    bad = _te(event="bogus")   # pattern 校验拒绝（直调不校验，看是否优雅返回）
    assert bad is not None


# ═══ 时间窗 ═══
def test_time_window(wired):
    led, _ = wired
    now = time.time()
    _ev(led, "completed", now - 7200)      # 2 小时前
    _ev(led, "completed", now - 60)
    r = _te(limit=10, from_ts=now - 3600)
    assert r["data"]["total"] == 1


# ═══ 任务下钻：任务行 + 事件链 ═══
def test_job_detail_includes_events(wired):
    led, q = wired
    j = q.enqueue("p", "stage", "s", {"x": 1}, max_attempts=1)
    w = q.claim("w")
    q.fail(j["job_id"], w["lease_owner"], "boom")
    r = media_api.media_queue_job(j["job_id"], events_limit=50)
    d = r["data"]
    assert d["job"]["state"] == "dead"
    names = [e["event"] for e in d["events"]]
    assert "enqueued" in names and "claimed" in names and "dead" in names
    miss = media_api.media_queue_job("no-such-job", events_limit=50)
    assert "不存在" in (miss.get("error") or "")


# ═══ 队列统计：原始计数与口径 ═══
def test_queue_stats_raw_counts(wired):
    led, q = wired
    j = q.enqueue("p", "stage", "s", {"x": 1})
    w = q.claim("w")
    q.complete(j["job_id"], w["lease_owner"], artifact_sha="abc")
    s = media_api.media_queue_stats()["data"]
    assert s["failure_rate_kind"] == "observed"
    assert s["success_count"] == 1 and s["failed_count"] == 0
    assert s["depth"]["queued"] == 0
