# panel/panel_api.py —— 总控台只读接口（真读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 契约: 全部返回 {data, updated_at, stale, error}
#   查询失败 → error 非空 + 保留最后成功值 + stale=True (绝不伪报"零告警/零调用")
from core.swallow import swallow as _swallow
import time, json, shutil, sqlite3
from fastapi import APIRouter, Body, Query
try:                                              # 早期导出名不一致会让本模块"导入即炸"，
    from skills import NATIVE_POTATO_CAPS         # 结果 /api/panel 从未被挂载（告警接口因此不存在）
except ImportError:                               # 真实导出名（skills/__init__.py）
    from skills import NATIVE_CAPABILITIES as NATIVE_POTATO_CAPS
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
# ── 注册表视图：注入的可能是 SkillRegistry（9 原生技能）也可能是 CapRegistry（53 离线 Octop 能力）──
# 真机病因（2026-10-08 逐行复核）：server.py:1384 注入的是 CapRegistry，而本文件原先读
#   reg.skills / reg.brain / health.get(类名) —— CapRegistry 三样都没有 ⇒ 拓扑页 AttributeError
#   走 error 信封、能力页恒判『未探测』、53 项一条都列不出来（列表只遍历 NATIVE_CAPABILITIES 9 条）。
# 口径：两种注册表都归一成一张视图；谁被注入都看得见，类型不同不许整页炸。
def _reg():
    return getattr(router, "registry", None)


def _reg_maps(reg):
    """→ (原生技能表, 离线能力表, 主脑, 账本)"""
    skills = getattr(reg, "skills", None)
    caps = getattr(reg, "caps", None)
    return (dict(skills) if isinstance(skills, dict) else {},
            dict(caps) if isinstance(caps, dict) else {},
            getattr(reg, "brain", None),
            getattr(reg, "ledger", None) or getattr(router, "ledger", None))


def _reg_health(reg):
    """probe 归一成 {名字: {"ok","reason"}}：CapRegistry 回 {done,detail} / SkillRegistry 回 Availability。"""
    if reg is None:
        return {}
    try:
        h = reg.probe_all() or {}
    except Exception as e:                                   # noqa: BLE001
        return {"__probe__": {"ok": False, "reason": f"{type(e).__name__}: {e}"}}
    out = {}
    for k, v in h.items():
        if isinstance(v, dict):
            out[k] = {"ok": bool(v.get("done")), "reason": str(v.get("detail") or "")}
        else:
            out[k] = {"ok": bool(getattr(v, "ok", False)),
                      "reason": str(getattr(v, "reason", "") or "")}
    return out


@router.get("/topology")
def topology():
    try:
        reg = _reg()
        skills, caps, brain, _led = _reg_maps(reg)
        health = _reg_health(reg)
        nodes, edges = [], []
        nodes.append({"id": "user",  "label": "用户请求", "group": "io",   "status": "ok"})
        nodes.append({"id": "brain", "label": "主脑 Brain", "group": "core",
                      "status": "ok",
                      "metric": getattr(brain, "calls", 0) if brain else 0})
        for n in skills:
            h = health.get(n) or {}
            nodes.append({"id": n, "label": n, "group": "skill",
                          "status": "ok" if h.get("ok") else "off",
                          "metric": _skill_count(reg, n)})
            edges.append({"from": "brain", "to": n})
        for cid in caps:                        # ← 53 项离线能力也进拓扑（原先整段缺失）
            h = health.get(cid) or {}
            nodes.append({"id": cid, "label": cid, "group": "octop",
                          "status": "ok" if h.get("ok") else "off",
                          "metric": _skill_count(reg, cid)})
            edges.append({"from": "brain", "to": cid})
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


# ── ③ 能力网格（原生 9 条 + Octop 离线 53 条，一张表全列）──
@router.get("/capabilities")
def capabilities():
    try:
        reg = _reg()
        _skills, caps, _brain, _led = _reg_maps(reg)
        health = _reg_health(reg)
        rows = _q("""SELECT skill, COUNT(*) calls, SUM(ok) ok, AVG(ms) ms
                     FROM skill_calls GROUP BY skill""")
        stats = {r["skill"]: r for r in rows}

        def _row(name, kind, backend, h, extra=None):
            s = stats.get(name.split("@")[0], {})
            r = {"name": name, "kind": kind, "backend": backend,
                 "ok": bool(h.get("ok")), "reason": h.get("reason") or "未探测",
                 "calls": s.get("calls", 0),
                 "rate": round((s.get("ok") or 0) / s["calls"], 3) if s.get("calls") else None,
                 "avg_ms": round(s.get("ms") or 0)}
            r.update(extra or {})
            return r

        out = []
        for name, meta in NATIVE_POTATO_CAPS.items():   # ← 本文件顶部把它 import as NATIVE_POTATO_CAPS；
            #   原版这里写 NATIVE_CAPABILITIES ⇒ NameError ⇒ 本接口从来只回 error 信封（真机复合病因之一）
            h = health.get(name) or health.get(meta["cls"].split(":")[1]) or {}
            out.append(_row(name, meta["kind"], meta["backend"], h, {"source": "native"}))
        for cid, cap in caps.items():          # ← 这一段原先不存在：53 项 Octop 能力从没上过表
            h = health.get(cid) or {}
            out.append(_row(cid, "octop/offline", None, h,
                            {"source": "octop-offline",
                             "equivalence": getattr(cap, "equivalence", ""),
                             "version": getattr(cap, "version", ""),
                             "description": getattr(cap, "description", ""),
                             "runnable": hasattr(reg, "run")}))
        return _env(out)
    except Exception as e:
        return _env(error=str(e))


@router.get("/capabilities/{cap_id}")
def capability_detail(cap_id: str):
    try:
        reg = _reg()
        _skills, caps, _brain, _led = _reg_maps(reg)
        cap = caps.get(cap_id)
        if cap is None:
            return _env(None, error=f"unknown capability: {cap_id}")
        h = _reg_health(reg).get(cap_id, {})
        man = [m for m in (reg.manifest() if hasattr(reg, "manifest") else [])
               if m.get("id") == cap_id]
        return _env({"id": cap_id, "ok": bool(h.get("ok")),
                     "reason": h.get("reason", "未探测"),
                     "equivalence": getattr(cap, "equivalence", ""),
                     "version": getattr(cap, "version", ""),
                     "description": getattr(cap, "description", ""),
                     "manifest": man[0] if man else None})
    except Exception as e:
        return _env(error=str(e))


@router.post("/capabilities/{cap_id}/run")
def capability_run(cap_id: str, payload: dict = Body(default={})):
    """真操作一条能力（Octop 离线能力的唯一门）。

    纪律：ok/output/error 分开原样回给调用方（失败绝不写成成功）；落一行 skill_calls 账，
          面板的「流水线 / 最近调用」才看得见它跑过；写账失败不吞掉能力结果，也不伪报成功。
    """
    t0 = time.time()
    try:
        reg = _reg()
        _skills, caps, _brain, led = _reg_maps(reg)
        if cap_id not in caps:
            return _env(error=f"unknown capability: {cap_id}")
        if not hasattr(reg, "run"):
            return _env(error=f"{type(reg).__name__} 不支持 run()")
        r = reg.run(cap_id, payload or {})
        ms = int(r.usage.get("ms") or (time.time() - t0) * 1000)
        d = {"ok": bool(r.ok), "output": r.output, "error": r.error,
             "warnings": list(r.warnings or []), "artifacts": list(r.artifacts or []),
             "usage": dict(r.usage or {})}
        _audit_cap(led, cap_id, payload, d, ms)
        return _env(d)
    except Exception as e:
        return _env(error=f"{type(e).__name__}: {e}")


def _audit_cap(led, cap_id, req, d, ms):
    """能力调用落账（照 skills/native.py:_audit 的口径，写失败只吞自己不吞结果）。"""
    if led is None:
        return
    try:
        ph = "?" if getattr(led, "dialect", "sqlite") == "sqlite" else "%s"
        sql = ("INSERT INTO skill_calls(ts,skill,version,tentacle,task_id,ok,ms,engine,"
               "request,result,artifacts,warnings,error) VALUES(" + ",".join([ph] * 13) + ")")
        with txn(led) as c:
            c.execute(sql, (time.time(), cap_id, "offline-1.0", "panel", "",
                            1 if d["ok"] else 0, ms, "octop-offline",
                            json.dumps(req, ensure_ascii=False, default=str)[:4000],
                            json.dumps(d.get("output"), ensure_ascii=False, default=str)[:4000],
                            json.dumps(d.get("artifacts"), ensure_ascii=False)[:2000],
                            json.dumps(d.get("warnings"), ensure_ascii=False)[:1000],
                            d.get("error") or ""))
    except Exception as e:
        _swallow(__file__, e)


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
