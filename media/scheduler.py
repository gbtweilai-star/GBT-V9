# media/scheduler.py —— 显存预算 + GPU 互斥 + 工作循环
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 重任务独占显存; 轻任务可并发但"总和"不得超预算
#   - 拿不到显存 = 排队等待(不是失败); 等待期间持续 heartbeat
#   - 进程内锁只管单调度器; 多进程共享 GPU 需外部跨进程锁
#   - 静态显存预算不替代真实显存监测
#   - 云主管道: 云模式下**本地预留显存恒为 0**, 本地 GPU 活不接受、交云插件(见 compute_router)
from core.swallow import swallow as _swallow
import os, threading, time, json


def _cloud_mode_default():
    try:
        from core.compute_router import channel
        return channel() == "cloud"
    except Exception:                                      # noqa: BLE001
        return (os.environ.get("V9_COMPUTE_CHANNEL") or "cloud").strip().lower() == "cloud"


class VramBudget:
    """显存预算分配器：独占任务需全空；共享任务需总和不超预算。

    云主管道（cloud_mode=True）时本地额度强制为 0：任何 need>0 的本地 GPU 活
    都**立刻拒绝**并给出原因（绝不静默排队、也不静默占卡）。
    """
    def __init__(self, total_mb, *, cloud_mode=None):
        self.cloud_mode = _cloud_mode_default() if cloud_mode is None else bool(cloud_mode)
        self.requested_mb = int(total_mb or 0)
        self.total = 0 if self.cloud_mode else int(total_mb or 0)
        self.used = 0
        self._exclusive = False
        self.last_refusal = None
        self.refused_local_mb = 0
        self._cv = threading.Condition()

    def acquire(self, need_mb, exclusive=False, timeout=None):
        need_mb = int(need_mb or 0)
        if self.cloud_mode and need_mb > 0:
            with self._cv:
                self.refused_local_mb += need_mb
                self.last_refusal = (f"本地 0 显存：该活需 {need_mb}MB，"
                                     f"已交云插件主管道（V9_COMPUTE_CHANNEL=cloud）")
            return False
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

    def acquire(self, need_mb, exclusive=False, timeout=None):
        need_mb = int(need_mb or 0)
        if self.cloud_mode and need_mb > 0:
            with self._cv:
                self.refused_local_mb += need_mb
                self.last_refusal = (f"本地 0 显存：该活需 {need_mb}MB，"
                                     f"已交云插件主管道（V9_COMPUTE_CHANNEL=cloud）")
            return False
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
        if self.cloud_mode and int(need_mb or 0) > 0:
            return
        with self._cv:
            self.used = max(0, self.used - need_mb)
            if exclusive:
                self._exclusive = False
            self._cv.notify_all()

    def snapshot(self):
        with self._cv:
            return {"total_mb": self.total, "used_mb": self.used,
                    "exclusive": self._exclusive,
                    "cloud_mode": self.cloud_mode,
                    "requested_mb": self.requested_mb,
                    "refused_local_mb": self.refused_local_mb,
                    "last_refusal": self.last_refusal,
                    "channel": "cloud" if self.cloud_mode else "local"}


class GpuScheduler:
    """单调度器工作循环：claim → 选适配器 → 抢显存 → 跑 → 回写。"""

    def __init__(self, queue, selector, adapters, budget_mb, *, worker_id=None,
                 poll_sec=2.0, hb_sec=30.0, job_timeout=3600):
        self.q = queue
        self.selector = selector
        self.adapters = adapters
        self.budget = VramBudget(budget_mb)
        self.last_delegations = []
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
        # 云主管道: 本地 GPU 活不占卡, 直接交云插件（绝不死等显存）
        if self.budget.cloud_mode and adapter.is_local and need > 0:
            self._delegate_to_cloud(job, adapter, owner, need)
            return
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

    def _delegate_to_cloud(self, job, adapter, owner, need_mb):
        """本地 0 显存：本地 GPU 活交云插件主管道。记账并如实回写原因。"""
        reason, plugin = "", None
        try:
            from core.compute_router import route, workload_of_job, mark
            wid = workload_of_job(job if isinstance(job, dict) else {})
            if wid is None:
                reason = f"local_vram_zero: 未登记的算力活（stage={job.get('stage')}）"
            else:
                r = route(wid)
                if r.get("channel") == "cloud":
                    plugin = r.get("plugin")
                    reason = f"local_vram_zero→cloud:{plugin}（本地需 {need_mb}MB，不占卡）"
                    led = getattr(self.q, "led", None) or getattr(self.q, "ledger", None)
                    if led is not None:
                        mark(r, db=led)
                else:
                    reason = f"local_vram_zero: 云不可用({r.get('why')})，保持本地备用未启用"
        except Exception as exc:                            # noqa: BLE001
            reason = f"local_vram_zero: 转云失败 {type(exc).__name__}"
        try:
            self.q.fail(job["job_id"], owner, reason)
        except Exception as e:
            _swallow(__file__, e)
        self.last_delegations.append({"job_id": job.get("job_id"), "plugin": plugin,
                                      "need_mb": need_mb, "at": time.time(),
                                      "reason": reason})
        del self.last_delegations[:-50]


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
