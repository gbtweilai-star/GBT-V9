# senses/gap_alerts.py —— 丢帧 → 告警状态机 + 语音播报（outbox 异步桥）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计:
#   采集线程只写 outbox（与 gap 行同事务），绝不等 DB 之外的 IO 或语音；
#   worker 线程排空 outbox → observe_incident（幂等）→ 仅 ACTIVE 边沿播报；
#   backfill() 补偿崩溃窗口：扫 gap 表里还没进 outbox 的行。
import os, time, json, sqlite3, threading
from dataclasses import dataclass
from panel.pipelines import source_status, _cols, _pick   # 复用防御式取数
from senses.sqldialect import txn


@dataclass
class GapEvent:
    gap_id: str
    tentacle_id: str
    frame_start: int
    frame_end: int
    missing_frames: int
    ts: float
    trace_id: str | None = None
    stream_id: str | None = None


def _outbox_ddl(dialect):
    from audit.ddl import pk, epoch, bool_t
    d = dialect
    return f"""
    CREATE TABLE IF NOT EXISTS gap_alert_outbox(
        gap_id TEXT PRIMARY KEY, tentacle_id TEXT NOT NULL, stream_id TEXT,
        trace_id TEXT, frame_start INTEGER, frame_end INTEGER,
        missing INTEGER, ts {epoch(d)} NOT NULL,
        processed {bool_t(d)} DEFAULT {"0" if d=="sqlite" else "false"},
        error TEXT);
    CREATE INDEX IF NOT EXISTS idx_outbox_pending
        ON gap_alert_outbox(processed, ts);
    """


class GapAlertBridge:
    def __init__(self, ledger, alerts, voice=None,
                 poll_interval=2.0, batch=200,
                 min_critical_frames=None):
        self.led, self.alerts, self.voice = ledger, alerts, voice
        self.interval, self.batch = poll_interval, batch
        self.min_frames = int(min_critical_frames or
                              os.environ.get("GAP_CRITICAL_MIN_FRAMES", 1))
        self._stop = threading.Event()
        self._t = None
        self._init()

    # ── 建表（幂等）──
    def _init(self):
        with txn(self.led) as cur:
            from audit.ddl import run_script
            run_script(cur, _outbox_ddl(self.led.dialect), self.led.dialect)

    # ── 采集线程调用：与 gap 行同事务写 outbox（不阻塞）──
    def on_gap_committed(self, gap: GapEvent, cur=None):
        """cur 可由调用方传入，实现与 gap 插入同事务"""
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        args = (gap.gap_id, gap.tentacle_id, gap.stream_id, gap.trace_id,
                gap.frame_start, gap.frame_end, gap.missing_frames, gap.ts)
        sql = (f"INSERT INTO gap_alert_outbox"
               f"(gap_id,tentacle_id,stream_id,trace_id,frame_start,frame_end,"
               f" missing,ts) VALUES("
               f"{','.join([ph]*8)}) ON CONFLICT DO NOTHING"
               if self.led.dialect in ("pg", "postgresql") else
               f"INSERT OR IGNORE INTO gap_alert_outbox"
               f"(gap_id,tentacle_id,stream_id,trace_id,frame_start,frame_end,"
               f" missing,ts) VALUES({','.join([ph]*8)})")
        if cur is not None:                    # 同事务
            cur.execute(sql, args)
            return
        with txn(self.led) as cur2:
            cur2.execute(sql, args)

    # ── worker：排空 outbox ──
    def _pending(self):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        false = "0" if self.led.dialect == "sqlite" else "false"
        with txn(self.led) as cur:
            cur.execute(f"SELECT gap_id,tentacle_id,stream_id,trace_id,frame_start,"
                        f"frame_end,missing,ts FROM gap_alert_outbox "
                        f"WHERE processed={false} ORDER BY ts LIMIT {self.batch}")
            return cur.fetchall()

    def _mark(self, gap_id, error=None):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        one = "1" if self.led.dialect == "sqlite" else "true"
        if error:
            # 失败行不标 processed，留待补偿重试
            with txn(self.led) as cur:
                cur.execute(f"UPDATE gap_alert_outbox SET error={ph} WHERE gap_id={ph}",
                            (error[:500], gap_id))
            return
        with txn(self.led) as cur:
            cur.execute(f"UPDATE gap_alert_outbox SET processed={one}, error=NULL "
                        f"WHERE gap_id={ph}", (gap_id,))

    def _handle(self, row):
        (gap_id, tid, stream, trace_id, f0, f1, missing, ts) = row
        missing = missing or 1
        level = "critical" if missing >= self.min_frames else "warning"
        key = f"devour.gap:{tid}" + (f":{stream}" if stream else "")
        res = self.alerts.observe_incident(
            alert_key=key, occurrence_id=gap_id, level=level, value=missing,
            detail={"gap_id": gap_id, "trace_id": trace_id,
                    "frame_start": f0, "frame_end": f1, "tentacle": tid,
                    "stream_id": stream})
        # 仅 ACTIVE 边沿播报一次；绝不在事务里调 TTS
        if res.transition == "fired" and self.voice:
            try:
                self.voice.say_alert({
                    "level": level,
                    "label": f"吞噬能丢帧，触手 {tid}",
                    "value": int(res.episode_total), "unit": "帧",
                    "episode": res.episode_id,          # 语音去重键
                    "trace_id": trace_id,
                })
            except Exception as e:
                print("[gap-alert] 播报失败(不影响告警):", e)

    def _drain_once(self):
        n = 0
        for row in self._pending():
            try:
                self._handle(row)
                self._mark(row[0])
                n += 1
            except Exception as e:
                self._mark(row[0], error=f"{type(e).__name__}: {e}")
        return n

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._drain_once()
            except Exception as e:
                print("[gap-alert] worker 异常:", e)
            self._stop.wait(self.interval)

    def start(self):
        if self._t and self._t.is_alive():
            return self
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True,
                                   name="gap-alert")
        self._t.start()
        return self

    def stop(self):
        self._stop.set()

    # ── 补偿：崩溃窗口内 gap 行没进 outbox 的补上 ──
    def backfill(self):
        st = source_status(self.led)
        info = st.get("gap", {})
        if not info.get("available"):
            return 0
        t = info["table"]
        cols = _cols(self.led, t)
        idc = _pick(cols, ("gap_id", "id"))
        if not idc:
            return 0
        tent = _pick(cols, ("tentacle_id", "tentacle")) or "tentacle_id"
        f0 = _pick(cols, ("frame_no", "frame_start")) or idc
        miss = _pick(cols, ("missing", "count")) or "1"
        tsc = _pick(cols, ("ts", "created_at")) or idc
        trc = "trace_id" if "trace_id" in cols else "NULL"
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        sql = f"""SELECT g.{idc}, g.{tent}, g.{trc}, g.{f0}, g.{f0}, g.{miss}, g.{tsc}
                  FROM {t} g
                  LEFT JOIN gap_alert_outbox o ON o.gap_id = g.{idc}
                  WHERE o.gap_id IS NULL"""
        with txn(self.led) as cur:
            cur.execute(sql)
            rows = cur.fetchall()
            for r in rows:
                self.on_gap_committed(GapEvent(
                    gap_id=str(r[0]), tentacle_id=str(r[1]), trace_id=r[2],
                    frame_start=r[3], frame_end=r[4], missing_frames=r[5] or 1,
                    ts=r[6] or time.time()), cur=cur)
        return len(rows)

    # ── 恢复：完整校验帧段通过时调用 ──
    def on_verified_clean_segment(self, tentacle_id, trace_id=None,
                                  segment_id=None, stream_id=None):
        key = f"devour.gap:{tentacle_id}" + (f":{stream_id}" if stream_id else "")
        res = self.alerts.recover_incident(
            key, detail={"trace_id": trace_id, "segment_id": segment_id,
                         "note": "采集已恢复；此前缺失帧不可恢复"})
        if res.transition == "recovered" and self.voice and \
                os.environ.get("GAP_RECOVER_TTS") == "1":     # 默认不播恢复，防刷屏
            try:
                self.voice.say_alert({"level": "info", "label": "吞噬能已恢复",
                                      "value": int(res.episode_total), "unit": "帧",
                                      "episode": res.episode_id})
            except Exception:
                pass
        return res

    def status(self):
        ph = "?" if self.led.dialect == "sqlite" else "%s"
        false = "0" if self.led.dialect == "sqlite" else "false"
        with txn(self.led) as cur:
            cur.execute(f"SELECT COUNT(*) FROM gap_alert_outbox "
                        f"WHERE processed={false}")
            pending = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM gap_alert_outbox WHERE error IS NOT NULL")
            failed = cur.fetchone()[0]
        return {"worker_alive": bool(self._t and self._t.is_alive()),
                "interval": self.interval, "pending": pending, "failed": failed,
                "min_critical_frames": self.min_frames}
