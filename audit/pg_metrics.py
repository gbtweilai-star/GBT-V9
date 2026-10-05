# audit/pg_metrics.py —— PG 账本监控：连接池 / 慢查询 / 锁等待
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time, threading, collections
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict

# ─────────────────────────────────────────────────────────────
# ① 池内视角：借还计数 + 等待时间 + 峰值 + 超时
# ─────────────────────────────────────────────────────────────
class PoolMetrics:
    """包在连接池外面，只观测不改语义"""
    def __init__(self, pool, maxconn=32, slow_ms=200):
        self.pool = pool
        self.maxconn = maxconn
        self.slow_ms = slow_ms
        self._lock = threading.Lock()
        self.in_use = 0
        self.peak_in_use = 0
        self.total_borrowed = 0
        self.total_waited = 0
        self.wait_ms_total = 0.0
        self.wait_ms_max = 0.0
        self.timeouts = 0
        self._slow: collections.deque = collections.deque(maxlen=200)
        self._queries: collections.deque = collections.deque(maxlen=500)
        self.query_count = 0
        self.query_ms_total = 0.0

    @contextmanager
    def conn(self):
        t0 = time.perf_counter()
        try:
            c = self.pool.getconn()
        except Exception:
            with self._lock: self.timeouts += 1
            raise
        wait_ms = (time.perf_counter() - t0) * 1000
        with self._lock:
            self.in_use += 1
            self.total_borrowed += 1
            self.peak_in_use = max(self.peak_in_use, self.in_use)
            self.wait_ms_total += wait_ms
            self.wait_ms_max = max(self.wait_ms_max, wait_ms)
            if wait_ms > 5:            # 超过 5ms 视为真等待过
                self.total_waited += 1
        try:
            yield c
        finally:
            with self._lock: self.in_use -= 1
            self.pool.putconn(c)

    def record_query(self, sql, elapsed_ms, ok=True):
        """由 PGLedger 在每条 SQL 后调用"""
        rec = {"sql": " ".join(sql.split())[:160], "ms": round(elapsed_ms, 1),
               "ts": time.time(), "ok": ok}
        with self._lock:
            self.query_count += 1
            self.query_ms_total += elapsed_ms
            self._queries.append(rec)
            if elapsed_ms >= self.slow_ms:
                self._slow.append(rec)

    # ── 池状态快照 ──
    def snapshot(self) -> dict:
        with self._lock:
            idle = getattr(self.pool, "_pool", [])
            idle_n = len(idle) if isinstance(idle, list) else 0
            used_n = self.in_use
            return {
                "in_use": used_n,
                "idle": max(idle_n, self.maxconn - used_n) if idle_n else self.maxconn - used_n,
                "maxconn": self.maxconn,
                "util_pct": round(used_n / self.maxconn * 100, 1) if self.maxconn else 0,
                "peak_in_use": self.peak_in_use,
                "total_borrowed": self.total_borrowed,
                "total_waited": self.total_waited,
                "wait_ms_avg": round(self.wait_ms_total / max(self.total_borrowed, 1), 2),
                "wait_ms_max": round(self.wait_ms_max, 1),
                "timeouts": self.timeouts,
                "query_count": self.query_count,
                "query_ms_avg": round(self.query_ms_total / max(self.query_count, 1), 2),
                "slow_count": len(self._slow),
                "slow_threshold_ms": self.slow_ms,
            }

    def slow_queries(self, limit=20) -> list:
        with self._lock:
            return list(self._slow)[-limit:][::-1]

    def recent_queries(self, limit=30) -> list:
        with self._lock:
            return list(self._queries)[-limit:][::-1]

    def reset_peak(self):
        with self._lock:
            self.peak_in_use = self.in_use


# ─────────────────────────────────────────────────────────────
# ② 服务端视角：pg_stat_activity（活跃/空闲/锁等待）
# ─────────────────────────────────────────────────────────────
class PgProbe:
    """只读探针：从 pg_stat_activity / pg_locks 取服务端真相"""
    def __init__(self, ledger, interval=5.0):
        self.ledger = ledger
        self.interval = interval
        self._stop = threading.Event()
        self.last = {"ts": 0, "server": {}, "locks": [], "long_running": []}

    # 单次采集
    def collect(self) -> dict:
        out = {"ts": time.time()}
        with self.ledger._tx() as c, c.cursor() as cur:
            # 连接状态分布
            cur.execute("""
                SELECT state, COUNT(*) FROM pg_stat_activity
                WHERE datname = current_database() GROUP BY state""")
            states = {s or "unknown": n for s, n in cur.fetchall()}

            # 锁等待：等锁的查询
            cur.execute("""
                SELECT pid, now()-query_start AS dur, wait_event_type, left(query,120)
                FROM pg_stat_activity
                WHERE wait_event_type = 'Lock' AND state <> 'idle'
                ORDER BY query_start""")
            locks = [{"pid": r[0], "wait_sec": r[1].total_seconds(),
                      "wait_type": r[2], "query": r[3]} for r in cur.fetchall()]

            # 长事务/慢查询（>2s）
            cur.execute("""
                SELECT pid, now()-query_start AS dur, state, left(query,120)
                FROM pg_stat_activity
                WHERE state <> 'idle' AND now()-query_start > interval '2 seconds'
                ORDER BY query_start DESC LIMIT 20""")
            longr = [{"pid": r[0], "dur_sec": r[1].total_seconds(),
                      "state": r[2], "query": r[3]} for r in cur.fetchall()]

            # 当前连接总数 vs 上限
            cur.execute("SELECT COUNT(*), current_setting('max_connections')::int "
                        "FROM pg_stat_activity")
            conns, maxconn = cur.fetchone()

            # 缓存命中率（账本读多，这个指标很有用）
            cur.execute("""SELECT blks_hit, blks_read FROM pg_stat_database
                           WHERE datname = current_database()""")
            hit, read = cur.fetchone()
            cache_pct = round(hit / max(hit + read, 1) * 100, 2)

        out["server"] = {
            "states": states,
            "connections": conns, "max_connections": maxconn,
            "conn_pct": round(conns / max(maxconn, 1) * 100, 1),
            "cache_hit_pct": cache_pct,
        }
        out["locks"] = locks
        out["long_running"] = longr
        self.last = out
        return out

    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try: self.collect()
                except Exception: pass
        threading.Thread(target=loop, daemon=True).start()

    def stop(self): self._stop.set()
