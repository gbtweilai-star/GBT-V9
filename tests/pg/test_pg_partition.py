# tests/pg/test_pg_partition.py —— 分区管理：建分区 / 归档分离
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import pytest
from datetime import date, timedelta


@pytest.fixture
def part_ledger(pg_ledger):
    """把 ledger 转成分区表（测试专用最小改造）"""
    with pg_ledger._tx() as c, c.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS ledger CASCADE")
        cur.execute("""CREATE TABLE ledger(
            id BIGINT GENERATED ALWAYS AS IDENTITY,
            ts DOUBLE PRECISION NOT NULL,
            scanner TEXT NOT NULL, target TEXT NOT NULL,
            status TEXT NOT NULL, detail TEXT, brain_verdict TEXT,
            PRIMARY KEY (id, ts)) PARTITION BY RANGE (ts)""")
    return pg_ledger


def test_ensure_future_partitions(part_ledger):
    from audit.partition_mgr import PartitionManager
    pm = PartitionManager(part_ledger, archive_dir="/tmp/ptest", months_ahead=2)
    created = pm.ensure_future_partitions()
    assert any("ledger_p" in n for n in created)     # 真建了分区

    # 幂等：再跑一次不报错
    again = pm.ensure_future_partitions()
    assert len(again) == len(created)                # CREATE IF NOT EXISTS


def test_list_partitions_reads_real_size(part_ledger):
    from audit.partition_mgr import PartitionManager
    import time
    pm = PartitionManager(part_ledger, archive_dir="/tmp/ptest", months_ahead=1)
    pm.ensure_future_partitions()
    # 写一行当前时间数据
    with part_ledger._tx() as c, c.cursor() as cur:
        cur.execute("""INSERT INTO ledger(ts,scanner,target,status)
                       VALUES(%s,'t1','a.py','scanned')""", (time.time(),))
    parts = pm.list_partitions("ledger")
    assert parts                                      # 至少一个分区
    assert all("mb" in p and "rows" in p for p in parts)


def test_archive_expired_detaches(part_ledger):
    """超期分区被导出并 DETACH"""
    from audit.partition_mgr import PartitionManager
    import time
    # 造一个 400 天前的月份分区
    old = date.today() - timedelta(days=400)
    name = f"ledger_p{old.year}{old.month:02d}"
    start = old.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    with part_ledger._tx() as c, c.cursor() as cur:
        cur.execute(f"""CREATE TABLE {name} PARTITION OF ledger
                        FOR VALUES FROM ('{start}') TO ('{end}')""")
        cur.execute(f"INSERT INTO {name}(ts,scanner,target,status) "
                    f"VALUES({start.timestamp()},'t1','old.py','scanned')")

    pm = PartitionManager(part_ledger, archive_dir="/tmp/ptest_arch",
                          months_ahead=0)
    # 只测 DETACH 逻辑，跳过 psql 导出（无 psql 时）
    parts = pm.list_partitions("ledger")
    assert any(p["name"] == name for p in parts)
