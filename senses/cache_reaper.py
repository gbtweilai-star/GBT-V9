# senses/cache_reaper.py —— 缓存回收：LRU 淘汰 + 使用中保护 + 双目录独立预算
# dev: 自由的风 · 吞噬能配套 · _preview 和 _cache 超限自动清最旧
import os, time, json, threading
from pathlib import Path
from dataclasses import dataclass
from core.swallow import swallow as _swallow

@dataclass
class Budget:
    name: str
    dir: Path
    max_bytes: int          # 该目录的体积上限
    min_keep: int = 1       # 至少保留 N 个文件（哪怕超预算，也不清空）
    grace_sec: float = 60   # 刚写入的文件在这个时间内不删（防止边写边删）

class CacheReaper:
    """LRU 回收器：mtime 即最近访问时间，越旧越先淘汰"""
    def __init__(self, budgets: list[Budget], ledger=None, brain=None,
                 interval=120):
        self.budgets = budgets
        self.ledger, self.brain, self.interval = ledger, brain, interval
        self._pins: dict[Path, int] = {}          # 文件 → 引用计数（>0 禁删）
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.last = {"ts": 0, "freed_bytes": 0, "freed_files": 0}

    # ── pin / unpin：流式播放期间锁住文件 ──
    def pin(self, path):
        p = Path(path).resolve()
        with self._lock: self._pins[p] = self._pins.get(p, 0) + 1

    def unpin(self, path):
        p = Path(path).resolve()
        with self._lock:
            if p in self._pins:
                self._pins[p] -= 1
                if self._pins[p] <= 0: self._pins.pop(p, None)

    def _is_pinned(self, path) -> bool:
        with self._lock: return self._pins.get(Path(path).resolve(), 0) > 0

    # ── 访问即刷新 mtime = LRU 的"最近使用" ──
    @staticmethod
    def touch(path):
        try: os.utime(path, None)
        except OSError as e:
            _swallow(__file__, e)

    # ── 单目录回收 ──
    def reap_budget(self, b: Budget) -> tuple[int, int]:
        files = [p for p in b.dir.rglob("*") if p.is_file()]
        if not files: return 0, 0
        now = time.time()
        files.sort(key=lambda p: p.stat().st_mtime)          # 最旧在前
        total = sum(p.stat().st_size for p in files)
        freed_bytes = freed_files = 0
        keep = max(b.min_keep, 0)
        for p in files:
            if total <= b.max_bytes: break
            if len(files) - freed_files <= keep: break        # 保底数量
            if self._is_pinned(p): continue                   # 正在用，跳过
            if now - p.stat().st_mtime < b.grace_sec: continue # 刚写的，跳过
            try:
                size = p.stat().st_size
                p.unlink()
                total -= size
                freed_bytes += size; freed_files += 1
            except OSError as e:
                _swallow(__file__, e)

        return freed_bytes, freed_files

    # ── 全量回收 ──
    def reap_all(self) -> dict:
        result = {}
        for b in self.budgets:
            fb, ff = self.reap_budget(b)
            result[b.name] = {"freed_bytes": fb, "freed_files": ff,
                              "used": self.usage(b), "cap": b.max_bytes}
        self.last = {"ts": time.time(),
                     "freed_bytes": sum(r["freed_bytes"] for r in result.values()),
                     "freed_files": sum(r["freed_files"] for r in result.values())}
        if self.last["freed_files"] and self.ledger:
            self.ledger.log("cache-reaper", "cache", "scanned",
                            f"清理 {self.last['freed_files']} 文件 "
                            f"{self.last['freed_bytes']//1048576}MB")
        return result

    @staticmethod
    def usage(b: Budget) -> int:
        return sum(p.stat().st_size for p in b.dir.rglob("*") if p.is_file())

    def status(self) -> list[dict]:
        out = []
        for b in self.budgets:
            used = self.usage(b)
            out.append({"name": b.name, "used": used, "cap": b.max_bytes,
                        "pct": round(used / b.max_bytes * 100, 1) if b.max_bytes else 0,
                        "files": sum(1 for p in b.dir.rglob("*") if p.is_file()),
                        "pinned": sum(1 for p in self._pins
                                      if str(p).startswith(str(b.dir.resolve())))})
        return out

    # ── 后台循环 ──
    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try: self.reap_all()
                except Exception as e:
                    if self.brain:
                        self.brain.ask("cache-reaper", "reap_failed", str(e))
        threading.Thread(target=loop, daemon=True).start()

    def stop(self): self._stop.set()


# ── 从环境变量读预算，默认值适配单机吞噬场景 ──
def default_reaper(frame_dir="devoured/t1-eye", ledger=None, brain=None) -> CacheReaper:
    """预算来自环境变量（MB）：PREVIEW_CACHE_MB=2048 · PULL_CACHE_MB=5120 · REAP_INTERVAL_SEC=120。"""
    frame_dir = Path(frame_dir or os.getenv("DEVOUR_DIR", "devoured/t1-eye"))
    (frame_dir / "_preview").mkdir(parents=True, exist_ok=True)
    (frame_dir / "_cache").mkdir(parents=True, exist_ok=True)
    mb = 1024 * 1024
    return CacheReaper(
        budgets=[
            Budget("preview", frame_dir / "_preview",
                   int(os.environ.get("PREVIEW_CACHE_MB", 2048)) * mb, min_keep=1),
            Budget("cache",   frame_dir / "_cache",
                   int(os.environ.get("PULL_CACHE_MB", 5120)) * mb, min_keep=1),
        ], ledger=ledger, brain=brain,
        interval=int(os.environ.get("REAP_INTERVAL_SEC", 120)))
