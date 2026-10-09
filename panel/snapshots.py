# panel/snapshots.py —— 健康快照：写入 / 幂等 / 历史曲线
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 快照必须存 algorithm_version + 原始指标 + 证据, 否则调阈值后历史无法解释。
from core.swallow import swallow as _swallow
import os, json, time, threading
from panel.health import tentacle_health
from senses.sqldialect import txn
from audit.ddl import run_script

ALGO_VERSION = "health-1.0"
RETENTION_DAYS = int(os.environ.get("HEALTH_RETENTION_DAYS", 400))


def _ddl(d):
    from audit.ddl import epoch
    return f"""
    CREATE TABLE IF NOT EXISTS health_snapshots(
        tentacle_id TEXT NOT NULL, bucket_start {epoch(d)} NOT NULL,
        window_seconds INTEGER NOT NULL, grade TEXT NOT NULL,
        metrics TEXT NOT NULL, evidence TEXT NOT NULL,
        algorithm_version TEXT NOT NULL,
        PRIMARY KEY(tentacle_id, bucket_start, window_seconds));
    CREATE INDEX IF NOT EXISTS idx_hs_bucket ON health_snapshots(bucket_start);
    CREATE INDEX IF NOT EXISTS idx_hs_tent_bucket
        ON health_snapshots(tentacle_id, bucket_start);
    """


def ensure_table(led):
    with txn(led) as cur:
        ddl = _ddl(led.dialect)
        run_script(cur, ddl, led.dialect)


def record_snapshot(led, window_hours=1, bucket=None):
    """当前窗口 → 每触手一条快照；幂等 upsert。返回写入条数。"""
    ensure_table(led)
    now = time.time()
    win = int(window_hours * 3600)
    bstart = int(bucket if bucket is not None else (now // win) * win)
    data = tentacle_health(led, hours=window_hours)
    n = 0
    ph = "?" if led.dialect == "sqlite" else "%s"
    for t in data.get("tentacles", []):
        args = (t["tentacle_id"], bstart, win, t["grade"],
                json.dumps(t["metrics"], ensure_ascii=False),
                json.dumps(t["evidence"], ensure_ascii=False), ALGO_VERSION)
        sql = (f"""INSERT INTO health_snapshots
            (tentacle_id,bucket_start,window_seconds,grade,metrics,evidence,
             algorithm_version) VALUES({','.join([ph]*7)})
            ON CONFLICT(tentacle_id,bucket_start,window_seconds) DO UPDATE SET
            grade=excluded.grade, metrics=excluded.metrics,
            evidence=excluded.evidence, algorithm_version=excluded.algorithm_version"""
            if led.dialect == "sqlite" else
            f"""INSERT INTO health_snapshots
            (tentacle_id,bucket_start,window_seconds,grade,metrics,evidence,
             algorithm_version) VALUES({','.join([ph]*7)})
            ON CONFLICT(tentacle_id,bucket_start,window_seconds) DO UPDATE SET
            grade=EXCLUDED.grade, metrics=EXCLUDED.metrics,
            evidence=EXCLUDED.evidence, algorithm_version=EXCLUDED.algorithm_version""")
        try:
            with txn(led) as cur:
                cur.execute(sql, args)
            n += 1
        except Exception as e:
            _swallow(__file__, e)
    _reap(led)
    return n


def _reap(led):
    cutoff = time.time() - RETENTION_DAYS * 86400
    ph = "?" if led.dialect == "sqlite" else "%s"
    try:
        with txn(led) as cur:
            cur.execute(f"DELETE FROM health_snapshots WHERE bucket_start<{ph}",
                        (cutoff,))
    except Exception as e:
        _swallow(__file__, e)


def health_history(led, hours=168, bucket_hours=1):
    """等级分布（堆叠）+ 每触手等级时间线"""
    ensure_table(led)
    end, start = int(time.time()), int(time.time()) - int(hours * 3600)
    ph = "?" if led.dialect == "sqlite" else "%s"
    step = int(bucket_hours * 3600)
    with txn(led) as cur:
        cur.execute(f"""SELECT tentacle_id, bucket_start, grade, metrics, evidence
                        FROM health_snapshots
                        WHERE bucket_start>={ph} AND bucket_start<{ph}
                        ORDER BY bucket_start""", (start, end))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    GRADES = ["异常", "亚健康", "关注", "健康", "样本不足"]
    buckets = list(range((start // step) * step, end, step))
    idx = {b: i for i, b in enumerate(buckets)}
    # 等级分布（每桶各等级触手数）
    dist = {g: [0] * len(buckets) for g in GRADES}
    # 每触手时间线
    tl = {}
    for r in rows:
        i = idx.get(int(r["bucket_start"]))
        if i is None:
            continue
        dist.setdefault(r["grade"], [0] * len(buckets))[i] += 1
        tl.setdefault(r["tentacle_id"], {})[i] = {
            "grade": r["grade"], "metrics": r["metrics"], "evidence": r["evidence"]}
    series = []
    for tid in sorted(tl):
        series.append({"tentacle_id": tid, "points": [
            {"start": buckets[i], "grade": (tl[tid].get(i) or {}).get("grade"),
             "metrics": (tl[tid].get(i) or {}).get("metrics"),
             "evidence": (tl[tid].get(i) or {}).get("evidence")}
            for i in range(len(buckets))]})
    return {"bucket_seconds": step, "buckets": buckets, "grade_order": GRADES,
            "distribution": dist, "series": series,
            "has_history": bool(rows), "algorithm_version": ALGO_VERSION}


class SnapshotDaemon:
    """每小时 UTC 写一次快照"""
    def __init__(self, ledger, window_hours=1, interval=None):
        self.led, self.win = ledger, window_hours
        self.interval = interval or 3600
        self._stop = threading.Event(); self._t = None

    def start(self):
        if not (self._t and self._t.is_alive()):
            self._stop.clear()
            self._t = threading.Thread(target=self._loop, daemon=True,
                                       name="health-snap")
            self._t.start()
        return self

    def _loop(self):
        try:
            record_snapshot(self.led, self.win)     # 启动先补一次
        except Exception as e:
            print("[health-snap] 初写失败:", e)
        while not self._stop.is_set():
            self._stop.wait(self.interval)
            try:
                record_snapshot(self.led, self.win)
            except Exception as e:
                print("[health-snap] 写入失败:", e)

    def stop(self):
        self._stop.set()
