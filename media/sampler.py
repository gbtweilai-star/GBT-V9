# media/sampler.py —— 定时采样：真实读数入 media_monitor_samples
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 采样失败 → 写 NULL + sources.unavailable, 绝不写 0;
#       对齐时间桶 + ON CONFLICT(ts) DO UPDATE (两后端兼容)
import time, json, threading
from senses.sqldialect import txn
from core.swallow import swallow as _swallow


def alias_ph(led):
    return "?" if led.dialect == "sqlite" else "%s"


def migrate(led):
    """幂等迁移：补齐 backoff/failure 列（旧表升级）"""
    want = ["backoff_count INTEGER", "next_retry_in REAL",
            "failure_rate REAL", "failure_attempts INTEGER"]
    with txn(led) as cur:
        if led.dialect == "sqlite":
            cur.execute("PRAGMA table_info(media_monitor_samples)")
            have = {r[1] for r in cur.fetchall()}
            for w in want:
                name = w.split()[0]
                if name not in have:
                    cur.execute(f"ALTER TABLE media_monitor_samples ADD COLUMN {w}")
        else:
            cur.execute("SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='media_monitor_samples'")
            have = {r[0] for r in cur.fetchall()}
            for w in want:
                name = w.split()[0]
                if name not in have:
                    cur.execute(f"ALTER TABLE media_monitor_samples ADD COLUMN {w}")


class MonitorSampler:
    def __init__(self, ledger, *, interval=45, bucket=None,
                 vram_probe=None, queue_stats=None, nvml=None):
        self.led = ledger
        self.interval = float(interval)
        self.bucket = int(bucket or interval)     # 对齐桶宽
        self._vram = vram_probe                    # 返回 budget.snapshot()
        self._stats = queue_stats                  # 返回 queue_stats dict
        self._nvml = nvml                          # 返回 (dict|None, source)
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def _collect(self):
        """分别采集；任一失败 → 该项 NULL + source 标记，不编数字"""
        src = {}
        row = {"queued": None, "running": None, "dead": None,
               "oldest_wait": None, "backoff_count": None, "next_retry_in": None,
               "failure_rate": None, "failure_attempts": None,
               "vram_reserved_mb": None, "vram_real_mb": None, "sources": {}}
        # 队列
        try:
            s = self._stats()
            row.update(queued=s["depth"]["queued"], running=s["depth"]["running"],
                       dead=s["depth"]["dead"],
                       oldest_wait=s["wait"]["oldest_runnable_age"],
                       backoff_count=s["wait"]["backoff_count"],
                       next_retry_in=s["wait"]["next_retry_in"],
                       failure_rate=s["failure_rate"],
                       failure_attempts=None)
            src["queue"] = "observed"
        except Exception:
            src["queue"] = "unavailable"
        # 静态显存预留
        try:
            b = self._vram()
            row["vram_reserved_mb"] = b["used_mb"]
            src["vram_reserved"] = "observed"
        except Exception:
            src["vram_reserved"] = "unavailable"
        # 真实显存（可能 None）
        try:
            real, why = self._nvml()
            if real:
                row["vram_real_mb"] = real["used"]
            src["vram_real"] = why                 # nvml|nvidia-smi|unavailable
        except Exception:
            src["vram_real"] = "unavailable"
        row["sources"] = json.dumps(src, ensure_ascii=False)
        return row

    def sample_once(self, now=None):
        now = now if now is not None else time.time()
        ts = int(now // self.bucket) * self.bucket      # 对齐桶
        row = self._collect()
        ph = alias_ph(self.led)
        cols = ["ts", "queued", "running", "dead", "oldest_wait",
                "backoff_count", "next_retry_in", "failure_rate",
                "vram_reserved_mb", "vram_real_mb", "sources"]
        vals = [ts] + [row[c] for c in cols[1:]]
        setclause = ", ".join(f"{c}=excluded.{c}" if self.led.dialect == "sqlite"
                              else f"{c}=EXCLUDED.{c}" for c in cols[1:])
        with txn(self.led) as cur:
            cur.execute(
                f"INSERT INTO media_monitor_samples({','.join(cols)}) "
                f"VALUES({','.join([ph]*len(cols))}) "
                f"ON CONFLICT(ts) DO UPDATE SET {setclause}", vals)
        return ts, row

    def serve_forever(self):
        try:
            migrate(self.led)
        except Exception as e:
            _swallow(__file__, e)

        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:
                _swallow(__file__, e)
                     # 采样异常不崩进程
            self._stop.wait(self.interval)
