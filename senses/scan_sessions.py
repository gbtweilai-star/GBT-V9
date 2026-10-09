# senses/scan_sessions.py —— 扫描会话 + 心跳：为扫描事件/小时提供严谨时长
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计:
#   - 不动 capture_sessions(避免影响采集数据), 新增扫描专用表
#   - 一轮扫描一个 run_id; 每触手每分配一个 session
#   - 短任务靠 started_at/ended_at 标记; 存活>interval 才发心跳
#   - 速率分母 = 该触手会话活跃区间并集(不重复计时, 不拿采集 uptime 冒充)
#   - 活跃<MIN_ACTIVE 或 关联事件<MIN_EVENTS → 速率给 null(样本不足)
from core.swallow import swallow as _swallow
import os, time, uuid, threading

from senses.sqldialect import txn

MIN_ACTIVE_SEC = float(os.environ.get("SCAN_MIN_ACTIVE_SEC", 300))
MIN_EVENTS = int(os.environ.get("SCAN_MIN_EVENTS", 5))


def _ddl(d):
    from audit.ddl import epoch
    return f"""
    CREATE TABLE IF NOT EXISTS scan_sessions(
        session_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, trace_id TEXT,
        tentacle_id TEXT NOT NULL, started_at {epoch(d)} NOT NULL,
        last_heartbeat {epoch(d)} NOT NULL, ended_at {epoch(d)},
        expected_interval {epoch(d)} NOT NULL, state TEXT NOT NULL,
        end_reason TEXT);
    CREATE INDEX IF NOT EXISTS idx_scan_sess_tent_start
        ON scan_sessions(tentacle_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_scan_sess_run ON scan_sessions(run_id);

    CREATE TABLE IF NOT EXISTS scan_heartbeats(
        session_id TEXT NOT NULL, ts {epoch(d)} NOT NULL,
        work_units INTEGER DEFAULT 0,
        PRIMARY KEY(session_id, ts));
    CREATE INDEX IF NOT EXISTS idx_scan_hb_sess_ts
        ON scan_heartbeats(session_id, ts);
    """


def ensure_tables(led):
    with txn(led) as cur:
        from audit.ddl import run_script
        run_script(cur, _ddl(led.dialect), led.dialect)


def ensure_ledger_column(led):
    """给 ledger 补 scan_session_id 列（幂等）"""
    try:
        with txn(led) as cur:
            if led.dialect == "sqlite":
                cur.execute("PRAGMA table_info(ledger)")
                cols = [r[1] for r in cur.fetchall()]
                if "scan_session_id" not in cols:
                    cur.execute("ALTER TABLE ledger ADD COLUMN scan_session_id TEXT")
            else:
                cur.execute("SELECT column_name FROM information_schema.columns "
                            "WHERE table_name='ledger'")
                cols = [r[0] for r in cur.fetchall()]
                if "scan_session_id" not in cols:
                    cur.execute("ALTER TABLE ledger ADD COLUMN scan_session_id TEXT")
    except Exception as e:
        print("[scan-sessions] ledger 列迁移跳过:", e)


def _ph(led):
    return "?" if led.dialect == "sqlite" else "%s"


class ScanSession:
    """一次扫描分配（一触手一 session）；用 with 保证 finally 关闭"""
    def __init__(self, ledger, run_id, tentacle_id, trace_id=None,
                 interval=5.0, timeout=None):
        self.led, self.run_id = ledger, run_id
        self.tid, self.trace_id = tentacle_id, trace_id
        self.interval = float(interval)
        self.timeout = float(timeout or max(3 * self.interval, 15.0))
        self.session_id = None
        self._units = 0
        self._stop = threading.Event()
        self._hb = None

    def __enter__(self):
        ensure_tables(self.led)
        now, ph = time.time(), _ph(self.led)
        sid = f"scan-{self.tid}-{uuid.uuid4().hex[:10]}"
        with txn(self.led) as cur:
            # 遗留 running 会话超时 → interrupted
            cur.execute(f"""UPDATE scan_sessions SET state='interrupted',
                ended_at=COALESCE(ended_at,last_heartbeat),
                end_reason='heartbeat timeout'
                WHERE tentacle_id={ph} AND state='running'
                  AND last_heartbeat < {ph}""", (self.tid, now - self.timeout))
            cur.execute(f"""INSERT INTO scan_sessions
                (session_id,run_id,trace_id,tentacle_id,started_at,last_heartbeat,
                 expected_interval,state) VALUES({','.join([ph]*8)})""",
                (sid, self.run_id, self.trace_id, self.tid, now, now,
                 self.interval, "running"))
            cur.execute(f"""INSERT INTO scan_heartbeats(session_id,ts,work_units)
                VALUES({','.join([ph]*3)})""", (sid, now, 0))
        self.session_id = sid
        # 长任务才有心跳：线程先等一个 interval，短任务会在那之前退出
        self._hb = threading.Thread(target=self._loop, daemon=True,
                                    name=f"scanhb-{self.tid}")
        self._hb.start()
        return self

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                self.beat()
            except Exception as e:
                _swallow(__file__, e)

    def beat(self, work_units=None):
        if not self.session_id:
            return
        if work_units is not None:
            self._units = int(work_units)
        now, ph = time.time(), _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""INSERT INTO scan_heartbeats(session_id,ts,work_units)
                VALUES({','.join([ph]*3)}) ON CONFLICT DO NOTHING"""
                if self.led.dialect != "sqlite" else
                f"""INSERT OR IGNORE INTO scan_heartbeats(session_id,ts,work_units)
                VALUES({','.join([ph]*3)})""",
                (self.session_id, now, self._units))
            cur.execute(f"""UPDATE scan_sessions SET last_heartbeat={ph}
                WHERE session_id={ph}""", (now, self.session_id))

    def done_one(self):
        """每扫完一个目标调一次，用于进度心跳"""
        self._units += 1
        now = time.time()
        # 只在超过 interval 未心跳时才写，避免短任务堆行
        try:
            with txn(self.led) as cur:
                cur.execute(f"SELECT last_heartbeat FROM scan_sessions "
                            f"WHERE session_id={_ph(self.led)}", (self.session_id,))
                r = cur.fetchone()
                if r and now - float(r[0]) >= self.interval:
                    self.beat()
        except Exception as e:
            _swallow(__file__, e)

    def __exit__(self, exc_type, exc, tb):
        self._stop.set()
        now, ph = time.time(), _ph(self.led)
        reason = "closed" if exc_type is None else f"error:{exc_type.__name__}"
        try:
            with txn(self.led) as cur:
                cur.execute(f"""UPDATE scan_sessions SET state={ph},
                    ended_at={ph}, end_reason={ph} WHERE session_id={ph}""",
                    ("closed" if exc_type is None else "interrupted",
                     now, reason, self.session_id))
        except Exception as e:
            _swallow(__file__, e)
        self.session_id = None
        return False


# ══ 扫描活跃时长（并集，不重复计时）══
def observed_scan_seconds(led, tentacle_id, start, end, timeout=None):
    """返回窗口内该触手扫描活跃秒数；表不可用→None（不是0）"""
    ensure_tables(led)
    ph = _ph(led)
    try:
        with txn(led) as cur:
            cur.execute(f"""SELECT session_id, started_at, ended_at,
                                   expected_interval
                            FROM scan_sessions
                            WHERE tentacle_id={ph} AND started_at<{ph}
                              AND COALESCE(ended_at, 9e18)>{ph}""",
                        (tentacle_id, end, start))
            sess = cur.fetchall()
            if not sess:
                return 0.0
            ids = [s[0] for s in sess]
            marks = {i: [] for i in ids}
            cur.execute(f"""SELECT session_id, ts FROM scan_heartbeats
                            WHERE ts>={ph} AND ts<={ph}""", (start - 86400, end + 86400))
            for sid, ts in cur.fetchall():
                if sid in marks:
                    marks[sid].append(float(ts))
    except Exception:
        return None

    intervals = []
    for sid, st, en, exp in sess:
        to = float(timeout or max(3 * float(exp or 5), 15.0))
        pts = sorted(set([float(st)] + marks.get(sid, []) +
                         ([float(en)] if en is not None else [])))
        for a, b in zip(pts, pts[1:]):
            if 0 < (b - a) <= to:                 # 断档不计
                intervals.append((max(a, start), min(b, end)))
    intervals = sorted(x for x in intervals if x[1] > x[0])
    total, ca, cb = 0.0, None, None
    for a, b in intervals:                        # 合并重叠 → 不重复计时
        if cb is None:
            ca, cb = a, b
        elif a <= cb:
            cb = max(cb, b)
        else:
            total += cb - ca; ca, cb = a, b
    if cb is not None:
        total += cb - ca
    return total


def scan_rate(led, tentacle_id, start, end, linked_events):
    """严谨的 事件/小时；样本不足返回 (None, reason)"""
    sec = observed_scan_seconds(led, tentacle_id, start, end)
    if sec is None:
        return None, "扫描会话数据不可用"
    if sec < MIN_ACTIVE_SEC:
        return None, f"活跃时长不足（{sec:.0f}s < {MIN_ACTIVE_SEC:.0f}s）"
    if (linked_events or 0) < MIN_EVENTS:
        return None, f"关联事件不足（{linked_events or 0} < {MIN_EVENTS}）"
    return round((linked_events or 0) / (sec / 3600), 2), ""
