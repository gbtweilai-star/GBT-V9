# scan/pool.py —— 多触手并发扫描 · 每根触手独立身份/配额/记忆
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import queue, threading, time
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed

@dataclass
class Tentacle:
    """一根并发触手的运行时身份"""
    tid: str
    brain: object
    ledger: object
    mem: object
    keyring: object = None          # 隔离总线：配额闸门
    rules: object = None            # scan_rules.scan
    stats: dict = field(default_factory=lambda:
        {"scanned": 0, "vuln": 0, "blocked": 0, "llm_calls": 0, "ms": 0})

    def _think(self, target, detail):
        """带配额闸门的反思：超配额则跳过 LLM，只落账"""
        if self.keyring and not self.keyring.allow(self.tid):
            return {"cmd": "continue", "hint": "配额已满，跳过反思"}
        self.stats["llm_calls"] += 1
        return self.brain.ask(self.tid, target, detail)

    def handle(self, target: str):
        t0 = time.perf_counter()
        try:
            finding = self.rules(target) if self.rules else []
            if finding:
                self.stats["vuln"] += 1
                self.ledger.log(self.tid, target, "vuln", str(finding))
                cmd = self._think(target, str(finding))
                self.ledger.set_verdict(target, cmd.get("verdict", ""), self.tid)
                self.mem.remember(self.tid, f"vuln:{target}", finding)
                return {"target": target, "status": "vuln", "cmd": cmd}
            self.stats["scanned"] += 1
            self.ledger.log(self.tid, target, "scanned", "")
            return {"target": target, "status": "scanned"}
        except Exception as e:
            self.stats["blocked"] += 1
            self.ledger.log(self.tid, target, "blocked", str(e))
            cmd = self._think(target, f"卡点: {e}")        # 卡点回主脑等指令
            return {"target": target, "status": "blocked", "cmd": cmd}
        finally:
            self.stats["ms"] += int((time.perf_counter() - t0) * 1000)


class ScanPool:
    """工作队列 + 触手池：目标不重不漏，账本由 Ledger 自身保证线程安全"""
    def __init__(self, tentacles: list[Tentacle], ledger, brain=None,
                 flush_every=200):
        self.tentacles = tentacles
        self.ledger, self.brain = ledger, brain
        self.flush_every = flush_every
        self.q: queue.Queue = queue.Queue()
        self._pending: list = []                       # 批写缓冲
        self._plock = threading.Lock()
        self._stop = threading.Event()
        self.done = 0
        self.total = 0

    # ── 入队：目标全集 ──
    def feed(self, targets):
        self.total = len(targets)
        for t in targets:
            self.q.put(t)

    # ── 单个触手的工作循环 ──
    def _worker(self, t: Tentacle):
        while not self._stop.is_set():
            try:
                target = self.q.get(timeout=1.0)
            except queue.Empty:
                return                                 # 队列空，收工
            try:
                res = t.handle(target)
                self._buffer(target, res["status"])
                self.done += 1
            finally:
                self.q.task_done()

    # ── 批量落账缓冲：降低锁竞争 ──
    def _buffer(self, target, status):
        with self._plock:
            self._pending.append(target)
            if len(self._pending) >= self.flush_every:
                self._pending.clear()

    # ── 主跑 ──
    def run(self, targets, max_workers=None) -> dict:
        self.feed(targets)
        workers = max_workers or len(self.tentacles)
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers,
                                thread_name_prefix="tentacle") as ex:
            futs = [ex.submit(self._worker, t) for t in self.tentacles]
            for f in as_completed(futs):
                f.result()                              # 让异常浮出，不静默
        elapsed = time.perf_counter() - t0
        return self.summary(elapsed)

    def stop(self):
        self._stop.set()

    def summary(self, elapsed=0.0) -> dict:
        agg = {"scanned": 0, "vuln": 0, "blocked": 0, "llm_calls": 0, "ms": 0}
        per = {}
        for t in self.tentacles:
            per[t.tid] = dict(t.stats)
            for k in agg: agg[k] += t.stats[k]
        return {"total_targets": self.total, "done": self.done,
                "elapsed_sec": round(elapsed, 2),
                "throughput": round(self.done / elapsed, 1) if elapsed else 0,
                "aggregate": agg, "per_tentacle": per,
                "ledger_counts": self.ledger.counts()}
