# media/scheduler.py —— 显存预算 + GPU 互斥 + 工作循环
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 重任务独占显存; 轻任务可并发但"总和"不得超预算
#   - 拿不到显存 = 排队等待(不是失败); 等待期间持续 heartbeat
#   - 进程内锁只管单调度器; 多进程共享 GPU 需外部跨进程锁
#   - 静态显存预算不替代真实显存监测
import os, threading, time, json


class VramBudget:
    """显存预算分配器：独占任务需全空；共享任务需总和不超预算。"""
    def __init__(self, total_mb):
        self.total = int(total_mb)
        self.used = 0
        self._exclusive = False
        self._cv = threading.Condition()

    def acquire(self, need_mb, exclusive=False, timeout=None):
        deadline = None if timeout is None else time.time() + timeout
        with self._cv:
            while True:
                ok = (not self._exclusive and not exclusive
                      and self.used + need_mb <= self.total) or \
                     (exclusive and self.used == 0)
                if ok:
                    self.used += need_mb
                    self._exclusive = exclusive
                    return True
                remain = None if deadline is None else deadline - time.time()
                if remain is not None and remain <= 0:
                    return False
                self._cv.wait(timeout=remain)

    def release(self, need_mb, exclusive=False):
        with self._cv:
            self.used = max(0, self.used - need_mb)
            if exclusive:
                self._exclusive = False
            self._cv.notify_all()

    def snapshot(self):
        with self._cv:
            return {"total_mb": self.total, "used_mb": self.used,
                    "exclusive": self._exclusive}


class GpuScheduler:
    """单调度器工作循环：claim → 选适配器 → 抢显存 → 跑 → 回写。"""

    def __init__(self, queue, selector, adapters, budget_mb, *, worker_id=None,
                 poll_sec=2.0, hb_sec=30.0, job_timeout=3600):
        self.q = queue
        self.selector = selector
        self.adapters = adapters
        self.budget = VramBudget(budget_mb)
        self.worker_id = worker_id or f"w-{os.getpid()}"
        self.poll_sec = poll_sec
        self.hb_sec = hb_sec
        self.job_timeout = job_timeout
        self._stop = threading.Event()

    def stop(self):
        self._stop.set()

    def serve_forever(self):
        while not self._stop.is_set():
            self.q.sweep_expired()                     # 回收崩溃租约
            job = self.q.claim(self.worker_id)
            if not job:
                self._stop.wait(self.poll_sec)
                continue
            self.run_job(job)

    def run_job(self, job):
        owner = job["lease_owner"]
        adapter = self.selector.pick(job, self.adapters)
        if adapter is None:
            # 无合法适配器: 回队列等待(不是死信), 除非许可不匹配
            reason = self.selector.last_reject_reason(job, self.adapters)
            self.q.fail(job["job_id"], owner, f"no adapter: {reason}")
            return

        need = adapter.vram_mb if adapter.is_local else 0
        exclusive = adapter.exclusive
        # 抢显存；拿不到就排队等待，期间持续 heartbeat 续租
        got = False
        while not got and not self._stop.is_set():
            self.q.heartbeat(job["job_id"], owner)      # 等 GPU 时也要续租
            got = self.budget.acquire(need, exclusive, timeout=self.hb_sec)
        if not got:
            return
        try:
            with AdapterGuard(self, job, adapter, owner):
                res = adapter.run(job, {"budget": self.budget.snapshot()})
            self.q.complete(job["job_id"], owner,
                            artifact_sha=res.get("artifact_sha"),
                            checkpoint=res.get("checkpoint"))
        except Exception as e:
            self.q.fail(job["job_id"], owner, f"{type(e).__name__}: {e}")
        finally:
            self.budget.release(need, exclusive)


class AdapterGuard:
    """跑任务期间后台续租；结束/异常都停掉。"""
    def __init__(self, sched, job, adapter, owner):
        self.s, self.job, self.owner = sched, job, owner
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        def beat():
            while not self._stop.wait(self.s.hb_sec):
                self.s.q.heartbeat(self.job["job_id"], self.owner)
        self._t = threading.Thread(target=beat, daemon=True)
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        if self._t:
            self._t.join(timeout=1)
        return False
