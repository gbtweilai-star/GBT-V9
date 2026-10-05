# media/selector.py —— 先判资格(许可/区域/健康), 再判成本(本地等待 vs 云预算)
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 许可未知 = 不可商用; 商用任务遇到 false/区域不符 → 直接排除
#   - 本地优先: 选预估等待最小的本地适配器(忙则排队)
#   - 云默认关(cloud_enabled=False); 开启后要"原子预留预算", 防各自检查后超支
#   - 无合法适配器: 暂时不可用→回队列; 许可/能力不匹配→死信并上报主脑
import threading
from typing import Protocol, runtime_checkable


@runtime_checkable
class Adapter(Protocol):
    name: str
    is_local: bool
    is_cloud: bool
    vram_mb: int
    exclusive: bool
    commercial_allowed: bool | None          # None=未知→按不可商用
    regions: set | None                      # None=无限制
    def supports(self, job) -> bool: ...
    def health(self) -> bool: ...
    def estimated_wait(self, job) -> float: ...
    def estimated_cost(self, job) -> float: ...
    def remaining_quota(self, job): ...      # None=不限(本地)
    def run(self, job, ctx) -> dict: ...


class BudgetLedger:
    """云预算原子预留，防并发超支。"""
    def __init__(self, limit):
        self.limit = float(limit or 0)
        self.spent = 0.0
        self._lock = threading.Lock()

    def reserve(self, amount):
        with self._lock:
            if self.spent + amount > self.limit:
                return False
            self.spent += amount
            return True

    def release(self, amount):
        with self._lock:
            self.spent = max(0.0, self.spent - amount)


class QuotaSelector:
    def __init__(self, *, cloud_enabled=False, budget=None, spend=None):
        self.cloud_enabled = bool(cloud_enabled)
        self.budget = budget or {}
        self.spend = spend or {}
        self._reason = {}

    # ── 资格筛选 ──
    def _eligible(self, job, a):
        if not a.supports(job):
            return False, "unsupported"
        if not a.health():
            return False, "unhealthy"
        if job.get("commercial"):
            if a.commercial_allowed is not True:
                return False, "license_not_commercial"
            if a.regions is not None and job.get("region") not in a.regions:
                return False, "region_forbidden"
        return True, None

    def pick(self, job, adapters):
        self._reason[job["job_id"]] = None
        cands = []
        hard_reject = None
        for a in adapters:
            ok, why = self._eligible(job, a)
            if ok:
                cands.append(a)
            else:
                if why in ("license_not_commercial", "region_forbidden"):
                    hard_reject = why          # 许可不匹配 → 死信
        # 1) 本地优先：预估等待最小（忙则排队）
        locals_ = [a for a in cands if a.is_local]
        if locals_:
            return min(locals_, key=lambda a: a.estimated_wait(job))
        # 2) 云：默认关 + 预算 + 额度
        if not self.cloud_enabled:
            self._reason[job["job_id"]] = hard_reject or "cloud_disabled"
            return None
        clouds = []
        for a in cands:
            if not a.is_cloud:
                continue
            cost = a.estimated_cost(job)
            if cost > self.budget.get(a.name, 0):
                continue
            q = a.remaining_quota(job)
            if q is not None and q < cost:
                continue
            clouds.append((cost, a))
        if not clouds:
            self._reason[job["job_id"]] = hard_reject or "no_capacity"
            return None
        cost, a = min(clouds, key=lambda t: t[0])
        led = self.spend.get(a.name)
        if led and not led.reserve(cost):              # 原子预留
            self._reason[job["job_id"]] = "budget_exceeded"
            return None
        return a

    def last_reject_reason(self, job, adapters):
        return self._reason.get(job["job_id"]) or "no_capacity"
