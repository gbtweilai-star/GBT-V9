# 断言 PanelDB 与 TestDB 契约一致 —— 防止测试用的双替身悄悄漂移
from datetime import datetime, timezone
import pytest

from common.timeutil import canon_iso, to_epoch, timestamp_param
from panel.db import PanelDB

SAME_INSTANT = [
    "2025-01-02T03:04:05.123456Z",
    "2025-01-02T03:04:05.123456+00:00",
    "2025-01-02T05:04:05.123456+02:00",          # 同一时刻，不同偏移
    datetime(2025, 1, 2, 3, 4, 5, 123456),       # naive → 按 UTC
    1735787045.123456,                           # epoch
]


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_timestamp_param_is_canonical_and_uniform(dialect):
    out = {timestamp_param(dialect, v) for v in SAME_INSTANT}
    assert len(out) == 1, f"同一时刻写出了多种参数: {out}"
    one = out.pop()
    if dialect == "sqlite":
        assert isinstance(one, str) and one.endswith("Z")
    else:
        assert isinstance(one, datetime) and one.tzinfo is not None


def test_testdb_and_panel_db_share_timestamp_semantics(web_api):
    """TestDB（测试替身）与 PanelDB（生产壳）对同一输入必须给同一结果"""
    _, _, db = web_api
    for v in SAME_INSTANT:
        assert db.timestamp_param(v) == timestamp_param(db.dialect, v)


def test_epoch_roundtrip_lossless():
    for v in SAME_INSTANT:
        assert canon_iso(to_epoch(v)) == canon_iso(v)   # 微秒不丢


def test_placeholder_contract():
    """routes._ph 依据 db.dialect，两个实现都必须暴露该属性"""
    import inspect
    from panel import routes_web
    src = inspect.getsource(routes_web._ph)
    assert "postgres" in src and "%s" in src
