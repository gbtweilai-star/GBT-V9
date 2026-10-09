# panel/routes/fleet.py —— 触手编队只读面（规模/统一密钥指纹/驱动审计）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律：面板只读。这里不驱动任何触手，只看"编队配好了没、每根是不是同一把钥匙、
#       每次驱动是谁指挥的"。驱动一律走 tools/fleet_cli.py（引擎侧，带指挥官工单）。
from fastapi import APIRouter, Depends

from common.db import get_db, fetch_all

router = APIRouter(prefix="/api/fleet")


def _config_view() -> dict:
    """编队配置视图：只暴露密钥指纹，绝不回显原文。"""
    try:
        from core.tentacle_fleet import FLEET_DEFAULT_N, TentacleFleet
        f = TentacleFleet(ledger=None)              # 只装配不驱动（面板侧零副作用）
        st = f.status()
        return {"n": st["n"], "model": st["model"], "rpm": st["rpm"],
                "key_id": st["key_id"], "key_loaded": st["key_loaded"],
                "key_source": st["key_source"], "same_key": st["same_key"],
                "commander": st["commander"], "mode": st["mode"],
                "configured_n": FLEET_DEFAULT_N,
                "roles": {r: sum(1 for t in f.tentacles.values() if t.role == r)
                          for r in sorted({t.role for t in f.tentacles.values()})}}
    except Exception as exc:                        # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


@router.get("/status")
async def fleet_status(db=Depends(get_db)):
    """编队规模 + 统一密钥指纹 + 驱动统计（读数来自 fleet_drive 审计表）。"""
    cfg = _config_view()
    try:
        rows = await fetch_all(db, """SELECT COUNT(*) AS drives,
                COALESCE(SUM(ok),0) AS ok, COALESCE(SUM(tokens),0) AS tokens,
                COUNT(DISTINCT tentacle_id) AS tentacles_used,
                COUNT(DISTINCT key_id) AS key_ids
            FROM fleet_drive""", [], db=db)
        row = rows[0] if rows else {}
    except Exception as exc:                        # noqa: BLE001
        row = {"error": f"{type(exc).__name__}: {exc}"}
    return {"config": cfg, "drives": row}


@router.get("/drives")
async def fleet_drives(limit: int = 20, db=Depends(get_db)):
    """最近驱动审计：哪根触手、什么任务、谁指挥、用哪把钥匙（指纹）、成败与耗时。"""
    limit = max(1, min(int(limit or 20), 200))
    try:
        rows = await fetch_all(db, """SELECT tentacle_id, task, ok, reason, tokens, ms,
                key_id, issued_by, trace_id, at
            FROM fleet_drive ORDER BY at DESC LIMIT ?""", [limit], db=db)
    except Exception as exc:                        # noqa: BLE001
        return {"items": [], "error": f"{type(exc).__name__}: {exc}"}
    return {"items": rows, "limit": limit}
