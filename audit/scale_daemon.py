# audit/scale_daemon.py —— 实例级扩容守护：状态机 + 冷却 + 预算 + 审计
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 重要: Tiger 计算层不是 autoscaling，resize 会短暂重启（通常<1分钟）
#   -> 所以必须: 连续越阈值才动、冷却期内不重复、有预算上限、全程审计
#   -> 自动降配建议人工批准（默认关闭）
from core.swallow import swallow as _swallow
import os, json, time, threading, subprocess
from enum import Enum
from senses.sqldialect import txn

class S(str, Enum):
    IDLE="idle"; WATCH="watch"; SCALING="scaling"; COOLDOWN="cooldown"; BLOCKED="blocked"

# 规格阶梯（CPU millis, 内存 GB）——按需调整
LADDER = [(500,2),(1000,4),(2000,8),(4000,16),(8000,32),(16000,64),(32000,128)]

# ── 跨进程状态表：守护写、面板只读（单行/服务）──
STATE_DDL = """
CREATE TABLE IF NOT EXISTS scaler_state(
    service_id TEXT PRIMARY KEY,
    state TEXT, spec_cpu INTEGER, spec_mem INTEGER,
    used_pct REAL, growth_gb_day REAL, eta_days REAL,
    last_action TEXT, last_action_ts TIMESTAMPTZ,
    cooldown_until TIMESTAMPTZ, protect_flag BOOL DEFAULT false,
    resizes_this_month INTEGER DEFAULT 0,
    heartbeat TIMESTAMPTZ DEFAULT now(), updated_at TIMESTAMPTZ DEFAULT now());
"""

# 单控制器锁：advisory lock 只有 PG 有；SQLite 退化为「本地单控制器」语义
LOCK_KEY = 0x5CA1E


class ScaleDaemon:
    def __init__(self, ledger, watch, service_id=None, project_id=None,
                 api_key=None, cooldown_sec=1800, max_cpu_millis=8000,
                 monthly_resize_budget=20, brain=None):
        self.led, self.watch, self.brain = ledger, watch, brain
        self.service_id = service_id or os.environ.get("TIGER_SERVICE_ID")
        self.project_id = project_id or os.environ.get("TIGER_PROJECT_ID")
        self.api_key = api_key or os.environ.get("TIGER_SECRET_KEY")
        self.cooldown = cooldown_sec
        self.max_cpu = max_cpu_millis
        self.budget = monthly_resize_budget
        self.state = S.IDLE.value
        self._owns_control = False
        self._spec: dict = {}
        self._last_action = ""
        try:
            with txn(self.led) as cur:
                cur.execute(STATE_DDL)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

        self._stop = threading.Event()
        self._streak = 0
        self._last_scale = 0.0
        self._resizes_this_month = 0

    # ── 审计：每次动作都落库 ──
    def _audit(self, action, detail, ok=True):
        with txn(self.led) as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS scale_audit(
                ts TIMESTAMPTZ DEFAULT now(), action TEXT, detail TEXT, ok BOOL)""")
            cur.execute("INSERT INTO scale_audit(action,detail,ok) VALUES(%s,%s,%s)",
                        (action, detail, ok))

    # ── 当前规格 ──
    def current_spec(self) -> dict:
        # 走 Tiger CLI（简单可靠）；也可换 REST API
        try:
            out = subprocess.run(
                ["tiger", "service", "get", self.service_id, "-o", "json"],
                capture_output=True, text=True, check=True).stdout
            d = json.loads(out)
            return {"cpu_millis": d.get("cpu_millis"), "memory_gbs": d.get("memory_gbs")}
        except Exception as e:
            self._audit("get_spec", f"失败: {e}", ok=False)
            return {}

    # ── 升一档 ──
    def scale_up_one(self) -> dict:
        cur = self.current_spec()
        cpu = cur.get("cpu_millis")
        if not cpu:
            return {"ok": False, "why": "读不到当前规格"}
        nxt = next(((c, m) for c, m in LADDER if c > cpu), None)
        if not nxt:
            return {"ok": False, "why": "已在阶梯顶"}
        if nxt[0] > self.max_cpu:
            self.state = S.BLOCKED.value
            self._audit("scale_up", f"超预算上限 {self.max_cpu}，拒绝升到 {nxt[0]}", ok=False)
            if self.brain:
                self.brain.ask("scale", self.service_id, f"扩容受阻：超上限 {self.max_cpu}")
            return {"ok": False, "why": f"超上限 {self.max_cpu}"}
        if self._resizes_this_month >= self.budget:
            self._audit("scale_up", f"月度预算 {self.budget} 用尽", ok=False)
            return {"ok": False, "why": "月度预算用尽"}
        try:
            subprocess.run(["tiger", "service", "resize", self.service_id,
                            "--cpu", str(nxt[0]), "--memory", str(nxt[1])],
                           check=True, capture_output=True)
            self._resizes_this_month += 1
            self._audit("scale_up", f"{cpu}->{nxt[0]} cpu, {nxt[1]}GB")
            return {"ok": True, "from": cpu, "to": nxt[0], "mem": nxt[1]}
        except Exception as e:
            self._audit("scale_up", f"resize 失败: {e}", ok=False)
            return {"ok": False, "why": str(e)}

    # ── 主循环：读 advice → 状态机决定动作 ──
    def tick(self) -> dict:
        if not self._owns_control and not self.acquire_control():
            try:
                self.watch.sample()
            except Exception as e:
                from core import swallow as _sw; _sw.swallow(__file__, e)

            return {"state": "observer", "note": "未持有控制器锁"}
        adv = self.watch.advise()
        used = adv["used_pct"]
        want_grow = any(a["action"] == "grow" for a in adv["actions"])
        want_archive = any(a["action"] == "archive" for a in adv["actions"])
        want_protect = any(a["action"] == "protect" for a in adv["actions"])
        result = {"state": self.state, "used_pct": used, "actions": []}

        # 连续 3 次建议扩容才动（防单次尖峰）
        self._streak = self._streak + 1 if want_grow else 0

        # 保护模式优先（限流由写入侧读 flag 实现）
        if want_protect:
            self._audit("protect", f"used={used}%")
            result["actions"].append("protect")
            result["protect_flag"] = True

        # 归档（表级，安全操作，随时可做）
        if want_archive:
            result["actions"].append("archive")

        # 扩容：连续 + 冷却 + 预算 三重门
        if self._streak >= 3 and time.time() - self._last_scale > self.cooldown:
            self.state = S.SCALING.value
            r = self.scale_up_one()
            if r["ok"]:
                self._last_scale = time.time()
                self.state = S.COOLDOWN.value
                result["actions"].append(f"scaled_up {r.get('from')}->{r.get('to')}")
            else:
                self.state = S.BLOCKED.value
                result["actions"].append(f"scale_blocked: {r['why']}")
            self._streak = 0
        elif self.state == S.COOLDOWN.value and time.time() - self._last_scale > self.cooldown:
            self.state = S.WATCH.value

        result["state"] = self.state
        self._persist_state(result)
        return result

    def acquire_control(self) -> bool:
        """尝试成为唯一控制器；PG 用 advisory lock，SQLite 视为单机唯一。"""
        backend = getattr(self.led, "backend", "sqlite")
        if backend == "sqlite":
            self._owns_control = True          # 单机单进程语义
            return True
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
                row = cur.fetchone()
                self._owns_control = bool(row and row[0])
        except Exception:
            self._owns_control = False
        return self._owns_control

    def _persist_state(self, extra: dict | None = None) -> None:
        """把控制器状态写进 scaler_state（面板跨进程只读这一行）。"""
        try:
            g = self.watch.growth_rate() if hasattr(self.watch, "growth_rate") else {}
        except Exception:
            g = {}
        row = {
            "service_id": self.service_id or "local",
            "state": self.state,
            "spec_cpu": (self._spec or {}).get("cpu_millis"),
            "spec_mem": (self._spec or {}).get("memory_gbs"),
            "used_pct": g.get("used_pct"),
            "growth_gb_day": g.get("gb_per_day"),
            "eta_days": g.get("eta_days"),
            "last_action": self._last_action,
            "cooldown_until": (self._last_scale + self.cooldown) if self._last_scale else None,
            "protect_flag": bool(extra and extra.get("protect_flag")),
            "resizes_this_month": self._resizes_this_month,
        }
        backend = getattr(self.led, "backend", "sqlite")
        try:
            if backend == "sqlite":
                self._persist_state_sqlite(row)
            else:
                self._persist_state_pg(row)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def _persist_state_pg(self, row: dict) -> None:
        with self.led._tx(write=True) as c, c.cursor() as cur:
            cur.execute("""
                INSERT INTO scaler_state
                (service_id,state,spec_cpu,spec_mem,used_pct,growth_gb_day,eta_days,
                 last_action,last_action_ts,cooldown_until,protect_flag,
                 resizes_this_month,heartbeat,updated_at)
                VALUES(%(service_id)s,%(state)s,%(spec_cpu)s,%(spec_mem)s,%(used_pct)s,
                 %(growth_gb_day)s,%(eta_days)s,%(last_action)s,now(),
                 to_timestamp(%(cooldown_until)s),%(protect_flag)s,
                 %(resizes_this_month)s,now(),now())
                ON CONFLICT (service_id) DO UPDATE SET
                  state=EXCLUDED.state, spec_cpu=EXCLUDED.spec_cpu,
                  spec_mem=EXCLUDED.spec_mem, used_pct=EXCLUDED.used_pct,
                  growth_gb_day=EXCLUDED.growth_gb_day, eta_days=EXCLUDED.eta_days,
                  last_action=EXCLUDED.last_action, last_action_ts=now(),
                  cooldown_until=EXCLUDED.cooldown_until,
                  protect_flag=EXCLUDED.protect_flag,
                  resizes_this_month=EXCLUDED.resizes_this_month,
                  heartbeat=now(), updated_at=now()""", row)

    def _persist_state_sqlite(self, row: dict) -> None:
        """SQLite 版（本地演练/单机）：等价字段，时间是 epoch 秒。"""
        import json as _json
        import time as _t
        with self.led._tx(write=True) as c:
            c.execute("""CREATE TABLE IF NOT EXISTS scaler_state(
                service_id TEXT PRIMARY KEY, state TEXT, spec_cpu INTEGER,
                spec_mem INTEGER, used_pct REAL, growth_gb_day REAL, eta_days REAL,
                last_action TEXT, last_action_ts REAL, cooldown_until REAL,
                protect_flag INTEGER DEFAULT 0, resizes_this_month INTEGER DEFAULT 0,
                heartbeat REAL, updated_at REAL)""")
            c.execute("""INSERT INTO scaler_state VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(service_id) DO UPDATE SET
                  state=excluded.state, spec_cpu=excluded.spec_cpu,
                  spec_mem=excluded.spec_mem, used_pct=excluded.used_pct,
                  growth_gb_day=excluded.growth_gb_day, eta_days=excluded.eta_days,
                  last_action=excluded.last_action, last_action_ts=excluded.last_action_ts,
                  cooldown_until=excluded.cooldown_until,
                  protect_flag=excluded.protect_flag,
                  resizes_this_month=excluded.resizes_this_month,
                  heartbeat=excluded.heartbeat, updated_at=excluded.updated_at""",
                (row["service_id"], row["state"], row["spec_cpu"], row["spec_mem"],
                 row["used_pct"], row["growth_gb_day"], row["eta_days"],
                 row["last_action"], _t.time(), row["cooldown_until"],
                 1 if row["protect_flag"] else 0, row["resizes_this_month"],
                 _t.time(), _t.time()))

    def start(self):
        def loop():
            while not self._stop.wait(60):        # 每分钟判一次
                try: self.tick()
                except Exception as e:
                    _swallow(__file__, e)
        threading.Thread(target=self.tick and loop, daemon=True, name="scale-daemon").start()
    def stop(self): self._stop.set()
