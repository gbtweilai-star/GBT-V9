# tests/pg/test_pg_growth.py —— 增长监控 + 耗尽预测 + 扩容建议
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time
import pytest


def test_growth_sample_and_rate(pg_ledger):
    from audit.growth_watch import GrowthWatch
    gw = GrowthWatch(pg_ledger, capacity_bytes=10 * 1073741824)  # 10GB 假容量
    s = gw.sample()
    assert s["db"] > 0                                # 真读到 pg_database_size
    assert 0 <= s["used_pct"] <= 100

    # 手工塞两条样本，验证斜率计算
    with pg_ledger._tx() as c, c.cursor() as cur:
        now = time.time()
        cur.execute("""INSERT INTO db_size_samples(ts,db_bytes,capacity_bytes,conn_pct)
                       VALUES(to_timestamp(%s),%s,%s,0),(to_timestamp(%s),%s,%s,0)""",
                    (now - 3600, 1_000_000_000, 10_737_418_240,
                     now, 1_500_000_000, 10_737_418_240))
    g = gw.growth_rate(window=5)
    assert g["bytes_per_sec"] > 0                     # 有增长
    assert g["gb_per_day"] > 0
    assert g["eta_days"] is not None                  # 算出耗尽天数


def test_growth_advise_thresholds(pg_ledger):
    from audit.growth_watch import GrowthWatch
    gw = GrowthWatch(pg_ledger, capacity_bytes=1_000_000_000)  # 极小容量
    gw.last = {"db": 960_000_000}                     # 假装已用 96%
    adv = gw.advise()
    actions = [a["action"] for a in adv["actions"]]
    assert "protect" in actions                       # ≥95% 触发保护
    assert "archive" in actions                       # ≥85% 触发归档
    assert "warn" in actions                          # ≥70% 告警


def test_growth_advise_predictive_grow(pg_ledger):
    """预测提前扩容：斜率陡 + 用量够高 → 建议 grow"""
    from audit.growth_watch import GrowthWatch
    gw = GrowthWatch(pg_ledger, capacity_bytes=1_000_000_000)
    with pg_ledger._tx() as c, c.cursor() as cur:
        now = time.time()
        cur.execute("""INSERT INTO db_size_samples(ts,db_bytes,capacity_bytes,conn_pct)
                       VALUES(to_timestamp(%s),%s,%s,0),(to_timestamp(%s),%s,%s,0)""",
                    (now - 600, 600_000_000, 1_000_000_000,
                     now, 700_000_000, 1_000_000_000))   # 10min 涨 100MB
    gw.sample()                                       # 刷新 last
    gw.last = {"db": 700_000_000}
    adv = gw.advise(resize_lead_sec=600)
    assert any(a["action"] == "grow" for a in adv["actions"])


def test_growth_sample_table_created(pg_ledger):
    from audit.growth_watch import GrowthWatch
    GrowthWatch(pg_ledger).sample()
    with pg_ledger._tx() as c, c.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM db_size_samples")
        assert cur.fetchone()[0] >= 1
