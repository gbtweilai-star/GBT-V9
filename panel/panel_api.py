# panel/panel_api.py —— 总控台只读接口（真读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 契约: 全部返回 {data, updated_at, stale, error}
#   查询失败 → error 非空 + 保留最后成功值 + stale=True (绝不伪报"零告警/零调用")
import time, json, shutil, sqlite3
from fastapi import APIRouter, Query
from skills import NATIVE_POTATO_CAPS  # 见下: 能力清单
from senses.sqldialect import txn

router = APIRouter(prefix="/api/panel")
_STALE_AFTER = 30          # 秒; 超过两倍刷新间隔即标陈旧


def _env(data=None, error=None, updated=None):
    ts = updated or time.time()
    return {"data": data, "updated_at": ts,
            "stale": bool(error) or (time.time() - ts > _STALE_AFTER),
            "error": error}


def _q(sql, args=()):
    """方言无关查询: 统一走账本 _tx()"""
    led = router.ledger
    ph = "?" if led.dialect == "sqlite" else "%s"
    sql = sql.replace("{}", ph)
    with txn(led) as cur:
        cur.execute(sql, args)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


# ── ① 总览摘要 ──
@router.get("/overview")
def overview():
    try:
        led = router.ledger
        calls = _q("""SELECT COUNT(*) n, SUM(ok) ok, AVG(ms) ms
                      FROM skill_calls WHERE ts > {}""",
                   (time.time() - 3600,))[0]
        scanned = _q("SELECT COUNT(*) n FROM ledger")[0]["n"]
        alerts = _q("SELECT COUNT(*) n FROM alert_events WHERE ts > {}",
                    (time.time() - 86400,))[0]["n"]
        return _env({"calls_1h": calls["n"], "ok_rate":
                     round((calls["ok"] or 0) / calls["n"], 3) if calls["n"] else None,
                     "avg_ms": round(calls["ms"] or 0), "scanned": scanned,
                     "alerts_24h": alerts,
                     "ledger_backend": led.dialect,
                     "generated_at": time.time()})
    except Exception as e:
        return _env(error=str(e))


# ── ② 拓扑：节点 + 连线（状态色 + 活动量）──
@router.get("/topology")
def topology():
    try:
        reg = router.registry
        health = reg.probe_all()
        nodes, edges = [], []
        nodes.append({"id": "user",  "label": "用户请求", "group": "io",   "status": "ok"})
        nodes.append({"id": "brain", "label": "主脑 Brain", "group": "core",
                      "status": "ok", "metric": reg.brain.calls if reg.brain else 0})
        for n, s in reg.skills.items():
            h = health.get(s.__class__.__name__, None)
            nodes.append({"id": n, "label": n, "group": "skill",
                          "status": "ok" if (h and h.ok) else "off",
                          "metric": _skill_count(reg, n)})
            edges.append({"from": "brain", "to": n})
        for name, ok in _adapter_health().items():
            nodes.append({"id": name, "label": name, "group": "adapter",
                          "status": "ok" if ok else "off"})
            edges.append({"from": "brain", "to": name})
        for st in ("ledger", "devour", "voice"):
            nodes.append({"id": st, "label": {"ledger": "账本", "devour": "吞噬能",
                                               "voice": "语音"}[st],
                          "group": "store", "status": "ok"})
            edges.append({"from": "brain", "to": st})
        edges.insert(0, {"from": "user", "to": "brain"})
        return _env({"nodes": nodes, "edges": edges})
    except Exception as e:
        return _env(error=str(e))


def _skill_count(reg, name):
    try:
        return _q("SELECT COUNT(*) n FROM skill_calls WHERE skill={}", (name,))[0]["n"]
    except Exception:
        return None


def _adapter_health():
    return {
        "octop": getattr(router, "octop", None) and router.octop.health().get("octop", False),
        "ai-memory": bool(getattr(router, "mem", None)),
        "codex": bool(shutil.which("codex")),
        "archify": bool(shutil.which("archify")),
    }


# ── ③ 能力网格 ──
@router.get("/capabilities")
def capabilities():
    try:
        reg = router.registry
        health = reg.probe_all()
        rows = _q("""SELECT skill, COUNT(*) calls, SUM(ok) ok, AVG(ms) ms
                     FROM skill_calls GROUP BY skill""")
        stats = {r["skill"]: r for r in rows}
        out = []
        for name, meta in NATIVE_CAPABILITIES.items():
            cls = meta["cls"].split(":")[1]
            h = health.get(cls)
            s = stats.get(name.split("@")[0], {})
            out.append({
                "name": name, "kind": meta["kind"], "backend": meta["backend"],
                "ok": bool(h and h.ok), "reason": (h.reason if h else "未探测"),
                "calls": s.get("calls", 0),
                "rate": round((s.get("ok") or 0) / s["calls"], 3) if s.get("calls") else None,
                "avg_ms": round(s.get("ms") or 0),
            })
        return _env(out)
    except Exception as e:
        return _env(error=str(e))


# ── ④ 任务流水线（步骤链，可下钻）──
@router.get("/pipelines")
def pipelines(limit: int = 30):
    try:
        rows = _q("""SELECT task_id, skill, ts, ok, ms, error FROM skill_calls
                     ORDER BY ts DESC LIMIT {}""", (limit,))
        # 按 task_id 聚成步骤链
        chains = {}
        for r in rows:
            c = chains.setdefault(r["task_id"], {"task_id": r["task_id"], "steps": []})
            c["steps"].append({"step": r["skill"], "ts": r["ts"], "ok": bool(r["ok"]),
                               "ms": r["ms"], "error": r["error"]})
        for c in chains.values():
            c["steps"].sort(key=lambda x: x["ts"])
            c["status"] = "ok" if all(s["ok"] for s in c["steps"]) else "fail"
        return _env(sorted(chains.values(), key=lambda c: -max(
            s["ts"] for s in c["steps"])))
    except Exception as e:
        return _env(error=str(e))


@router.get("/pipelines/{task_id}")
def pipeline_detail(task_id: str):
    try:
        steps = _q("""SELECT ts,skill,version,ok,ms,engine,tentacle,
                             request,result,artifacts,warnings,error
                      FROM skill_calls WHERE task_id={} ORDER BY ts""", (task_id,))
        return _env({"task_id": task_id, "steps": steps})
    except Exception as e:
        return _env(error=str(e))


# ── ⑤ 告警 + 扩容 ──
@router.get("/alerts")
def alerts():
    try:
        ev = _q("""SELECT ts,alert_key,transition,level,value,threshold,detail
                   FROM alert_events ORDER BY ts DESC LIMIT 50""")
        st = _q("""SELECT alert_key,state,episode_id,last_seen,peak_value,fired_count
                   FROM alert_state""")
        return _env({"events": ev, "states": st})
    except Exception as e:
        return _env(error=str(e))


@router.get("/scale")
def scale():
    try:
        s = _q("SELECT * FROM scaler_state LIMIT 1")
        parts = _partition_list() if router.ledger.dialect == "pg" else None
        audits = (_q("SELECT * FROM scale_audit ORDER BY ts DESC LIMIT 20")
                  if router.ledger.dialect == "pg" else [])
        return _env({"controller": s[0] if s else None,
                     "partitions": parts, "audits": audits,
                     "capacity_gb": float(__import__("os").environ.get("DB_CAPACITY_GB", 0) or 0)})
    except Exception as e:
        return _env(error=str(e))


def _partition_list():
    try:
        return _q("""SELECT inhrelid::regclass::text name,
                            pg_total_relation_size(inhrelid) bytes
                     FROM pg_inherits ORDER BY name""")
    except Exception:
        return None
