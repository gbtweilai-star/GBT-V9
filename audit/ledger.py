# audit/ledger.py —— SQLite 账本 · 线程安全 · 与 PGLedger 同接口
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 线程安全方案:
#   - threading.local 每线程独立连接（不跨线程共享）
#   - WAL 模式（读不阻塞写）+ busy_timeout 兜写竞争
#   - _tx() 提供与 PGLedger 同签名的事务上下文，线程内可重入
# 后端一致性:
#   - dialect 属性供共享模块（告警/扩容/缓存）做方言分支
#   - 时间统一存 epoch 秒（REAL），与 PG 版保持同语义
import os, time, sqlite3, threading
from contextlib import contextmanager
from enum import Enum
from pathlib import Path


class Status(Enum):
    SCANNED = "scanned"     # 正常扫完
    VULN    = "vuln"        # 疑似漏洞
    BLOCKED = "blocked"     # 卡点，已回主脑
    CROSS   = "cross"       # 被交叉复查


class Ledger:
    def __init__(self, db="tentacle_ledger.db", busy_ms=8000):
        self.path = str(db)
        self.busy_ms = busy_ms
        self._local = threading.local()          # 线程本地连接槽
        self._write_lock = threading.Lock()      # 仅 DDL/批量用
        self.backend = "sqlite"
        self._init()

    # ═══════════════════════════════════════════════════════
    # 方言标识：共享模块据此分支
    # ═══════════════════════════════════════════════════════
    @property
    def dialect(self) -> str:
        return "sqlite"

    # ═══════════════════════════════════════════════════════
    # 连接：每线程一个，WAL + busy_timeout
    # ═══════════════════════════════════════════════════════
    @property
    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "conn", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=self.busy_ms / 1000,
                                isolation_level=None)      # 手动管理事务
            c.execute(f"PRAGMA busy_timeout={self.busy_ms}")
            c.execute("PRAGMA journal_mode=WAL")           # 读不阻塞写
            c.execute("PRAGMA synchronous=NORMAL")         # WAL 下的安全折中
            c.execute("PRAGMA foreign_keys=ON")
            self._local.conn = c
        return c

    # ═══════════════════════════════════════════════════════
    # _tx()：与 PGLedger 同签名的事务上下文
    #   用法: with ledger._tx() as c: c.execute(...)
    #   线程内可重入：嵌套调用不会重复 BEGIN / 提前 COMMIT
    # ═══════════════════════════════════════════════════════
    @contextmanager
    def _tx(self, write=False):
        depth = getattr(self._local, "tx_depth", 0)
        if depth == 0:
            self.conn.execute("BEGIN IMMEDIATE")           # 立即取写锁，防升级死锁
        self._local.tx_depth = depth + 1
        try:
            yield self.conn
            if depth == 0:
                self.conn.execute("COMMIT")
        except Exception:
            if depth == 0:
                try: self.conn.execute("ROLLBACK")
                except sqlite3.OperationalError: pass
            raise
        finally:
            self._local.tx_depth = depth                   # 恢复外层深度

    # ═══════════════════════════════════════════════════════
    # 建表
    # ═══════════════════════════════════════════════════════
    def _init(self):
        with self._write_lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS ledger(
                    ts REAL, scanner TEXT, target TEXT,
                    status TEXT, detail TEXT, brain_verdict TEXT);
                CREATE INDEX IF NOT EXISTS idx_target  ON ledger(target);
                CREATE INDEX IF NOT EXISTS idx_scanner ON ledger(scanner);
                CREATE INDEX IF NOT EXISTS idx_status  ON ledger(status);
            """)

    # ═══════════════════════════════════════════════════════
    # 写：单条 INSERT（busy_timeout + 指数退避兜竞争）
    # ═══════════════════════════════════════════════════════
    def log(self, scanner, target, status, detail="", event_id=None):
        st = status.value if isinstance(status, Status) else str(status)
        for attempt in range(5):
            try:
                with self._tx() as c:
                    c.execute(
                        "INSERT INTO ledger VALUES(?,?,?,?,?,?)",
                        (time.time(), scanner, target, st, detail, ""))
                return
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 4:
                    time.sleep(0.05 * (2 ** attempt))      # 50/100/200/400ms
                    continue
                raise

    # ═══════════════════════════════════════════════════════
    # 批写：一个事务包住多条，减少锁竞争
    # ═══════════════════════════════════════════════════════
    def log_many(self, rows):
        """rows: [(scanner, target, status, detail), ...]"""
        data = [(time.time(), s, t,
                 (st.value if isinstance(st, Status) else str(st)), d, "")
                for (s, t, st, d) in rows]
        with self._tx() as c:
            c.executemany("INSERT INTO ledger VALUES(?,?,?,?,?,?)", data)

    # ═══════════════════════════════════════════════════════
    # 更新：裁决回填
    # ═══════════════════════════════════════════════════════
    def set_verdict(self, target, verdict, scanner=None):
        sql = "UPDATE ledger SET brain_verdict=? WHERE target=?"
        args = [verdict, target]
        if scanner:
            sql += " AND scanner=?"; args.append(scanner)
        with self._tx() as c:
            c.execute(sql, args)

    # ═══════════════════════════════════════════════════════
    # 读
    # ═══════════════════════════════════════════════════════
    def coverage(self, root_files: set) -> float:
        done = {r for (r,) in self.conn.execute(
            "SELECT DISTINCT target FROM ledger "
            "WHERE status IN ('scanned','vuln','cross')")}
        return len(done & root_files) / max(len(root_files), 1)

    def counts(self) -> dict:
        return {st: n for st, n in self.conn.execute(
            "SELECT status, COUNT(*) FROM ledger GROUP BY status")}

    def scanned_by(self, target):
        return [r for (r,) in self.conn.execute(
            "SELECT DISTINCT scanner FROM ledger WHERE target=?", (target,))]

    def by_status(self, status):
        st = status.value if isinstance(status, Status) else str(status)
        return self.conn.execute(
            "SELECT ts, target, detail FROM ledger WHERE status=?", (st,)).fetchall()

    def all_rows(self):
        return self.conn.execute(
            "SELECT ts, scanner, target, status, detail, brain_verdict "
            "FROM ledger").fetchall()

    # ═══════════════════════════════════════════════════════
    # 关闭：只关当前线程的连接
    # ═══════════════════════════════════════════════════════
    def close(self):
        c = getattr(self._local, "conn", None)
        if c:
            try: c.close()
            except Exception: pass
            self._local.conn = None
