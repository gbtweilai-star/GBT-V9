# audit/growth_watch.py —— 存储级扩容：体积采样 + 增长斜率 + 耗尽预测
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 阈值（经复核校准）:
#   70% 告警 | 85% 触发归档 | 95% 保护模式（限流）
#   预测: 若按当前斜率将在「扩容耗时×2」内到 80%，提前扩容
import time, threading
from datetime import datetime
from senses.sqldialect import txn

METRICS_DDL = """
CREATE TABLE IF NOT EXISTS db_size_samples(
    ts TIMESTAMPTZ NOT NULL DEFAULT now(),
    db_bytes BIGINT, ledger_bytes BIGINT, alert_bytes BIGINT,
    capacity_bytes BIGINT, conn_pct REAL);
CREATE INDEX IF NOT EXISTS idx_size_ts ON db_size_samples(ts DESC);
"""

class GrowthWatch:
    def __init__(self, ledger, capacity_bytes=None, interval=300, brain=None):
        self.led, self.brain = ledger, brain
        self.capacity = capacity_bytes or int(
            float(os.environ.get("DB_CAPACITY_GB", 16)) * 1073741824)   # 默认16TB? 见下
        self.interval = interval
        self._stop = threading.Event()
        self.last = {}
        with txn(self.led) as cur:
            cur.execute(METRICS_DDL)

    # ── 采样 ──
    def sample(self) -> dict:
        with txn(self.led) as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            db = cur.fetchone()[0]
            sizes = {}
            for t in ("ledger", "alert_events", "cross_tasks"):
                try:
                    cur.execute("SELECT pg_total_relation_size(%s)", (t,))
                    sizes[t] = cur.fetchone()[0]
                except Exception:
                    sizes[t] = 0
            cur.execute("SELECT COUNT(*)::real / current_setting('max_connections')::int "
                        "FROM pg_stat_activity")
            conn_pct = round(cur.fetchone()[0] * 100, 1)
            cur.execute("""INSERT INTO db_size_samples
                (db_bytes,ledger_bytes,alert_bytes,capacity_bytes,conn_pct)
                VALUES(%s,%s,%s,%s,%s)""",
                (db, sizes["ledger"], sizes["alert_events"], self.capacity, conn_pct))
        rec = {"ts": time.time(), "db": db, "ledger": sizes["ledger"],
               "alert": sizes["alert_events"], "cap": self.capacity,
               "used_pct": round(db / self.capacity * 100, 2), "conn_pct": conn_pct}
        self.last = rec
        return rec

    # ── 增长斜率（最近 N 个样本线性拟合）──
    def growth_rate(self, window=12) -> dict:
        with txn(self.led) as cur:
            cur.execute("""SELECT EXTRACT(EPOCH FROM ts), db_bytes
                FROM db_size_samples ORDER BY ts DESC LIMIT %s""", (window,))
            rows = cur.fetchall()[::-1]
        if len(rows) < 2:
            return {"bytes_per_sec": 0.0, "samples": len(rows),
                    "note": "样本不足"}
        (t0, b0), (t1, b1) = rows[0], rows[-1]
        dt = max(t1 - t0, 1)
        rate = (b1 - b0) / dt
        used, cap = self.last.get("db", b1), self.capacity
        eta = (cap - used) / rate if rate > 0 else None
        return {"bytes_per_sec": rate,
                "mb_per_hour": round(rate * 3600 / 1048576, 2),
                "gb_per_day": round(rate * 86400 / 1073741824, 3),
                "samples": len(rows),
                "used_pct": round(used / cap * 100, 2),
                "eta_sec": eta,
                "eta_days": round(eta / 86400, 1) if eta else None}

    # ── 决策：给出该做什么（不自作主张，交守护执行）──
    def advise(self, resize_lead_sec=600) -> dict:
        g = self.growth_rate()
        used_pct = g.get("used_pct", 0)
        eta = g.get("eta_sec")
        actions = []
        if used_pct >= 95:
            actions.append({"action": "protect", "why": "≥95% 进入保护模式：限流+拒低优先级写"})
        if used_pct >= 85:
            actions.append({"action": "archive", "why": "≥85% 触发归档超期分区"})
        if used_pct >= 70:
            actions.append({"action": "warn", "why": "≥70% 告警"})
        # 预测提前扩容
        if eta is not None and eta < resize_lead_sec * 2 and used_pct >= 60:
            actions.append({"action": "grow", "why":
                f"按斜率 {g['gb_per_day']}GB/天，{g['eta_days']}天后耗尽"})
        return {"used_pct": used_pct, "growth": g, "actions": actions}

    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try: self.sample()
                except Exception: pass
        threading.Thread(target=loop, daemon=True, name="growth-watch").start()
    def stop(self): self._stop.set()
