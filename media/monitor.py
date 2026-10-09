# media/monitor.py —— 生成队列告警监视器：阈值 → 告警状态机 → critical 语音
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律:
#   - 全部走 AlertManager.observe_incident（episode 边沿去重，不刷屏）
#   - critical 才语音播报；恢复用 recover_incident（连续确认由状态机把关）
#   - 阈值全部环境变量可调；默认值见下
import os
import threading
import time
import uuid

from media.metrics import queue_stats, read_vram, vram_snapshot
from core.swallow import swallow as _swallow

TH = {
    "depth_warn": float(os.environ.get("MEDIA_WARN_DEPTH", "50")),
    "depth_crit": float(os.environ.get("MEDIA_CRIT_DEPTH", "200")),
    "wait_warn": float(os.environ.get("MEDIA_WARN_WAIT_SEC", "600")),
    "wait_crit": float(os.environ.get("MEDIA_CRIT_WAIT_SEC", "1800")),
    "fail_warn": float(os.environ.get("MEDIA_WARN_FAIL", "0.10")),
    "fail_crit": float(os.environ.get("MEDIA_CRIT_FAIL", "0.25")),
    "fail_min_attempts": int(os.environ.get("MEDIA_MIN_ATTEMPTS", "20")),
    "vram_reserved_warn": float(os.environ.get("MEDIA_WARN_VRAM", "0.90")),
    "vram_reserved_crit": float(os.environ.get("MEDIA_CRIT_VRAM", "1.00")),
    "vram_real_warn": float(os.environ.get("MEDIA_WARN_VRAM_REAL", "0.90")),
    "vram_real_crit": float(os.environ.get("MEDIA_CRIT_VRAM_REAL", "0.95")),
}


def _level(value, warn, crit):
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return None


class MediaMonitor:
    """周期采样队列指标 → 阈值判定 → 事件型告警（critical 语音）"""

    def __init__(self, ledger, alerts, voice=None, *, interval=30.0,
                 queue=None, budget=None, thresholds=None):
        self.led, self.alerts, self.voice = ledger, alerts, voice
        self.interval = float(interval)
        self.q = queue
        self.budget = budget
        self.th = dict(TH)
        self.th.update(thresholds or {})
        self._last_dead = None
        self._stop = threading.Event()      # 未置位 = 可运行；置位 = 已停
        self._t = None

    def sample_once(self):
        """采样一轮：每个指标独立 incident（键分开，恢复互不干扰）"""
        s = queue_stats(self.led)
        if s.get("coverage") != "observed":
            return {"coverage": "unavailable"}
        now = time.time()
        occ = uuid.uuid4().hex[:8]

        # ① 队列深度（queued；dead 不算待处理）
        depth = s["depth"]["queued"]
        lvl = _level(depth, self.th["depth_warn"], self.th["depth_crit"])
        self._observe("media.queue.depth", lvl, depth, occ,
                      {"running": s["depth"]["running"], "dead": s["depth"]["dead"]})

        # ② 最老可运行等待（退避任务不算等待 GPU）
        wait = s["wait"]["oldest_runnable_age"] or 0
        lvl = _level(wait, self.th["wait_warn"], self.th["wait_crit"])
        self._observe("media.queue.wait", lvl, wait, occ,
                      {"backoff_count": s["wait"]["backoff_count"],
                       "next_retry_in": s["wait"]["next_retry_in"]})

        # ③ 失败率（样本足够才判；无样本 → 恢复，不报 0%）
        rate = s["failure_rate"]
        if s["failure_rate_kind"] == "observed" and \
                s["failure_attempts"] >= self.th["fail_min_attempts"]:
            lvl = _level(rate, self.th["fail_warn"], self.th["fail_crit"])
            self._observe("media.failure", lvl, rate, occ,
                          {"attempts": s["failure_attempts"]})
        else:
            self.alerts.recover_incident("media.failure",
                                         detail={"note": "样本不足或无失败"})

        # ④ 显存：静态预留 与 真实读数 分开判，绝不合并
        snap = vram_snapshot(self.budget)
        if snap and snap.get("total_mb"):
            pct = snap["used_mb"] / snap["total_mb"]
            lvl = _level(pct, self.th["vram_reserved_warn"],
                         self.th["vram_reserved_crit"])
            self._observe("media.vram.reserved", lvl, round(pct, 4), occ,
                          {"used_mb": snap["used_mb"], "total_mb": snap["total_mb"],
                           "note": "进程内静态预算"})
        real, src = read_vram()
        if real and real.get("total_mb"):
            pct = real["used_mb"] / real["total_mb"]
            lvl = _level(pct, self.th["vram_real_warn"], self.th["vram_real_crit"])
            self._observe("media.vram.real", lvl, round(pct, 4), occ,
                          {"used_mb": real["used_mb"], "total_mb": real["total_mb"],
                           "source": src})
        else:
            self.alerts.recover_incident("media.vram.real",
                                         detail={"note": "真实显存采集不可用"})

        # ⑤ 死信：新增即 critical
        dead = s["depth"]["dead"]
        if self._last_dead is not None and dead > self._last_dead:
            self._observe("media.dead", "critical", dead, occ,
                          {"new": dead - self._last_dead, "total": dead})
        elif dead == 0:
            self.alerts.recover_incident("media.dead",
                                         detail={"note": "死信清零"})
        self._last_dead = dead
        return {"coverage": "observed", "depth": depth, "dead": dead}

    def _observe(self, key, level, value, occ, detail):
        r = self.alerts.observe_incident(key, occ,
                                         level=level or "info",
                                         value=value, detail=detail)
        if r.transition == "fired" and level == "critical" and self.voice:
            try:
                self.voice.say_alert({
                    "level": "critical", "label": key,
                    "value": value, "unit": "", "episode": r.episode_id})
            except Exception as e:
                _swallow(__file__, e)

        return r

    def _loop(self):
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception as e:
                _swallow(__file__, e)


    def start(self):
        if self._t is not None and self._t.is_alive():
            return                               # 已在跑
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True,
                                   name="media-monitor")
        self._t.start()

    def stop(self):
        self._stop.set()
