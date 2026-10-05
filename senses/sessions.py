# senses/sessions.py —— 采集会话 + 心跳：为严谨的丢帧率提供 uptime
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 原则: uptime 按心跳证据计算, 相邻心跳间隔 <= timeout 才计入;
#       崩溃后断档部分不计, 避免虚增运行时长稀释丢帧率。
import os, time, uuid, threading
from senses.sqldialect import txn
from audit.ddl import run_script


def _ddl(d):
    from audit.ddl import epoch
    return f"""
    CREATE TABLE IF NOT EXISTS capture_sessions(
        session_id TEXT PRIMARY KEY, tentacle_id TEXT NOT NULL, stream_id TEXT,
        started_at {epoch(d)} NOT NULL, last_heartbeat {epoch(d)}, ended_at {epoch(d)},
        expected_interval {epoch(d)} NOT NULL, frames_captured INTEGER DEFAULT 0,
        state TEXT NOT NULL, end_reason TEXT);
    CREATE INDEX IF NOT EXISTS idx_sessions_tent_start
        ON capture_sessions(tentacle_id, started_at);

    CREATE TABLE IF NOT EXISTS capture_heartbeats(
        session_id TEXT NOT NULL, ts {epoch(d)} NOT NULL,
        frames_captured INTEGER DEFAULT 0,
        PRIMARY KEY(session_id, ts));
    CREATE INDEX IF NOT EXISTS idx_hb_session_ts
        ON capture_heartbeats(session_id, ts);
    """


def ensure_tables(led):
    with txn(led) as cur:
        ddl = _ddl(led.dialect)
        run_script(cur, ddl, led.dialect)


def _ph(led):
    return "?" if led.dialect == "sqlite" else "%s"


class SessionTracker:
    """一根触手一个 tracker；心跳写入与 session 更新同事务"""
    def __init__(self, ledger, tentacle_id, stream_id=None,
                 interval=5.0, timeout=None):
        self.led, self.tid, self.stream = ledger, tentacle_id, stream_id
        self.interval = float(interval)
        self.timeout = float(timeout or max(3 * self.interval, 15.0))
        self.session_id = None
        self._frames = 0
        self._lock = threading.Lock()

    def start(self):
        """开新会话；先把超时未心跳的旧 running 会话标为 interrupted"""
        now = time.time()
        ph = _ph(self.led)
        sid = f"{self.tid}-{uuid.uuid4().hex[:10]}"
        with txn(self.led) as cur:
            # 遗留会话清理：超时未心跳 → interrupted
            cur.execute(f"""UPDATE capture_sessions
                SET state='interrupted', ended_at=last_heartbeat,
                    end_reason='heartbeat timeout'
                WHERE tentacle_id={ph} AND state='running'
                  AND COALESCE(last_heartbeat, started_at) < {ph}""",
                (self.tid, now - self.timeout))
            cur.execute(f"""INSERT INTO capture_sessions
                (session_id,tentacle_id,stream_id,started_at,last_heartbeat,
                 expected_interval,frames_captured,state)
                VALUES({','.join([ph]*8)})""",
                (sid, self.tid, self.stream, now, now, self.interval, 0, "running"))
            cur.execute(f"""INSERT INTO capture_heartbeats
                (session_id,ts,frames_captured) VALUES({','.join([ph]*3)})""",
                (sid, now, 0))
        self.session_id = sid
        return sid

    def beat(self, frames_delta=0):
        """上报一次心跳；与 session 更新同事务"""
        if not self.session_id:
            return
        with self._lock:
            self._frames += int(frames_delta or 0)
            now, ph = time.time(), _ph(self.led)
            with txn(self.led) as cur:
                cur.execute(f"""INSERT INTO capture_heartbeats
                    (session_id,ts,frames_captured) VALUES({','.join([ph]*3)})
                    ON CONFLICT DO NOTHING""" if self.led.dialect != "sqlite" else
                    f"""INSERT OR IGNORE INTO capture_heartbeats
                    (session_id,ts,frames_captured) VALUES({','.join([ph]*3)})""",
                    (self.session_id, now, self._frames))
                cur.execute(f"""UPDATE capture_sessions
                    SET last_heartbeat={ph}, frames_captured={ph}
                    WHERE session_id={ph}""", (now, self._frames, self.session_id))

    def stop(self, reason="closed"):
        if not self.session_id:
            return
        now, ph = time.time(), _ph(self.led)
        with txn(self.led) as cur:
            cur.execute(f"""UPDATE capture_sessions SET state='closed',
                ended_at={ph}, end_reason={ph} WHERE session_id={ph}""",
                (now, reason, self.session_id))
        self.session_id = None

    # ── 自动心跳线程（可选便利）──
    def auto_start(self, frames_provider=None):
        self.start()

        def _loop():
            while self.session_id:
                try:
                    self.beat(frames_provider() if frames_provider else 0)
                except Exception:
                    pass
                time.sleep(self.interval)
        t = threading.Thread(target=_loop, daemon=True, name=f"hb-{self.tid}")
        t.start()
        return t


# ══ uptime 计算：只认有证据的活跃时间 ══
def observed_uptime(led, tentacle_id, start, end, timeout=None):
    """返回窗口内观测到的活跃秒数；心跳数据不可用 → None（不是 0）"""
    ensure_tables(led)
    ph = _ph(led)
    try:
        with txn(led) as cur:
            cur.execute(f"""SELECT h.session_id, h.ts, s.expected_interval
                FROM capture_heartbeats h
                JOIN capture_sessions s ON s.session_id = h.session_id
                WHERE s.tentacle_id={ph} AND h.ts>={ph} AND h.ts<={ph}
                ORDER BY h.session_id, h.ts""",
                (tentacle_id, start - 86400, end + 86400))
            rows = cur.fetchall()
    except Exception:
        return None
    if not rows:
        return 0.0
    # 按会话分组 → 生成 [a,b] 区间（间隔 <= timeout）→ 全局合并 → 求和
    intervals, cur_sid, prev_ts, prev_to = [], None, None, None
    for sid, ts, exp in rows:
        to = float(timeout or max(3 * float(exp or 5), 15.0))
        if sid != cur_sid:
            cur_sid, prev_ts = sid, ts
            continue
        if ts > prev_ts and (ts - prev_ts) <= to:
            intervals.append((prev_ts, ts))
        prev_ts = ts
    # 裁剪到窗口
    intervals = [(max(a, start), min(b, end)) for a, b in intervals]
    intervals = sorted(x for x in intervals if x[1] > x[0])
    total, cur_a, cur_b = 0.0, None, None
    for a, b in intervals:                       # 合并重叠（多会话并发不重复计）
        if cur_b is None:
            cur_a, cur_b = a, b
        elif a <= cur_b:
            cur_b = max(cur_b, b)
        else:
            total += cur_b - cur_a
            cur_a, cur_b = a, b
    if cur_b is not None:
        total += cur_b - cur_a
    return total
