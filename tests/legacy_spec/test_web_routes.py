# tests/test_web_routes.py —— 抓取接口：翻页 / 游标对抗 / 双后端一致
from __future__ import annotations
import base64, json, os
from datetime import datetime, timezone

import pytest

from parity_routes_time import canon_iso           # 或从 conftest 导入
from tests.conftest import DDL_TS

pytestmark = pytest.mark.web_api

FIXED = DDL_TS


def page(client, **params):
    r = client.get("/api/web/scrapes", params=params)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def drain(client, limit=2, **filters):
    seen, cursor = [], None
    while True:
        body = page(client, limit=limit, cursor=cursor, **filters) if cursor \
            else page(client, limit=limit, **filters)
        seen.extend(x["run_id"] for x in body["items"])
        cursor = body["next_cursor"]
        if not body["has_more"]:
            return seen


# ═══════════ ① 同时间戳翻页 ═══════════
def test_same_timestamp_paging_no_gap_no_duplicate(web_api):
    _, client, db = web_api
    ids = [f"r-{i:03}" for i in range(1, 6)]
    for rid in ids:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)

    seen = drain(client, limit=2)
    assert seen == sorted(ids, reverse=True)        # run_id DESC 兜底稳定排序
    assert len(seen) == len(set(seen)) == 5         # 无重复、无遗漏


def test_paging_order_stable_across_repeat_runs(web_api):
    _, client, db = web_api
    for rid in [f"r-{i:03}" for i in range(1, 6)]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    assert drain(client, limit=2) == drain(client, limit=2)   # 顺序确定


def test_insert_between_pages_does_not_repeat(web_api):
    _, client, db = web_api
    original = [f"r-{i:03}" for i in range(1, 6)]
    for rid in original:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)

    first = page(client, limit=2)
    seen = [x["run_id"] for x in first["items"]]

    db.insert_scrape_run(run_id="zz-new", host="example.com", created_at=FIXED)

    cursor = first["next_cursor"]
    while cursor:
        body = page(client, limit=2, cursor=cursor)
        seen.extend(x["run_id"] for x in body["items"])
        cursor = body["next_cursor"]

    assert "zz-new" not in seen                      # 新行排在游标之前 → 不重放
    assert seen == sorted(original, reverse=True)


def test_has_more_boundary_exact_multiple(web_api):
    _, client, db = web_api
    for rid in ["a", "b", "c", "d"]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    first = page(client, limit=2)
    assert first["has_more"] is True and len(first["items"]) == 2
    second = page(client, limit=2, cursor=first["next_cursor"])
    assert second["has_more"] is False and second["next_cursor"] is None


# ═══════════ ② 游标对抗 ═══════════
def _flip_sig(token: str) -> str:
    raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    return base64.urlsafe_b64encode(raw[:-1] + bytes([raw[-1] ^ 1])).decode().rstrip("=")


def test_cursor_tamper_and_truncate(web_api):
    _, client, db = web_api
    for rid in ["r-1", "r-2", "r-3"]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    token = page(client, limit=1)["next_cursor"]

    assert client.get("/api/web/scrapes",
                      params={"limit": 1, "cursor": _flip_sig(token)}).status_code == 400
    assert client.get("/api/web/scrapes",
                      params={"limit": 1, "cursor": token[:-8]}).status_code == 400
    assert client.get("/api/web/scrapes",
                      params={"limit": 1, "cursor": "not-base64!!"}).status_code == 400


def test_cursor_scope_mismatch_on_filter_change(web_api):
    """换筛选复用旧游标 → 400（前端应清空重拉）"""
    _, client, db = web_api
    for rid in ["r-1", "r-2", "r-3"]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    db.insert_scrape_run(run_id="o-1", host="other.example", created_at=FIXED)

    token = page(client, limit=1, host="example.com")["next_cursor"]

    for changed in ({"host": "other.example"}, {"tier": "browser"},
                    {"matched": "false"}, {"from_ts": FIXED.timestamp()}):
        r = client.get("/api/web/scrapes",
                       params={"limit": 1, "cursor": token, **changed})
        assert r.status_code == 400 and r.json()["detail"] == "invalid_cursor", changed


def test_cursor_key_missing_is_503_not_unsigned(web_api, monkeypatch):
    _, client, db = web_api
    db.insert_scrape_run(run_id="a", host="example.com", created_at=FIXED)
    monkeypatch.delenv("WEB_CURSOR_HMAC_KEY", raising=False)
    assert client.get("/api/web/scrapes", params={"limit": 1}).status_code == 503


def test_cursor_key_rotation_invalidates_old_cursor(web_api, monkeypatch):
    _, client, db = web_api
    for rid in ["r-1", "r-2"]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    token = page(client, limit=1)["next_cursor"]
    monkeypatch.setenv("WEB_CURSOR_HMAC_KEY", "z" * 48)       # 轮换
    assert client.get("/api/web/scrapes",
                      params={"limit": 1, "cursor": token}).status_code == 400


def test_cursor_not_reusable_across_endpoints(web_api):
    _, client, db = web_api
    for rid in ["r-1", "r-2"]:
        db.insert_scrape_run(run_id=rid, host="example.com", created_at=FIXED)
    db.insert_selector_version(domain="example.com", selector_id="sel-1", version=1)
    token = page(client, limit=1)["next_cursor"]

    r = client.get("/api/web/selectors/example.com/sel-1",
                   params={"limit": 1, "cursor": token})
    assert r.status_code == 422, "selector 端点的整数游标必须拒绝非数字 token"


# ═══════════ ③ 详情与选择器 ═══════════
def test_detail_404_and_payload_shape(web_api):
    _, client, db = web_api
    db.insert_scrape_run(run_id="r-1", host="example.com", created_at=FIXED,
                         result_count=3, selector_id="sel-1", selector_version=1)
    db.insert_item(run_id="r-1", position=0, excerpt="hello")
    db.insert_item(run_id="r-1", position=1, excerpt="world")
    db.insert_selector_version(domain="example.com", selector_id="sel-1", version=1,
                               match_score=0.42, last_matched=FIXED)

    assert client.get("/api/web/scrapes/nope").status_code == 404
    assert client.get("/api/web/scrapes/" + "x" * 200).status_code == 422

    d = client.get("/api/web/scrapes/r-1").json()["data"]
    assert [i["excerpt"] for i in d["items"]] == ["hello", "world"]
    assert d["items_truncated"] is True                 # result_count 3 > 实取 2
    assert d["artifact_available"] is False
    assert d["selector"]["match_score"] == pytest.approx(0.42)
    assert "created_at_ts" in d["run"]


def test_detail_never_leaks_storage_credentials(web_api):
    _, client, db = web_api
    db.insert_scrape_run(run_id="r-1", host="example.com", created_at=FIXED,
                         artifact_ref="r2://bucket/parity/tok/seg.mkv")
    d = client.get("/api/web/scrapes/r-1").json()["data"]
    blob = json.dumps(d)
    assert d["artifact_available"] is True
    assert "X-Amz-Signature" not in blob and "AKIA" not in blob
    assert "r2://" not in blob                          # 只给标记，不给引用/预签名


def test_selector_versions_ascending_and_null_score(web_api):
    _, client, db = web_api
    for v, score in [(1, 0.9), (2, None), (3, 0.42)]:
        db.insert_selector_version(domain="example.com", selector_id="sel-1",
                                   version=v, match_score=score, last_matched=FIXED)
    d = client.get("/api/web/selectors/example.com/sel-1").json()["data"]
    assert [x["version"] for x in d["versions"]] == [1, 2, 3]   # ★升序画趋势图
    assert d["versions"][1]["match_score"] is None              # NULL 保真
    assert d["versions"][2]["match_score"] == pytest.approx(0.42)


def test_selector_validation_and_404(web_api):
    _, client, _ = web_api
    assert client.get("/api/web/selectors/BAD_HOST_!/sel-1").status_code == 422
    assert client.get("/api/web/selectors/example.com/" + "s" * 200).status_code == 422
    d = client.get("/api/web/selectors/example.com/missing").json()["data"]
    assert d["versions"] == [] and d["has_more"] is False       # 空集而非 404


def test_argument_validation(web_api):
    _, client, _ = web_api
    assert client.get("/api/web/scrapes", params={"tier": "drop"}).status_code == 422
    assert client.get("/api/web/scrapes", params={"host": "bad host"}).status_code == 422
    assert client.get("/api/web/scrapes",
                      params={"from_ts": 200, "to_ts": 100}).status_code == 422
    assert client.get("/api/web/scrapes", params={"limit": 101}).status_code == 422
    assert client.get("/api/web/scrapes", params={"limit": 0}).status_code == 422


# ═══════════ ④ 双后端一致性 ═══════════
def normalize_row(row: dict) -> dict:
    required = {"run_id", "host", "tier", "matched", "match_score", "created_at_ts"}
    assert required <= set(row), f"缺字段: {required - set(row)}"
    r = dict(row)
    r["matched"] = bool(r["matched"])                       # 0/1 → bool 统一
    r["match_score"] = None if r["match_score"] is None else round(float(r["match_score"]), 6)
    r["created_at_us"] = round(float(r.pop("created_at_ts")) * 1_000_000)  # 微秒对齐
    for k in ("trace_id", "tentacle_id", "status", "policy_code",
              "selector_id", "selector_version"):
        r.pop(k, None)                                      # 非语义差异不参与比较
    return r


def _seed_canonical(db):
    db.insert_scrape_run(run_id="r-1", host="example.com", matched=True,
                         match_score=0.9, created_at=FIXED, result_count=2)
    db.insert_scrape_run(run_id="r-2", host="example.com", matched=False,
                         match_score=None, created_at=FIXED, status="blocked_by_policy",
                         policy_code="robots_disallow")
    db.insert_scrape_run(run_id="r-3", host="other.example", tier="stealth",
                         matched=True, match_score=0.42, created_at=FIXED)


def test_backend_parity_same_seed_same_result(web_api, tmp_path):
    """在**同一个后端**内跑自洽检查；跨后端对拍见下一个测试。"""
    _, client, db = web_api
    _seed_canonical(db)

    rows = [normalize_row(x) for x in page(client, limit=10)["items"]]
    assert [r["run_id"] for r in rows] == ["r-1", "r-2", "r-3"]   # 同时戳 → run_id DESC
    assert rows[1]["match_score"] is None
    assert rows[0]["matched"] is True

    # 分页片段拼起来必须等于整页
    parts = drain(client, limit=1)
    assert parts == ["r-1", "r-2", "r-3"]


@pytest.mark.pg
def test_cross_backend_identical_normalized_output(tmp_path, monkeypatch):
    """SQLite 与 PG 同一份种子 → 规范化后完全一致（PG 缺失时本测试 skip）。"""
    if not os.getenv("PARITY_DATABASE_URL"):
        pytest.skip("需要 PostgreSQL")

    monkeypatch.setenv("WEB_CURSOR_HMAC_KEY", "k" * 48)
    from tests.conftest import _make_postgres, _make_sqlite

    def collect(db):
        from panel.server import create_app
        from panel.routes_web import get_db
        app = create_app({"database_url": "unused", "profile": "test"})
        app.dependency_overrides[get_db] = lambda: db
        from fastapi.testclient import TestClient
        c = TestClient(app)
        _seed_canonical(db)
        out, cursor = [], None
        while True:
            p = {"limit": 2}
            if cursor:
                p["cursor"] = cursor
            body = c.get("/api/web/scrapes", params=p).json()["data"]
            out.extend(normalize_row(x) for x in body["items"])
            cursor = body["next_cursor"]
            if not body["has_more"]:
                return out

    import uuid as _uuid
    pg = _make_postgres(f"test_{_uuid.uuid4().hex[:12]}")
    try:
        assert collect(_make_sqlite(tmp_path)) == collect(pg)
    finally:
        pg.conn.close()


# ═══════════ ⑤ 时间一致性 ═══════════
def test_epoch_identical_across_formats(web_api):
    """同一时刻用 Z / +00:00 / naive UTC / epoch 写入 → created_at_ts 必须一致"""
    _, client, db = web_api
    from tests.conftest import canon_iso
    variants = [
        "2025-01-02T03:04:05.123456Z",
        "2025-01-02T03:04:05.123456+00:00",
        datetime(2025, 1, 2, 3, 4, 5, 123456),          # naive → 按 UTC 解释
        1735787045.123456,
    ]
    for i, v in enumerate(variants):
        db.insert_scrape_run(run_id=f"t-{i}", host="example.com", created_at=v)

    ts = {x["created_at_ts"] for x in page(client, limit=10)["items"]}
    assert len(ts) == 1, f"同一时刻写出了多个 epoch: {ts}"

    # 写入归一化：库里存的文本必须是同一种规范格式
    stored = {r["created_at"] for r in
              db.rows("SELECT created_at FROM web_scrape_runs")}
    assert len(stored) == 1 and stored.pop().endswith("Z")


def test_microsecond_precision_survives_paging_cursor(web_api):
    _, client, db = web_api
    for i in range(3):
        db.insert_scrape_run(run_id=f"m-{i}", host="example.com",
                             created_at=datetime(2025, 1, 2, 3, 4, 5, 123456 + i,
                                                 tzinfo=timezone.utc))
    seen = drain(client, limit=1)
    assert seen == ["m-2", "m-1", "m-0"]      # 微秒级差异不被游标抹平
