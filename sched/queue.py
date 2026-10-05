# sched/queue.py —— GPU 队列适配：显存互斥 + 本工程持久化队列
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 对齐点：parity/operations.py 的 scheduler.fake_gpu_queue 期望
# (gpu_mutex, ledger, max_attempts)。显存预算用本工程 media/scheduler.VramBudget，
# 任务仍进 media.queue.JobQueue —— 唯一调度器原则，不新造第二套。
from media.queue import JobQueue, ensure_tables
from media.scheduler import VramBudget


class GpuQueue:
    def __init__(self, ledger, gpu_mutex=None, *, max_attempts=2,
                 budget_mb=None, **kw):
        ensure_tables(ledger)
        self.led = ledger
        self.mutex = gpu_mutex
        self.budget = VramBudget(int(budget_mb or 1024))
        self.q = JobQueue(ledger)
        self.max_attempts = int(max_attempts)

    def enqueue(self, project_id, stage="gpu", skill="media.video.gen",
                params=None, *, priority=0):
        return self.q.enqueue(project_id, stage, skill, params or {},
                              priority=priority, max_attempts=self.max_attempts)

    def snapshot(self):
        return self.budget.snapshot()

    def __getattr__(self, name):
        return getattr(self.q, name)
