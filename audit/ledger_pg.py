# audit/ledger_pg.py —— PostgreSQL 账本 · 真并发写
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 要点（经复核）:
#   - ThreadedConnectionPool 连接池，每写借连接、短事务、立即归还
#   - event_id UUID UNIQUE 做幂等键，重试复用同一 ID
#   - ON CONFLICT 指定冲突键（不用 SQLite 式宽泛 OR IGNORE）
#   - 复核领取用 FOR UPDATE SKIP LOCKED，事务外做扫描
#   - 绝不在持有事务时调用 LLM / 做网络 IO
import os, time, uuid, json, threading
from enum import Enum
from contextlib import contextmanager
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool

class Status(Enum):
    SCANNED = "scanned"; VULN = "vuln"; BLOCKED = "blocked"; CROSS = "cross"

SCHEMA_FILE = Path(__file__).with_name("schema_pg.sql")

# ─────────────────────────────────────────────────────────────
# 连接池监控 + 服务端探针（面板「账本后端」卡片的数据源）
# ─────────────────────────────────────────────────────────────
import threading
import time as _time
from senses.sqldialect import txn
from core.swallow import swallow as _swallow


class PoolMetrics:
    """线程安全的池/查询读数：借出、等待、超时、慢查询环比。"""

    def __init__(self, pool, maxconn: int, slow_ms: int = 200, slow_ring: int = 50):
        self.pool = pool
        self.maxconn = int(maxconn)
        self.slow_ms = int(slow_ms)
        self._lock = threading.Lock()
        self.total_borrowed = 0
        self.total_waited = 0
        self.wait_ms_sum = 0.0
        self.wait_ms_max = 0.0
        self.timeouts = 0
        self.query_count = 0
        self.query_ms_sum = 0.0
        self.query_ms_max = 0.0
        self.in_flight = 0
        self.peak_in_use = 0
        self._slow: list[dict] = []
        self._slow_ring = slow_ring

    # ── 借用（_tx 入口调用）──
    def on_borrow(self, wait_ms: float, ok: bool) -> None:
        with self._lock:
            if not ok:
                self.timeouts += 1
                return
            self.total_borrowed += 1
            if wait_ms > 5:
                self.total_waited += 1
            self.wait_ms_sum += wait_ms
            self.wait_ms_max = max(self.wait_ms_max, wait_ms)
            self.in_flight += 1
            self.peak_in_use = max(self.peak_in_use, self.in_flight)

    # ── 归还（_tx finally 调用）──
    def on_release(self, query_ms: float | None = None, sql: str = "") -> None:
        with self._lock:
            self.in_flight = max(0, self.in_flight - 1)
            if query_ms is not None:
                self.query_count += 1
                self.query_ms_sum += query_ms
                self.query_ms_max = max(self.query_ms_max, query_ms)
                if query_ms >= self.slow_ms:
                    self._slow.append({"sql": (sql or "")[:300],
                                       "ms": round(query_ms, 1),
                                       "ts": _time.time()})
                    if len(self._slow) > self._slow_ring:
                        self._slow = self._slow[-self._slow_ring:]

    # ── 快照（面板直接吃）──
    def snapshot(self) -> dict:
        with self._lock:
            total = self.pool.maxconn if hasattr(self.pool, "maxconn") else self.maxconn
            # ThreadedConnectionPool 内部池不暴露空闲数 → 用 minconn/maxconn 近似 + 在飞数
            minc = getattr(self.pool, "minconn", 0)
            maxc = getattr(self.pool, "maxconn", total)
            in_use = self.in_flight
            idle = max(0, maxc - in_use)
            util = round(in_use / maxc * 100, 1) if maxc else 0.0
            q_avg = round(self.query_ms_sum / self.query_count, 2) if self.query_count else 0.0
            w_avg = round(self.wait_ms_sum / self.total_borrowed, 2) if self.total_borrowed else 0.0
            return {"minconn": minc, "maxconn": maxc, "in_use": in_use,
                    "idle": idle, "util_pct": util,
                    "peak_in_use": self.peak_in_use,
                    "total_borrowed": self.total_borrowed,
                    "total_waited": self.total_waited,
                    "wait_ms_avg": w_avg, "wait_ms_max": round(self.wait_ms_max, 2),
                    "timeouts": self.timeouts,
                    "query_count": self.query_count,
                    "query_ms_avg": q_avg, "query_ms_max": round(self.query_ms_max, 2),
                    "slow_count": len(self._slow), "slow_ms": self.slow_ms}

    def slow_queries(self, limit: int = 10) -> list[dict]:
        with self._lock:
            return list(reversed(self._slow[-limit:]))


class ServerProbe:
    """服务端真读数：连接占用 / 缓存命中 / 锁等待 / 长事务（PG 专有）。"""

    def __init__(self, dsn: str, long_tx_s: int = 120):
        self.dsn = dsn
        self.long_tx_s = long_tx_s
        self.last: dict = {}
        self._lock = threading.Lock()

    def sample(self) -> dict:
        out: dict = {"ts": _time.time()}
        try:
            conn = psycopg2.connect(self.dsn, connect_timeout=5)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.last = {"ts": _time.time(), "error": f"{type(exc).__name__}: {exc}"}
                return self.last
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT COUNT(*), "
                            "COALESCE(current_setting('max_connections')::int, 0) "
                            "FROM pg_stat_activity")
                conns, maxc = cur.fetchone()
                out["server"] = {"connections": conns, "max_connections": maxc,
                                 "conn_pct": round(conns / maxc * 100, 1) if maxc else 0.0}
                cur.execute("SELECT blks_hit, blks_read FROM pg_stat_database "
                            "WHERE datname = current_database()")
                hit, read = cur.fetchone()
                total = (hit or 0) + (read or 0)
                out["server"]["cache_hit_pct"] = round((hit or 0) / total * 100, 2) if total else 0.0
                cur.execute("SELECT COUNT(*) FROM pg_locks WHERE NOT granted")
                out["blocked_locks"] = cur.fetchone()[0]
                cur.execute("""
                    SELECT pid, COALESCE(wait_event_type,'') AS wt,
                           EXTRACT(EPOCH FROM (now()-query_start)) AS run_s,
                           LEFT(COALESCE(query,''),200) AS q
                    FROM pg_stat_activity
                    WHERE state = 'active' AND pid <> pg_backend_pid()
                      AND query NOT ILIKE '%pg_stat_activity%'
                      AND EXTRACT(EPOCH FROM (now()-query_start)) > %s
                    ORDER BY run_s DESC LIMIT 10""", (self.long_tx_s,))
                out["long_running"] = [
                    {"pid": r[0], "wait_type": r[1], "run_sec": round(r[2], 1), "query": r[3]}
                    for r in cur.fetchall()]
                cur.execute("""
                    SELECT l.pid, EXTRACT(EPOCH FROM (now()-a.query_start)) AS wait_s,
                           LEFT(COALESCE(a.query,''),200) AS q
                    FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                    WHERE NOT l.granted ORDER BY wait_s DESC LIMIT 10""")
                out["locks"] = [{"pid": r[0], "wait_sec": round(r[1] or 0, 1), "query": r[2]}
                                for r in cur.fetchall()]
            out["ok"] = True
        except Exception as exc:  # noqa: BLE001
            out["ok"] = False
            out["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            conn.close()
        with self._lock:
            self.last = out
        return out


class PGLedger:
    def __init__(self, dsn=None, minconn=None, maxconn=None, schema=True,
                 slow_ms=None):
        self.dsn = dsn or os.environ["DATABASE_URL"]
        minconn = int(minconn if minconn is not None
                      else os.environ.get("PG_MINCONN", "2"))
        maxconn = int(maxconn if maxconn is not None
                      else os.environ.get("PG_MAXCONN", "32"))
        self.pool = ThreadedConnectionPool(minconn, maxconn, dsn=self.dsn)
        _slow = int(slow_ms if slow_ms is not None
                    else os.environ.get("PG_SLOW_MS", "200"))
        self.metrics = PoolMetrics(self.pool, maxconn=maxconn, slow_ms=_slow)
        self.probe = ServerProbe(self.dsn,
                                 long_tx_s=int(os.environ.get("PG_LONG_TX_S", "120")))
        self.backend = "pg"
        if schema:
            self._init()

    @contextmanager
    def _tx(self, write=False):
        """事务上下文：记录借池等待与查询耗时（供面板池监控）。"""
        t0 = _time.perf_counter()
        try:
            conn = self.pool.getconn()
        except Exception:
            self.metrics.on_borrow((_time.perf_counter() - t0) * 1000, ok=False)
            raise
        wait_ms = (_time.perf_counter() - t0) * 1000
        self.metrics.on_borrow(wait_ms, ok=True)
        sql_head = ""
        qt0 = _time.perf_counter()
        try:
            if write:
                conn.autocommit = False
            with conn.cursor() as probe_cur:
                sql_head = ""
            yield conn
            if write:
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception as e:
                _swallow(__file__, e)

            raise
        finally:
            self.metrics.on_release((_time.perf_counter() - qt0) * 1000, sql_head)
            try:
                conn.autocommit = True
            except Exception as e:
                _swallow(__file__, e)

            self.pool.putconn(conn)

    def _init(self):
        ddl = SCHEMA_FILE.read_text(encoding="utf-8")
        with self._tx() as c, c.cursor() as cur:
            cur.execute(ddl)

    # ── 写：单条，幂等（event_id 冲突即忽略）──
    def log(self, scanner, target, status, detail="", event_id=None):
        st = status.value if isinstance(status, Status) else str(status)
        with self._tx() as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO ledger(event_id, scanner, target, status, detail)
                VALUES(%s,%s,%s,%s,%s)
                ON CONFLICT (event_id) DO NOTHING""",
                (event_id or uuid.uuid4(), scanner, target, st, detail))

    # ── 批写：executemany，一个事务包住，减少往返 ──
    def log_many(self, rows):
        """rows: [(scanner, target, status, detail), ...]"""
        data = [((uuid.uuid4()), s, t,
                 (st.value if isinstance(st, Status) else str(st)), d)
                for (s, t, st, d) in rows]
        with self._tx() as c, c.cursor() as cur:
            psycopg2.extras.execute_batch(cur,
                "INSERT INTO ledger(event_id, scanner, target, status, detail) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING", data)

    def set_verdict(self, target, verdict, scanner=None):
        sql = "UPDATE ledger SET brain_verdict=%s WHERE target=%s"
        args = [verdict, target]
        if scanner:
            sql += " AND scanner=%s"; args.append(scanner)
        with self._tx() as c, c.cursor() as cur:
            cur.execute(sql, args)

    # ── 读 ──
    def coverage(self, root_files: set) -> float:
        with self._tx() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT target FROM ledger "
                        "WHERE status IN ('scanned','vuln','cross')")
            done = {r[0] for r in cur.fetchall()}
        return len(done & root_files) / max(len(root_files), 1)

    def counts(self) -> dict:
        with self._tx() as c, c.cursor() as cur:
            cur.execute("SELECT status, COUNT(*) FROM ledger GROUP BY status")
            return {s: n for s, n in cur.fetchall()}

    def scanned_by(self, target):
        with self._tx() as c, c.cursor() as cur:
            cur.execute("SELECT DISTINCT scanner FROM ledger WHERE target=%s", (target,))
            return [r[0] for r in cur.fetchall()]

    def by_status(self, status):
        st = status.value if isinstance(status, Status) else str(status)
        with self._tx() as c, c.cursor() as cur:
            cur.execute("SELECT ts, target, detail FROM ledger WHERE status=%s", (st,))
            return cur.fetchall()

    def all_rows(self):
        with self._tx() as c, c.cursor() as cur:
            cur.execute("SELECT ts, scanner, target, status, detail, brain_verdict "
                        "FROM ledger")
            return cur.fetchall()

    def close(self):
        self.pool.closeall()


# ─────────────────────────────────────────────────────────────
# 交叉复核：PG 版领取（FOR UPDATE SKIP LOCKED，天然无锁竞争）
# ─────────────────────────────────────────────────────────────
class PGCrossBoard:
    def __init__(self, ledger: PGLedger, brain=None, run_id=None,
                 lease_sec=300, max_attempts=3):
        self.led, self.brain = ledger, brain
        self.run_id = run_id or uuid.uuid4().hex[:10]
        self.lease_sec, self.max_attempts = lease_sec, max_attempts

    def plan(self, targets, scanners, k=1):
        if not scanners: raise ValueError("无可用触手")
        if k < 0 or k > len(scanners) - 1:
            raise ValueError(f"k={k} 越界，必须 0 <= k <= {len(scanners)-1}")
        if k == 0: return 0
        import random
        load = {s: 0 for s in scanners}
        rows, n = [], 0
        for t in sorted(targets):
            who = self.led.scanned_by(t)
            owner = who[0] if who else None
            if owner is None: continue
            pool = [s for s in scanners if s != owner]
            if not pool: continue
            pool.sort(key=lambda s: (load[s], random.random()))
            for rv in pool[:k]:
                rows.append((self.run_id, t, owner, rv)); load[rv] += 1; n += 1
        if rows:
            with txn(self.led) as cur:
                psycopg2.extras.execute_batch(cur,
                    "INSERT INTO cross_tasks(run_id,target,original_scanner,reviewer) "
                    "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING", rows)
        return n

    def claim(self, reviewer, scan_fn):
        """原子领取：SKIP LOCKED 让多触手各取各的，无阻塞"""
        with txn(self.led) as cur:
            now = "now()"
            # 回收过期租约
            cur.execute("""UPDATE cross_tasks
                SET state='retry', attempts=attempts+1, lease_until=NULL, updated_at=now()
                WHERE run_id=%s AND state='claimed' AND lease_until < now()
                  AND attempts < %s""", (self.run_id, self.max_attempts))
            cur.execute("""UPDATE cross_tasks SET state='blocked', updated_at=now()
                WHERE run_id=%s AND state='retry' AND attempts >= %s""",
                (self.run_id, self.max_attempts))
            # 取一项并锁住，跳过已被别人锁的行
            cur.execute("""SELECT target, original_scanner FROM cross_tasks
                WHERE run_id=%s AND reviewer=%s AND state IN ('pending','retry')
                ORDER BY updated_at
                FOR UPDATE SKIP LOCKED LIMIT 1""", (self.run_id, reviewer))
            row = cur.fetchone()
            if not row:
                return None
            target, owner = row
            cur.execute("""UPDATE cross_tasks
                SET state='claimed', lease_until=now() + (%s || ' seconds')::interval,
                    updated_at=now()
                WHERE run_id=%s AND target=%s AND reviewer=%s""",
                (self.lease_sec, self.run_id, target, reviewer))
        # ── 事务已提交，事务外做扫描（绝不持锁）──
        try:
            reviewed = scan_fn(target)
        except Exception as e:
            self._finish(target, reviewer, None, f"异常:{e}", "retry")
            return {"target": target, "reviewer": reviewer, "state": "retry"}
        original = self._original(target, owner)
        agree = self._fingerprint(original) == self._fingerprint(reviewed)
        self._finish(target, reviewer, reviewed, "", "done")
        if not agree:
            self._arbitrate(target, owner, original, reviewed)
        return {"target": target, "reviewer": reviewer,
                "state": "done", "agree": agree}

    @staticmethod
    def _fingerprint(f):
        if not f: return set()
        out = set()
        for x in (f if isinstance(f, list) else [f]):
            if isinstance(x, dict):
                out.add((str(x.get("rule","")), str(x.get("detail",""))[:160],
                         str(x.get("level",""))))
            else:
                out.add((str(x)[:160], "", ""))
        return out

    def _finish(self, target, reviewer, result, note, state):
        with txn(self.led) as cur:
            cur.execute("""UPDATE cross_tasks SET state=%s, result_json=%s, updated_at=now()
                WHERE run_id=%s AND target=%s AND reviewer=%s""",
                (state, json.dumps(result, ensure_ascii=False) if result is not None else note,
                 self.run_id, target, reviewer))

    def _original(self, target, owner):
        with txn(self.led) as cur:
            cur.execute("SELECT detail FROM ledger WHERE target=%s AND scanner=%s "
                        "AND status='vuln' LIMIT 1", (target, owner))
            r = cur.fetchone()
        return [{"detail": r[0]}] if r else []

    def _arbitrate(self, target, owner, original, reviewed):
        with txn(self.led) as cur:
            cur.execute("""INSERT INTO cross_arbitration
                (run_id,target,original_result,review_result)
                VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (self.run_id, target, json.dumps(original, ensure_ascii=False),
                 json.dumps(reviewed, ensure_ascii=False)))
        verdict = (self.brain.ask(owner, target,
                   f"交叉复核不一致: 原={original} 复核={reviewed}") if self.brain
                   else {"verdict": "disagree", "hint": "待人工"})
        with txn(self.led) as cur:
            cur.execute("""UPDATE cross_arbitration SET verdict=%s, hint=%s, resolved_at=now()
                WHERE run_id=%s AND target=%s""",
                (verdict.get("verdict",""), verdict.get("hint",""), self.run_id, target))
        self.led.set_verdict(target, verdict.get("verdict",""), owner)
