# panel/workflows_api.py —— 能力链 / DAG 的 HTTP 入口（原先整段缺失）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 逐行复核）：workflows/engine.py 有 $ref 串联能力（:15 正则 / :159-163 取上游 /
#   :53-64 校验必须指向上游），但**只挂 CLI**；唯一提到 HTTP 的那段代码被错放进 workflows/store.py
#   （从自己 import 自己、router/_env 未定义、无人 include）⇒ 页面上根本没有“把能力串起来跑”的门。
# 本文件把这些路由建在 panel_api 的同一只 router 上（同一前缀 /api/panel、同一份注入的注册表），
# 不再新造第三套调度：串行链只是在**同一个 WorkflowEngine** 前面加一层「线性步骤 → DAG」的糖。
# 纪律：明文凭据一律拒（scan_flow_secrets）；写 flow 走乐观锁（VersionConflict → 409）；
#       返回体沿用 panel_api 的 {data,updated_at,stale,error} 信封，失败不伪报成功。
from __future__ import annotations

from fastapi import Body, HTTPException

from panel.panel_api import _env, router
from skills.spec import scan_flow_secrets
from workflows.catalog import build_catalog, spec_of
from workflows.engine import WorkflowEngine
from workflows.store import (VersionConflict, delete_flow, get_flow, list_flows,
                             save_flow)


def _engine() -> WorkflowEngine:
    gate = getattr(router, "wf_gate", None)
    return WorkflowEngine(router.registry, ledger=router.ledger, gate=gate)


def linear_to_flow(steps: list) -> dict:
    """线性步骤 → DAG：s0→s1→…，每步自动吃上一步的输出（无需调用方猜键名）。"""
    nodes, edges, prev = [], [], None
    for i, st in enumerate(steps or []):
        nid = str(st.get("id") or f"s{i}")
        node = {"id": nid, "type": "skill", "skill": st.get("skill"),
                "inputs": dict(st.get("inputs") or {})}
        if prev and st.get("pipe", True):
            node["pipe"] = prev                 # 上一步的输出当请求体（显式 inputs 覆盖它）
            edges.append([prev, nid])
        nodes.append(node)
        prev = nid
    return {"id": "chain", "nodes": nodes, "edges": edges}


# ── 静态路径必须先注册，否则会被 /workflows/{fid} 吃掉 ──
@router.get("/workflows/catalog")
def wf_catalog():
    try:
        return _env(build_catalog(router.registry))
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.get("/workflows/spec/{skill}")
def wf_spec(skill: str):
    try:
        return _env(spec_of(router.registry, skill))
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.post("/workflows/validate")
def wf_validate(payload: dict = Body(default={})):
    try:
        flow = payload.get("definition") or payload.get("flow") or {}
        errs = _engine().validate(flow)
        return _env({"ok": not errs, "errors": errs})
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.post("/workflows/run")
def wf_run_inline(payload: dict = Body(default={})):
    """能力链的唯一执行门：既收完整 DAG（definition），也收线性步骤（steps）。"""
    try:
        flow = payload.get("definition") or payload.get("flow")
        if not flow and payload.get("steps"):
            flow = linear_to_flow(payload["steps"])
        if not flow:
            return _env(error="缺 definition / flow / steps，三选一")
        hits = scan_flow_secrets(flow)
        if hits:
            return _env(error=f"flow 含疑似明文凭据，请改用 $secret:NAME（{hits[:2]}）")
        out = _engine().run(flow, payload.get("inputs"),
                            trace_id=payload.get("run_id"))
        return _env(out)
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.post("/workflows/chain")
def wf_chain(payload: dict = Body(default={})):
    """线性能力链：steps=[{skill,inputs?},…]，上一步输出自动喂下一步。"""
    payload = dict(payload or {})
    if not payload.get("definition") and not payload.get("flow"):
        payload["steps"] = payload.get("steps") or []
    return wf_run_inline(payload)


@router.get("/workflows")
def wf_list():
    try:
        return _env(list_flows(router.ledger))
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.get("/workflows/{fid}")
def wf_get(fid: str):
    try:
        f = get_flow(router.ledger, fid)
        return _env(f, error=None if f else "flow 不存在")
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.put("/workflows/{fid}")
def wf_save(fid: str, payload: dict = Body(default={})):
    try:
        hits = scan_flow_secrets(payload.get("definition", {}))
        if hits:
            return _env(error=f"flow 含疑似明文凭据，请改用 $secret:NAME（{hits[:2]}）")
        r = save_flow(router.ledger, fid, payload.get("name", fid),
                      payload.get("definition") or {}, payload.get("version"))
        return _env(r)
    except VersionConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.delete("/workflows/{fid}")
def wf_delete(fid: str):
    try:
        return _env({"deleted": delete_flow(router.ledger, fid)})
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.post("/workflows/{fid}/run")
def wf_run(fid: str, payload: dict = Body(default={})):
    try:
        f = get_flow(router.ledger, fid)
        if not f:
            return _env(error="flow 不存在")
        out = _engine().run(f["definition"], payload.get("inputs"),
                            trace_id=payload.get("run_id"))
        return _env(out)
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")


@router.get("/workflow-runs/{run_id}")
def wf_run_get(run_id: str):
    try:
        import json as _j
        from senses.sqldialect import txn as _txn
        ph = "?" if router.ledger.dialect == "sqlite" else "%s"
        with _txn(router.ledger) as cur:
            cur.execute(f"SELECT trace_id,flow_id,ts,ok,steps FROM workflow_runs "
                        f"WHERE trace_id={ph}", (run_id,))
            r = cur.fetchone()
        if not r:
            return _env(None, error="run 不存在")
        return _env({"run_id": r[0], "flow_id": r[1], "ts": r[2],
                     "ok": bool(r[3]), "steps": _j.loads(r[4] or "[]")})
    except Exception as e:                                    # noqa: BLE001
        return _env(error=f"{type(e).__name__}: {e}")
