# panel/panel_api.py
from fastapi import Body, HTTPException
from workflows.catalog import build_catalog, spec_of
from workflows.store import list_flows, get_flow, save_flow, VersionConflict
from workflows.engine import WorkflowEngine
from skills.spec import scan_flow_secrets

@router.get("/workflows/catalog")
def wf_catalog():
    try:
        return _env(build_catalog(router.registry))
    except Exception as e:
        return _env(error=str(e))

@router.get("/workflows")
def wf_list():
    try:
        return _env(list_flows(router.ledger))
    except Exception as e:
        return _env(error=str(e))

@router.get("/workflows/{fid}")
def wf_get(fid: str):
    try:
        f = get_flow(router.ledger, fid)
        return _env(f, error=None if f else "flow 不存在")
    except Exception as e:
        return _env(error=str(e))

@router.put("/workflows/{fid}")
def wf_save(fid: str, payload: dict = Body(...)):
    try:
        hits = scan_flow_secrets(payload.get("definition", {}))
        if hits:
            return _env(error=f"flow 含疑似明文凭据，请改用 $secret:NAME（{hits[:2]}）")
        r = save_flow(router.ledger, fid, payload.get("name", fid),
                      payload["definition"], payload.get("version"))
        return _env(r)
    except VersionConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        return _env(error=str(e))

@router.post("/workflows/validate")
def wf_validate(payload: dict = Body(...)):
    try:
        eng = WorkflowEngine(router.registry)
        errs = eng.validate(payload.get("definition", {}))
        return _env({"ok": not errs, "errors": errs})
    except Exception as e:
        return _env(error=str(e))

@router.post("/workflows/{fid}/run")
def wf_run(fid: str, payload: dict = Body(default={})):
    try:
        f = get_flow(router.ledger, fid)
        if not f:
            return _env(error="flow 不存在")
        eng = WorkflowEngine(router.registry, ledger=router.ledger,
                             gate=(router.wf_gate if hasattr(router, "wf_gate")
                                   else None))
        out = eng.run(f["definition"], payload.get("inputs"),
                      trace_id=payload.get("run_id"))
        return _env(out)
    except Exception as e:
        return _env(error=str(e))

@router.get("/workflow-runs/{run_id}")
def wf_run_get(run_id: str):
    try:
        ph = "?" if router.ledger.dialect == "sqlite" else "%s"
        with router.ledger._tx() as c, c.cursor() as cur:
            cur.execute(f"SELECT trace_id,flow_id,ts,ok,steps FROM workflow_runs "
                        f"WHERE trace_id={ph}", (run_id,))
            r = cur.fetchone()
        if not r:
            return _env(None, error="run 不存在")
        import json as _j
        return _env({"run_id": r[0], "flow_id": r[1], "ts": r[2], "ok": bool(r[3]),
                     "steps": _j.loads(r[4])})
    except Exception as e:
        return _env(error=str(e))

@router.get("/workflows/spec/{skill}")
def wf_spec(skill: str):
    try:
        return _env(spec_of(router.registry, skill))
    except Exception as e:
        return _env(error=str(e))
