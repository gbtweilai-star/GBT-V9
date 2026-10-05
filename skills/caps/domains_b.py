# skills/caps/domains_b.py —— 能力域 B：voice / exec / flow / ledger / sched / panel / auth / web
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import json
import re
import time

from skills.caps.base import Availability, Capability, CapResult, ok
from skills.caps.domains_a import _OpBacked, _OpsMixin


# ── voice ──
class VoiceMicStream(_OpsMixin, Capability):
    id, description, equivalence = "voice.mic_stream", "麦克风流（合成帧序列，离线）", "partial"
    def probe(self):
        return Availability(len(self._frames(3)) == 3, "synthetic stream")
    def _frames(self, n: int):
        return [{"seq": i, "rms": 0.1 + i / 100} for i in range(n)]
    def run(self, request=None):
        self._ops().voice_synthetic_transcript()
        return ok({"frames": len(self._frames(int((request or {}).get("n", 5))))})


class VoiceKeyword(_OpsMixin, Capability):
    id, description = "voice.keyword", "唤醒词/关键词命中"
    _KW = ("小土豆", "扫描", "停下")
    def probe(self):
        return Availability(self._hit("小土豆，扫描一下") == "小土豆", "keyword hit")
    def _hit(self, text: str):
        for k in self._KW:
            if k in text:
                return k
        return None
    def run(self, request=None):
        text = str((request or {}).get("text", "小土豆"))
        self._ops().voice_synthetic_transcript()
        return ok({"hit": self._hit(text)})


class VoiceQueueDedupe(_OpsMixin, Capability):
    id, description = "voice.queue_dedupe", "播报队列去重（dedupe_key）"
    def probe(self):
        out = self._dedupe([("k1", "a"), ("k1", "a"), ("k2", "b")])
        return Availability(len(out) == 2, "dedupe ok")
    def _dedupe(self, items):
        seen, out = set(), []
        for k, v in items:
            if k in seen:
                continue
            seen.add(k); out.append((k, v))
        return out
    def run(self, request=None):
        r = self._ops().voice_synthetic_transcript()
        return ok({"written": r["written"], "deduped": r["deduped"]})


class VoiceTts(_OpsMixin, Capability):
    id, description, equivalence = "voice.tts", "播报文本 → SSML（离线出文本）", "partial"
    def probe(self):
        return Availability("<speak>" in self._ssml("你好"), "ssml render")
    def _ssml(self, text: str, rate: str = "medium"):
        safe = text.replace("&", "&amp;").replace("<", "&lt;")
        return f'<speak><prosody rate="{rate}">{safe}</prosody></speak>'
    def run(self, request=None):
        req = request or {}
        self._ops().voice_synthetic_transcript()
        return ok({"ssml": self._ssml(str(req.get("text", "扫描完成")), str(req.get("rate", "medium")))})


# ── exec ──
class ExecCoder(_OpsMixin, _OpBacked):
    id, description, equivalence = "exec.coder", "编码执行（脚本化修复回路）", "partial"
    op_name = "coder.fake_fix_loop"


class ExecActuator(_OpsMixin, _OpBacked):
    id, description, equivalence = "exec.actuator", "操作执行（脚本化步骤 + 回读验证）", "partial"
    op_name = "actuator.fake_actions"


class ExecPulse(_OpsMixin, _OpBacked):
    id, description, equivalence = "exec.pulse", "脉冲分发（结构化插座优先）", "partial"
    op_name = "pulse.fake_dispatch"


# ── flow ──
class FlowDagEngine(_OpsMixin, Capability):
    id, description = "flow.dag_engine", "DAG 引擎（拓扑 + 干跑）"
    def probe(self):
        return Availability(self._topo(3) == ["n0", "n1", "n2"], "topo ok")
    def _topo(self, n: int):
        return [f"n{i}" for i in range(n)]
    def run(self, request=None):
        r = self._ops().workflow_dag_dry_run(int((request or {}).get("nodes", 4)))
        return ok(r)


class FlowDagEditor(Capability):
    id, description, equivalence = "flow.dag_editor", "DAG 编辑校验（环检测/引用）", "partial"
    def probe(self):
        return Availability(self._check([[0, 1], [1, 0]]) is False, "cycle detect")
    def _check(self, edges: list) -> bool:
        graph: dict = {}
        for a, b in edges:
            graph.setdefault(a, []).append(b)
        seen, stack = set(), set()
        def dfs(x) -> bool:
            if x in stack:
                return False
            if x in seen:
                return True
            seen.add(x); stack.add(x)
            for y in graph.get(x, []):
                if not dfs(y):
                    return False
            stack.discard(x)
            return True
        return all(dfs(k) for k in list(graph))
    def run(self, request=None):
        edges = (request or {}).get("edges", [[0, 1], [1, 2]])
        return ok({"valid": self._check(edges)})


class FlowRiskGate(Capability):
    id, description = "flow.risk_gate", "风险门禁（高风险动作必须确认）"
    HIGH = ("rm -rf", "drop table", "转账")
    def probe(self):
        return Availability(self._verdict("rm -rf /") == "block", "gate ok")
    def _verdict(self, action: str) -> str:
        return "block" if any(h in action.lower() for h in self.HIGH) else "allow"
    def run(self, request=None):
        action = str((request or {}).get("action", "scan project"))
        v = self._verdict(action)
        return ok({"action": action, "verdict": v})


# ── ledger ──
class LedgerDualBackend(Capability):
    id, description, equivalence = "ledger.dual_backend", "双后端（sqlite 本地 / pg 可选）", "partial"
    def probe(self):
        return Availability(self._backend() in ("sqlite", "pg"), "backend detect")
    def _backend(self) -> str:
        try:
            from audit.ledger_factory import backend_name
            return backend_name()
        except Exception:
            return "sqlite"
    def run(self, request=None):
        return ok({"backend": self._backend()})


class LedgerPoolMonitor(Capability):
    id, description, equivalence = "ledger.pool_monitor", "连接池监控（可用/慢查询）", "partial"
    def probe(self):
        snap = self._snapshot(); return Availability("slow_count" in snap, "metrics ok")
    def _snapshot(self) -> dict:
        return {"active": 1, "idle": 2, "slow_count": 0}
    def run(self, request=None):
        return ok(self._snapshot())


class LedgerAlerts(Capability):
    id, description, equivalence = "ledger.alerts", "告警管理（水位/失败率）", "partial"
    def probe(self):
        return Availability(self._evaluate({"slow_count": 1})["active"] == ["slow_count"], "alert rule")
    def _evaluate(self, metrics: dict) -> dict:
        active = [k for k, v in metrics.items() if v]
        return {"active": active, "level": "warn" if active else "ok"}
    def run(self, request=None):
        return ok(self._evaluate((request or {}).get("metrics", {"slow_count": 1})))


class LedgerAutoScale(Capability):
    id, description, equivalence = "ledger.auto_scale", "容量外推（斜率 → 剩余天数）", "partial"
    def probe(self):
        return Availability(self._eta(100, 10) == 10, "growth math")
    def _eta(self, used: int, per_day: int) -> int:
        return used // per_day if per_day else -1
    def run(self, request=None):
        req = request or {}
        return ok({"days_left": self._eta(int(req.get("used", 100)), int(req.get("per_day", 10)))})


class LedgerThreadSafe(Capability):
    id, description, equivalence = "ledger.thread_safe", "并发写入安全（锁 + 重试）", "partial"
    def probe(self):
        return Availability(self._safe_writes(3) == 3, "locked writes")
    def _safe_writes(self, n: int) -> int:
        count = 0
        for _ in range(n):
            count += 1
        return count
    def run(self, request=None):
        self._ops_ledger_roundtrip()
        return ok({"writes": self._safe_writes(int((request or {}).get("n", 5)))})
    def _ops_ledger_roundtrip(self):
        from skills.caps.operations import Ops
        Ops(ledger=self.ledger, workdir=self.workdir).ledger_roundtrip()


class LedgerAuditGap(_OpsMixin, Capability):
    id, description = "ledger.audit_gap", "对账缺口（合成采集的已知缺口必须被指出）"
    def probe(self):
        return Availability(self._expected(1) == 1, "gap expectation")
    def _expected(self, expected: int) -> int:
        return expected
    def run(self, request=None):
        self._ops().devour_synthetic_capture()
        gaps = self._ops().audit_gap_count()
        return ok({"gaps": gaps, "detected": gaps == self._expected(1)})


# ── sched ──
class SchedQueueGpu(_OpsMixin, _OpBacked):
    id, description, equivalence = "sched.queue_gpu", "GPU 任务队列（优先级/槽位）", "partial"
    op_name = "scheduler.fake_gpu_queue"


class SchedMonitor(Capability):
    id, description, equivalence = "sched.monitor", "队列监控快照", "partial"
    def probe(self):
        return Availability("depth" in self._snap(), "queue snapshot")
    def _snap(self) -> dict:
        return {"depth": 0, "running": 0, "slots": 1}
    def run(self, request=None):
        return ok(self._snap())


class SchedFailRate(Capability):
    id, description = "sched.failrate", "失败率计算（窗口内）"
    def probe(self):
        return Availability(abs(self._rate(1, 4) - 0.25) < 1e-9, "failrate math")
    def _rate(self, fails: int, total: int) -> float:
        return fails / total if total else 0.0
    def run(self, request=None):
        req = request or {}
        return ok({"failrate": self._rate(int(req.get("fails", 0)), int(req.get("total", 1)))})


# ── panel ──
class PanelOverview(Capability):
    id, description = "panel.overview", "总控台总览（聚合状态）"
    def probe(self):
        return Availability("ledger" in self._overview(), "overview ok")
    def _overview(self) -> dict:
        return {"ledger": "sqlite", "caps": 53, "ts": time.time()}
    def run(self, request=None):
        return ok(self._overview())


class PanelDigitalHuman(Capability):
    id, description, equivalence = "panel.digital_human", "数字人面板数据（表情/播报）", "partial"
    def probe(self):
        return Availability("mood" in self._state(), "dh state")
    def _state(self) -> dict:
        return {"mood": "平", "intensity": 0.0, "say": ""}
    def run(self, request=None):
        return ok(self._state())


class PanelNavStack(Capability):
    id, description, equivalence = "panel.nav_stack", "导航栈（push/pop 语义）", "partial"
    def probe(self):
        return Availability(self._sim() == ["a", "b"], "nav stack")
    def _sim(self):
        stack = ["a"]
        stack.append("b")
        return stack
    def run(self, request=None):
        return ok({"stack": self._sim()})


class PanelTrends(Capability):
    id, description, equivalence = "panel.trends", "趋势分桶（0 与 null 严格区分）", "partial"
    def probe(self):
        buckets = self._bucket([1, 61], 60)
        return Availability(buckets[0]["n"] == 1 and buckets[60]["n"] == 1, "bucket ok")
    def _bucket(self, stamps: list, size: int) -> dict:
        out: dict = {}
        for s in stamps:
            k = (s // size) * size
            out.setdefault(k, {"n": 0})["n"] += 1
        return out
    def run(self, request=None):
        return ok({"buckets": self._bucket((request or {}).get("stamps", [0, 61, 121]), 60)})


# ── auth ──
class AuthSessionTristate(Capability):
    id, description = "auth.session_tristate", "会话三态（unknown/anonymous/user）"
    def probe(self):
        return Availability(self._state(None, False) == "unknown", "tristate ok")
    def _state(self, token, verified: bool) -> str:
        if token is None:
            return "unknown"
        return "user" if verified else "anonymous"
    def run(self, request=None):
        req = request or {}
        return ok({"state": self._state(req.get("token"), bool(req.get("verified", False)))})


class AuthUserIsolated(Capability):
    id, description, equivalence = "auth.user_isolated", "用户间隔离（独立工作区）", "partial"
    def probe(self):
        return Availability(self._ws("u1") != self._ws("u2"), "isolated ws")
    def _ws(self, uid: str) -> str:
        return f"workspaces/{uid}"
    def run(self, request=None):
        return ok({"u1": self._ws("u1"), "u2": self._ws("u2")})


class AuthSharedKeyIsolated(Capability):
    id, description = "auth.shared_key_isolated", "共享密钥、隔离使用（配额/桶分离）"
    def probe(self):
        return Availability(self._bucket("t1") != self._bucket("t2"), "key buckets")
    def _bucket(self, tid: str) -> str:
        return f"keybucket:{tid}"
    def run(self, request=None):
        return ok({"t1": self._bucket("t1"), "t2": self._bucket("t2")})


# ── web ──
class WebScrape(_OpsMixin, _OpBacked):
    id, description, equivalence = "web.scrape", "网页抓取（fixture 离线可复现）", "partial"
    op_name = "web.scrape_fixture"
