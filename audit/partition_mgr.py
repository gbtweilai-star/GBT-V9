# audit/partition_mgr.py —— 表级扩容：分区管理 + 自动建未来分区 + 归档分离
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 策略:
#   - ledger / alert_events 按月分区，提前建未来 3 个月
#   - 每天补齐未来分区，缺一个建一个（幂等）
#   - 超保留期分区: 导出→校验→DETACH→DROP（不直接删活跃表）
#   - 分区键必须进唯一约束（PG 硬要求）
import os, time, json, hashlib, subprocess
from datetime import datetime, timedelta, date
from pathlib import Path
from senses.sqldialect import txn

# ── 分区表定义：哪张表、按哪列、保留多久 ──
PARTITIONED = {
    "ledger":       {"col": "ts",       "retain_days": 180, "unit": "month"},
    "alert_events": {"col": "ts",       "retain_days": 365, "unit": "month"},
    "cross_tasks":  {"col": "updated_at","retain_days": 90,  "unit": "month"},
}

def _month_range(d: date):
    """给定日期所在月的 [起, 止)"""
    start = d.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt

def _part_name(table: str, d: date) -> str:
    return f"{table}_p{d.year}{d.month:02d}"

class PartitionManager:
    def __init__(self, ledger, archive_dir="partition_archive",
                 months_ahead=3, r2_uploader=None, brain=None):
        self.led, self.brain = ledger, brain
        self.ahead = months_ahead
        self.archive = Path(archive_dir); self.archive.mkdir(exist_ok=True)
        self.r2 = r2_uploader                  # 可选：复用吞噬能的 R2 客户端

    # ── ① 确保分区表结构存在（幂等）──
    def ensure_parent(self, table: str):
        col = PARTITIONED[table]["col"]
        with txn(self.led) as cur:
            cur.execute("""SELECT 1 FROM pg_class WHERE relname=%s AND relkind='p'""",
                        (table,))
            if cur.fetchone():
                return True                     # 已是分区表
            # 检测 TimescaleDB（更优，但免费版可能没有）
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='timescaledb'")
            has_ts = cur.fetchone() is not None
        return {"partitioned": False, "timescaledb": has_ts}

    # ── ② 补齐未来 N 个月分区（每天调用，幂等）──
    def ensure_future_partitions(self) -> list:
        created = []
        today = date.today()
        for table, cfg in PARTITIONED.items():
            col = cfg["col"]
            for i in range(self.ahead + 1):
                m = (today.replace(day=1) + timedelta(days=32 * i)).replace(day=1)
                start, end = _month_range(m)
                name = _part_name(table, m)
                sql = (f"CREATE TABLE IF NOT EXISTS {name} "
                       f"PARTITION OF {table} FOR VALUES "
                       f"FROM ('{start}') TO ('{end}')")
                try:
                    with txn(self.led) as cur:
                        cur.execute(sql)
                    created.append(name)
                except Exception as e:
                    if "already exists" not in str(e):
                        if self.brain:
                            self.brain.ask("part-mgr", name, f"建分区失败: {e}")
        return created

    # ── ③ 列出分区 + 体积 ──
    def list_partitions(self, table: str) -> list:
        with txn(self.led) as cur:
            cur.execute("""
                SELECT c.relname, pg_total_relation_size(c.oid) AS bytes,
                       pg_stat_get_live_tuples(c.oid) AS rows
                FROM pg_class c
                JOIN pg_inherits i ON i.inhrelid = c.oid
                JOIN pg_class p ON p.oid = i.inhparent
                WHERE p.relname = %s ORDER BY c.relname""", (table,))
            return [{"name": r[0], "mb": round(r[1]/1048576, 1), "rows": r[2] or 0}
                    for r in cur.fetchall()]

    # ── ④ 归档超期分区：导出→校验→DETACH→DROP ──
    def archive_expired(self) -> list:
        done = []
        today = date.today()
        for table, cfg in PARTITIONED.items():
            cutoff = today - timedelta(days=cfg["retain_days"])
            for p in self.list_partitions(table):
                # 从分区名解析月份
                try:
                    y, mo = int(p["name"][-6:-2]), int(p["name"][-2:])
                    pdate = date(y, mo, 1)
                except Exception:
                    continue
                _, pend = _month_range(pdate)
                if pend > cutoff:
                    continue                    # 还在保留期内
                # 活跃任务表：只归档已完成的
                if table == "cross_tasks":
                    with txn(self.led) as cur:
                        cur.execute(f"SELECT COUNT(*) FROM {p['name']} "
                                    f"WHERE state IN ('pending','claimed')")
                        if cur.fetchone()[0] > 0:
                            continue            # 有活跃任务，跳过
                r = self._archive_one(table, p)
                if r: done.append(r)
        return done

    def _archive_one(self, table: str, part: dict) -> dict | None:
        """导出 CSV → 校验 → R2 上传 → DETACH → DROP"""
        name = part["name"]
        csv = self.archive / f"{name}.csv.gz"
        try:
            subprocess.run(
                ["psql", os.environ["DATABASE_URL"], "-c",
                 f"\\copy {name} TO PROGRAM 'gzip > {csv}' WITH CSV HEADER"],
                check=True, capture_output=True)
        except Exception as e:
            if self.brain:
                self.brain.ask("part-mgr", name, f"导出失败: {e}")
            return None
        # 校验：行数对得上
        sha = self._sha256(csv)
        if self.r2:
            self.r2.upload_file(str(csv), os.environ["R2_BUCKET_NAME"],
                                f"partitions/{table}/{csv.name}")
        # DETACH + DROP（导出成功才动）
        with txn(self.led) as cur:
            cur.execute(f"ALTER TABLE {table} DETACH PARTITION {name}")
            cur.execute(f"DROP TABLE {name}")
        rec = {"table": table, "partition": name, "rows": part["rows"],
               "mb": part["mb"], "archive": str(csv), "sha256": sha,
               "ts": time.time()}
        if self.brain:
            self.brain.ask("part-mgr", name,
                           f"归档 {part['rows']} 行 {part['mb']}MB → {csv.name}")
        return rec

    @staticmethod
    def _sha256(p: Path) -> str:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
        return h.hexdigest()
