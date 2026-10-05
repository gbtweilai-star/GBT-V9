# alert/media_rules.py —— 队列指标 → 指纹 → 去抖/冷却/恢复
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 只对 observed 样本评估; 缺失/不可用不触发"健康"也不触发"恢复";
#       同指纹只保留一份活跃告警; critical 才语音播报; 死信按 job_id 去重
import time

LEVELS = ("normal", "warn", "critical")


class Rule:
    """持续去抖 + 恢复滞回。for_sec 需持续满足才升级; recover_sec 需连续满足才恢复。"""
    def __init__(self, name, warn, crit, for_sec, recover_sec, cooldown=300):
        self.name, self.warn, self.crit = name, warn, crit
        self.for_sec, self.recover_sec = for_sec, recover_sec
        self.cooldown = cooldown

    def level_of(self, v):
        if v >= self.crit: return "critical"
        if v >= self.warn: return "warn"
        return "normal"


# 阈值默认（可配置覆盖）
RULES = {
    "queue_depth":     Rule("queue_depth", 50, 200,  300, 300),
    "oldest_wait":     Rule("oldest_wait", 600, 1800, 120, 300),
    "failure_rate":    Rule("failure_rate", 0.10, 0.25, 300, 300),
    "vram_reserved":   Rule("vram_reserved", 0.90, 1.00, 120, 300),
    "vram_real":       Rule("vram_real", 0.90, 0.95, 120, 300),
}


class MediaAlertEvaluator:
    """
    与项目现有告警状态机对接：构造时传入 state_machine。
    期望接口（按你的状态机实际方法名映射）：
        sm.raise_(fingerprint, level, detail, ts)
        sm.clear(fingerprint, ts)
        sm.speak(text)          # 仅 critical 调用
    """
    def __init__(self, state_machine, rules=None, enabled=True):
        self.sm = state_machine
        self.rules = dict(rules or RULES)
        self.enabled = enabled
        self._since = {}        # fp -> (first_ts_of_pending_level)
        self._active = {}        # fp -> level
        self._last_clear = {}

    def _drive(self, fp, target, detail, ts):
        """等级状态推进：持续满足 / 滞回恢复"""
        cur = self._active.get(fp)
        pend = self._since.get(fp)
        if target == "normal":
            # 恢复：需连续满足 recover_sec；缺失样本不调用本路径
            if cur:
                if pend and ts - pend[0] >= self.rules[fp.split(":")[0]].recover_sec:
                    self.sm.clear(fp, ts); self._active.pop(fp, None)
                    self._since.pop(fp, None); self._last_clear[fp] = ts
                elif not pend:
                    self._since[fp] = (ts, "normal")
            return
        if cur == target:
            return
        if not pend or pend[1] != target:
            self._since[fp] = (ts, target)          # 换了目标等级 → 重新计时
            return
        if ts - pend[0] >= self.rules[fp.split(":")[0]].for_sec:
            self.sm.raise_(fp, target, detail, ts)
            self._active[fp] = target
            self._since.pop(fp, None)
            if target == "critical":
                self.sm.speak(detail.get("text", fp))

    def evaluate(self, metric, value, ts=None, *, observed=True, detail=None):
        """value=None 或 observed=False → 不评估(缺失/不可用不触发健康或恢复)"""
        if not self.enabled or not observed or value is None:
            return None
        ts = ts if ts is not None else time.time()
        rule = self.rules.get(metric)
        if not rule:
            return None
        fp = f"{metric}:main"                       # 指纹：同指标只一份活跃告警
        target = rule.level_of(value)
        d = dict(detail or {}); d.setdefault("text", f"{metric}={value}")
        self._drive(fp, target, d, ts)
        return self._active.get(fp, "normal")

    def evaluate_snapshot(self, sample, ts=None):
        """从一条 media_monitor_samples 评估全部指标"""
        ts = ts if ts is not None else sample.get("ts")
        if not sample:
            return
        self.evaluate("queue_depth", sample.get("queued"), ts,
                      observed=sample.get("queued") is not None)
        self.evaluate("oldest_wait", sample.get("oldest_wait"), ts,
                      observed=sample.get("oldest_wait") is not None)
        # 失败率仅在"有足够尝试"时才评估（样本不足不告警）
        fa = sample.get("failure_attempts")
        self.evaluate("failure_rate", sample.get("failure_rate"), ts,
                      observed=(sample.get("failure_rate") is not None
                                and (fa or 0) >= 20))
        self.evaluate("vram_reserved", _pct(sample.get("vram_reserved_mb"),
                                            sample.get("vram_total_mb")), ts,
                      observed=sample.get("vram_reserved_mb") is not None)
        self.evaluate("vram_real", _pct(sample.get("vram_real_mb"),
                                        sample.get("vram_total_mb")), ts,
                      observed=sample.get("vram_real_mb") is not None)


def _pct(used, total):
    return (used / total) if (used is not None and total) else None


# 死信告警：按 job_id 指纹，一次一份
def dead_letter_alert(sm, job_id, detail, ts=None):
    fp = f"dead:{job_id}"
    sm.raise_(fp, "warn", dict(detail, text=f"死信 {job_id}"), ts or time.time())
