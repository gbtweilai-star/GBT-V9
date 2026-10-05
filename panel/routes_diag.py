# panel/routes_diag.py
from fastapi import APIRouter, Depends, Request
from panel.db import get_db
from panel.dbmetrics import METRICS, POOL_SNAPSHOT, ALERTS

router = APIRouter(prefix="/api/monitor", tags=["monitor"])


@router.get("/db-health")
async def db_health(request: Request, db=Depends(get_db)):
    pool = await POOL_SNAPSHOT.read(request.app)
    metrics = METRICS.snapshot()

    sample = {**pool,
              "p95_ms": metrics["p95_ms"],
              "slow_total": metrics["slow_total"],
              "errors": metrics["errors"],
              "busy_locked": metrics["busy_locked"]}
    decision = ALERTS.evaluate(sample)

    if decision["emit"]:                     # ★只有状态变化才写库，且走账本写连接
        try:
            from audit.ledger import get_ledger
            await get_ledger().record_blocked(       # ← 对齐点
                source="panel_db",
                level=decision["level"],
                reasons=decision["reasons"],
                observed=sample)
        except Exception:
            pass                              # 写失败不影响返回

    return {"data": {
        "level": decision["level"],
        "reasons": decision["reasons"],
        "changed": decision["changed"],
        "pool": pool,
        "metrics": metrics,
        "thresholds": {
            "slow_ms": SLOW_MS,
            "pool_warn": ALERTS.cfg.pool_warn, "pool_crit": ALERTS.cfg.pool_crit,
            "pool_recover": ALERTS.cfg.pool_recover,
            "enter_windows": ALERTS.cfg.enter_windows,
            "exit_windows": ALERTS.cfg.exit_windows,
            "cooldown_s": ALERTS.cfg.cooldown_s,
        },
    }}
