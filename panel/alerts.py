# panel/alerts.py —— 面板告警状态机 · 边沿触发 · 不刷屏
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 规则（经复核）:
#   - 每类指标独立状态: NORMAL / ACTIVE / UNKNOWN
#   - 只在 NORMAL→ACTIVE 写一条 fired，ACTIVE→NORMAL 写一条 recovered
#   - ACTIVE 期间持续超标 -> 不重复写日志，只更新读数
#   - 探测失败 -> UNKNOWN，保留原 ACTIVE（失败≠恢复）
#   - 恢复迟滞: 池占用触发>90%，恢复需≤85%（防抖动反复）
#   - episode 去重: UNIQUE(alert_key, episode_id, transition)
#
# 落盘说明（2026-10-06）：双方言化——时间统一存 epoch 秒（sqlite REAL / pg DOUBLE
# PRECISION），建表/写入按 is_pg 分支用字面 SQL + 参数绑定，游标走 senses.sqldialect.txn。
from core.swallow import swallow as _swallow
import os, json, time, uuid, threading
from enum import Enum

from senses.sqldialect import is_pg, txn


class State(str, Enum):
    NORMAL = "normal"; ACTIVE = "active"; UNKNOWN = "unknown"

class Level(str, Enum):
    WARNING = "warning"       # 池占用超限
    CRITICAL = "critical"     # 锁等待 / 后端不可达

# ── 告警规则表：阈值 + 迟滞 + 级别 ──
RULES = {
    "pool_util":  {"level": Level.WARNING.value,
                   "trigger": lambda v: v > 90, "recover": lambda v: v <= 85,
                   "threshold": 90, "unit": "%", "label": "连接池占用"},
    "lock_waits": {"level": Level.CRITICAL.value,
                   "trigger": lambda v: v > 0, "recover": lambda v: v == 0,
                   "threshold": 0, "unit": "个", "label": "锁等待"},
    "backend_down": {"level": Level.CRITICAL.value,
                   "trigger": lambda v: v == 1, "recover": lambda v: v == 0,
                   "threshold": 1, "unit": "", "label": "后端不可达"},
}


class AlertManager:
    """每 3 秒被调用一次；只做状态跃迁判断与边沿写库"""
    def __init__(self, ledger, probe_fn, interval=3.0,
                 cooldown_sec=60, recover_confirm=2, dialect=None):
        self.led, self.probe_fn = ledger, probe_fn
        self.dialect = dialect or getattr(ledger, "dialect",
                                          "pg" if is_pg(ledger) else "sqlite")
        self.interval = interval
        self.cooldown = cooldown_sec          # 同类告警最小重发间隔
        self.recover_confirm = recover_confirm  # 连续 N 次正常才判恢复
        self._lock = threading.Lock()
        self._state = {}
        self._incidents = {}   # 事件型告警（外粘合层）                      # key -> {state, episode, first, peak, ok_streak}
        self._stop = threading.Event()
        self._last_fire = {}
        self._init_schema()
        self._load_state()                    # 重启后恢复，避免重复触发/漏恢复

    def _init_schema(self):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS alert_events(
                    id BIGSERIAL PRIMARY KEY,
                    alert_key TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    level TEXT NOT NULL,
                    value REAL, threshold REAL,
                    detail TEXT,
                    ts DOUBLE PRECISION NOT NULL,
                    UNIQUE(alert_key, episode_id, transition))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS alert_state(
                    alert_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    episode_id TEXT,
                    first_seen DOUBLE PRECISION,
                    last_seen DOUBLE PRECISION,
                    last_value REAL,
                    peak_value REAL,
                    fired_count INTEGER DEFAULT 0)""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_ts ON alert_events(ts)")
        else:
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS alert_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_key TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    level TEXT NOT NULL,
                    value REAL, threshold REAL,
                    detail TEXT,
                    ts REAL NOT NULL,
                    UNIQUE(alert_key, episode_id, transition))""")
                cur.execute("""CREATE TABLE IF NOT EXISTS alert_state(
                    alert_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    episode_id TEXT,
                    first_seen REAL,
                    last_seen REAL,
                    last_value REAL,
                    peak_value REAL,
                    fired_count INTEGER DEFAULT 0)""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_alert_ts ON alert_events(ts)")

    def _load_state(self):
        with txn(self.led) as cur:
            cur.execute("SELECT alert_key,state,episode_id,first_seen,peak_value "
                        "FROM alert_state")
            for k, st, ep, fs, pk in cur.fetchall():
                self._state[k] = {"state": st, "episode": ep,
                                  "first": fs if fs else time.time(),
                                  "peak": pk or 0, "ok_streak": 0}

    # ── 核心：单指标跃迁 ──
    def _evaluate(self, key, value, probe_ok=True) -> dict | None:
        rule = RULES[key]
        cur = self._state.setdefault(key, {"state": State.NORMAL.value,
            "episode": None, "first": None, "peak": 0, "ok_streak": 0})

        # ① 探测失败 -> UNKNOWN，保留原状态，不写日志
        if not probe_ok:
            if cur["state"] == State.NORMAL.value:
                cur["state"] = State.UNKNOWN.value
            return None

        # ② 从 UNKNOWN 恢复探测：不写事件，只回到原状态判断
        was_unknown = cur["state"] == State.UNKNOWN.value
        if was_unknown:
            cur["state"] = State.ACTIVE.value if cur["episode"] else State.NORMAL.value

        prev, episode = cur["state"], cur["episode"]

        if rule["trigger"](value):                        # 超阈值
            cur["ok_streak"] = 0
            cur["peak"] = max(cur["peak"] or 0, value)
            cur["last_value"] = value
            if prev != State.ACTIVE.value:
                episode = uuid.uuid4().hex[:8]            # 新 episode
                cur.update({"state": State.ACTIVE.value, "episode": episode,
                            "first": time.time()})
                return self._fire(key, episode, value, rule, cur)
            return None                                   # ACTIVE 期间不重复写

        # 未超阈值：走迟滞恢复
        if prev == State.ACTIVE.value:
            cur["ok_streak"] = (cur["ok_streak"] or 0) + 1
            if rule["recover"](value) and cur["ok_streak"] >= self.recover_confirm:
                ep = cur["episode"]
                dur = time.time() - (cur["first"] or time.time())
                cur.update({"state": State.NORMAL.value, "episode": None,
                            "first": None, "peak": 0, "ok_streak": 0})
                return self._recover(key, ep, value, rule, cur, dur)
        return None

    # ── 边沿写库：fire ──
    def _fire(self, key, episode, value, rule, cur):
        self._persist_state(key, cur)
        row = (key, episode, rule["level"], value, rule["threshold"],
               f"{rule['label']}={value}{rule['unit']} 超阈值{rule['threshold']}",
               time.time())
        try:
            if is_pg(self.led):
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(%s,%s,'fired',%s,%s,%s,%s,%s)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
            else:
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(?,?, 'fired',?,?,?,?,?)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

        self._last_fire[key] = time.time()
        # 真写一条 blocked 到账本（status 合法值里有 blocked）
        try:
            self.led.log("panel-alert", f"alert:{key}", "blocked",
                         f"{rule['label']}={value}{rule['unit']} (episode={episode})")
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

        return {"key": key, "level": rule["level"], "value": value,
                "episode": episode, "transition": "fired", "label": rule["label"]}

    # ── 边沿写库：recover ──
    def _recover(self, key, episode, value, rule, cur, dur):
        self._persist_state(key, cur)
        row = (key, episode, rule["level"], value, rule["threshold"],
               f"持续 {int(dur)}s 后恢复", time.time())
        try:
            if is_pg(self.led):
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(%s,%s,'recovered',%s,%s,%s,%s,%s)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
            else:
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(?,?,'recovered',?,?,?,?,?)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)

        return {"key": key, "level": rule["level"], "value": value,
                "episode": episode, "transition": "recovered",
                "label": rule["label"], "duration_sec": int(dur)}

    def _persist_state(self, key, cur):
        row = (cur["state"], cur["episode"], cur["first"] or time.time(),
               time.time(), cur.get("last_value"), cur.get("peak"), key)
        try:
            if is_pg(self.led):
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_state
                        (alert_key,state,episode_id,first_seen,last_seen,last_value,peak_value)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (alert_key) DO UPDATE SET
                          state=EXCLUDED.state, episode_id=EXCLUDED.episode_id,
                          first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen,
                          last_value=EXCLUDED.last_value, peak_value=EXCLUDED.peak_value""", row)
            else:
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_state
                        (alert_key,state,episode_id,first_seen,last_seen,last_value,peak_value)
                        VALUES(?,?,?,?,?,?,?)
                        ON CONFLICT (alert_key) DO UPDATE SET
                          state=EXCLUDED.state, episode_id=EXCLUDED.episode_id,
                          first_seen=EXCLUDED.first_seen, last_seen=EXCLUDED.last_seen,
                          last_value=EXCLUDED.last_value, peak_value=EXCLUDED.peak_value""", row)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    # ── 对外：一次探测 + 全指标评估 ──
    def tick(self) -> dict:
        with self._lock:
            snap = self.probe_fn()                       # {pool_util, lock_waits, backend_ok}
            events = []
            for key in RULES:
                if key == "backend_down":
                    # backend_down 由 backend_ok 折算，探针里没有同名键
                    ev = self._evaluate(key, 0 if snap.get("backend_ok") else 1)
                else:
                    v = snap.get(key)
                    if v is None:
                        self._evaluate(key, 0, probe_ok=False)   # 指标缺失 = UNKNOWN
                        continue
                    ev = self._evaluate(key, float(v))
                if ev: events.append(ev)
            active = self.active()
            return {"events": events, "active": active,
                    "snapshot": snap, "ts": time.time()}

    def active(self) -> list:
        out = []
        for key, cur in self._state.items():
            if cur["state"] == State.ACTIVE.value:
                out.append({"key": key, "label": RULES[key]["label"],
                            "level": RULES[key]["level"],
                            "episode": cur["episode"],
                            "value": cur.get("last_value"),
                            "peak": cur.get("peak"),
                            "threshold": RULES[key]["threshold"],
                            "unit": RULES[key]["unit"],
                            "since": cur.get("first"),
                            "duration_sec": int(time.time() - (cur.get("first") or time.time())),
                            "state": cur["state"]})
            elif cur["state"] == State.UNKNOWN.value:
                out.append({"key": key, "label": RULES[key]["label"],
                            "level": "unknown", "state": "unknown",
                            "value": None, "since": None})
        return out

    def overall(self) -> str:
        """面板用：critical > warning > unknown > normal"""
        st = {a["state"] for a in self.active()}
        lv = {a.get("level") for a in self.active()}
        if "critical" in lv: return "critical"
        if "warning" in lv: return "warning"
        if "unknown" in st: return "unknown"
        return "normal"

    def history(self, limit=50):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""SELECT alert_key,episode_id,transition,level,value,detail,ts
                    FROM alert_events ORDER BY ts DESC LIMIT %s""", (limit,))
                return [{"key": r[0], "episode": r[1], "transition": r[2],
                         "level": r[3], "value": r[4], "detail": r[5],
                         "ts": r[6]} for r in cur.fetchall()]
        with txn(self.led) as cur:
            cur.execute("""SELECT alert_key,episode_id,transition,level,value,detail,ts
                FROM alert_events ORDER BY ts DESC LIMIT ?""", (limit,))
            return [{"key": r[0], "episode": r[1], "transition": r[2],
                     "level": r[3], "value": r[4], "detail": r[5],
                     "ts": r[6]} for r in cur.fetchall()]

    # ── 后台循环 ──
    def start(self):
        def loop():
            while not self._stop.wait(self.interval):
                try: self.tick()
                except Exception as e:
                    _swallow(__file__, e)
        threading.Thread(target=loop, daemon=True, name="alert-loop").start()


    # ═══ 事件型告警（外粘合层用）：episode 边沿语义 ═══
    # observe：无活动 episode → fired（写一条）；已有 → active（不写，去重靠
    # UNIQUE(alert_key, episode_id, transition)）。recover：关闭 episode → recovered。
    def observe_incident(self, alert_key, occurrence_id, level="warning",
                         value=None, detail=None):
        from types import SimpleNamespace
        cur = self._incidents.setdefault(
            alert_key, {"episode": None, "total": 0, "first": None})
        if cur["episode"] is None:
            episode = uuid.uuid4().hex[:8]
            cur.update({"episode": episode, "total": 0, "first": time.time()})
            self._incident_event(alert_key, episode, "fired", level, value, detail)
            cur["total"] = 1
            self._persist_incident(alert_key, "active", episode, cur)
            return SimpleNamespace(transition="fired", episode_id=episode,
                                   episode_total=1)
        cur["total"] += 1
        self._persist_incident(alert_key, "active", cur["episode"], cur)
        return SimpleNamespace(transition="active", episode_id=cur["episode"],
                               episode_total=cur["total"])

    def recover_incident(self, alert_key, detail=None):
        from types import SimpleNamespace
        cur = self._incidents.get(alert_key)
        if not cur or cur["episode"] is None:
            return SimpleNamespace(transition="none", episode_id=None,
                                   episode_total=0)
        ep, total = cur["episode"], cur["total"]
        self._incident_event(alert_key, ep, "recovered", "info", None, detail)
        cur.update({"episode": None, "total": 0})
        self._persist_incident(alert_key, "normal", None, cur)
        return SimpleNamespace(transition="recovered", episode_id=ep,
                               episode_total=total)

    def _incident_event(self, alert_key, episode, transition, level, value,
                        detail):
        row = (alert_key, episode, transition, level, value, None,
               json.dumps(detail, ensure_ascii=False)[:300] if detail else None,
               time.time())
        try:
            if is_pg(self.led):
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
            else:
                with txn(self.led) as cc:
                    cc.execute("""INSERT INTO alert_events
                        (alert_key,episode_id,transition,level,value,threshold,detail,ts)
                        VALUES(?,?,?,?,?,?,?,?)
                        ON CONFLICT (alert_key,episode_id,transition) DO NOTHING""", row)
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def _persist_incident(self, alert_key, state, episode, cur):
        self._state[alert_key] = {"state": state, "episode": episode,
                                  "first": cur.get("first") or time.time(),
                                  "peak": cur.get("total") or 0, "ok_streak": 0}
        try:
            self._persist_state(alert_key, {
                "state": state, "episode": episode,
                "first": cur.get("first") or time.time(),
                "last_value": cur.get("total"),
                "peak": cur.get("total")})
        except Exception as e:
            from core import swallow as _sw; _sw.swallow(__file__, e)


    def stop(self): self._stop.set()
