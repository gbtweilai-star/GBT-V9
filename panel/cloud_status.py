# panel/cloud_status.py —— 云插件：连接状态表 + 共享资源速度（真实口径，窗口内无数据不编）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 数据来源（全部是**真实可观测**的表，没有一处推算/编造）：
#   fleet_drive     —— 触手被指挥官驱动的历史（时间、耗时 ms、tokens、成败）→ "链路速率"
#   read_tool_audit —— 只读工具调用历史（哪个域被问了几次）→ "共享资源调用"
#   cloud_binding   —— 触手↔插件双向绑定（谁是链路）
#   cloud_plugin_state —— 插件启用开关
#   cloud_share     —— 插件↔插件内部互绑共享
# 口径写死在本文件里，页面照抄展示：窗口 = 近 N 秒，桶 = N/12，速率单位 = 次/分钟。
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from common.db import get_db, fetch_all

router = APIRouter(prefix="/api/cloud")

WINDOW_DEFAULT = int(600)          # 近 10 分钟
BUCKETS = 12
SPEED_UNIT = "次/分钟"


def _window(win: int | None) -> int:
    try:
        w = int(win or WINDOW_DEFAULT)
    except (TypeError, ValueError):
        w = WINDOW_DEFAULT
    return max(60, min(w, 24 * 3600))


@router.get("/speed")
async def cloud_speed(window: int = WINDOW_DEFAULT, db=Depends(get_db)):
    """共享资源速度：总速率 / 分桶趋势 / Top-N 热链 / 共享资源调用。
    窗口内没有任何调用 → 全 0 + no_calls_in_window=true（不编造速度）。
    """
    win = _window(window)
    now = time.time()
    since_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - win))
    out: dict = {"window_s": win, "buckets": BUCKETS, "unit": SPEED_UNIT,
                 "since": since_iso, "no_calls_in_window": False}
    # ① 链路驱动（fleet_drive 在扫描账本：tentacle_ledger.db，不在面板只读库）
    rows = []
    try:
        from senses.sqldialect import txn
        import panel.server as _srv
        led = _srv.get_ledger()
        if led is not None:
            with txn(led) as cur:
                cur.execute("SELECT tentacle_id, at, ms, tokens, ok FROM fleet_drive"
                            " WHERE at >= ? ORDER BY at ASC", (since_iso,))
                cols = [d[0] for d in (cur.description or [])]
                rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:                                   # noqa: BLE001
        return {**out, "error": f"fleet_drive 读不到（{type(exc).__name__}）",
                "note": "表不存在说明还没驱动过触手"}
    rows = rows or []
    out["calls"] = len(rows)
    if not rows:
        out["no_calls_in_window"] = True
        out["rate_per_min"] = 0.0
        out["buckets_series"] = [0] * BUCKETS
        out["top_links"] = []
        out["avg_ms"] = 0
        out["tokens"] = 0
        out["ok_rate"] = None
        out["shared_calls"] = []
        out["note"] = ("窗口内没有触手驱动记录：速度按 0 报，并把原因写在这里"
                       "（不是「没数据当 0 算」）。")
        return out
    # ② 分桶（按 at 的 epoch 归桶）
    bucket_s = max(1, win // BUCKETS)
    series = [0] * BUCKETS
    t0 = now - win
    per_tent: dict = {}
    ok_n = 0
    tokens = 0
    ms_sum = 0
    for r in rows:
        try:
            from common.timeutil import to_epoch
            ep = float(to_epoch(r["at"]))
        except Exception:                                      # noqa: BLE001
            continue
        idx = int((ep - t0) // bucket_s)
        if 0 <= idx < BUCKETS:
            series[idx] += 1
        tid = str(r["tentacle_id"] or "-")
        e = per_tent.setdefault(tid, {"calls": 0, "ms": 0, "tokens": 0, "ok": 0})
        e["calls"] += 1
        e["ms"] += int(r["ms"] or 0)
        e["tokens"] += int(r["tokens"] or 0)
        e["ok"] += 1 if r["ok"] else 0
        ms_sum += int(r["ms"] or 0)
        tokens += int(r["tokens"] or 0)
        ok_n += 1 if r["ok"] else 0
    out.update({"rate_per_min": round(len(rows) / (win / 60.0), 2),
                "buckets_series": series, "bucket_seconds": bucket_s,
                "avg_ms": int(ms_sum / len(rows)), "tokens": tokens,
                "ok_rate": round(ok_n / len(rows), 4)})
    # ③ Top-N 热链（按调用数）
    top = sorted(per_tent.items(), key=lambda kv: -kv[1]["calls"])[:8]
    out["top_links"] = [{"tentacle": t, "calls": v["calls"],
                         "rate_per_min": round(v["calls"] / (win / 60.0), 2),
                         "avg_ms": int(v["ms"] / max(1, v["calls"])),
                         "tokens": v["tokens"],
                         "ok_rate": round(v["ok"] / max(1, v["calls"]), 3)}
                        for t, v in top]
    # ④ 共享资源调用（只读工具按域）
    try:
        srows = await fetch_all(db, """SELECT domain, COUNT(*) AS n
            FROM read_tool_audit WHERE at >= ? GROUP BY domain
            ORDER BY n DESC""", [since_iso], db=db)
        out["shared_calls"] = [{"domain": r["domain"], "calls": int(r["n"]),
                                "rate_per_min": round(int(r["n"]) / (win / 60.0), 2)}
                               for r in (srows or [])]
    except Exception:                                          # noqa: BLE001
        out["shared_calls"] = []
    out["note"] = (f"速率 = 近 {win} 秒内的真实驱动次数 ÷ 分钟；桶 = {bucket_s} 秒；"
                   "共享调用 = 只读工具审计按域统计")
    return out


@router.get("/links")
async def cloud_links(q: str = "", group: str = "", only_on: int = 0, db=Depends(get_db)):
    """连接状态表：每个插件的族/槽/开关/IP 出口/绑定触手数/内部互绑数/连接状态。"""
    from core.cloud_plugins import (GROUP_CN, GROUPS, PLUGIN_IDS, egress_of, registry)
    em: dict = {}
    try:
        rows = await fetch_all(db, "SELECT plugin, enabled FROM cloud_plugin_state",
                               [], db=db)
        em = {r["plugin"]: int(r["enabled"] or 0) for r in (rows or [])}
    except Exception:                                          # noqa: BLE001
        em = {}
    bound: dict = {}
    try:
        rows = await fetch_all(db, """SELECT plugin, COUNT(DISTINCT tentacle) AS n
            FROM cloud_binding WHERE direction='t2p' GROUP BY plugin""", [], db=db)
        bound = {r["plugin"]: int(r["n"]) for r in (rows or [])}
    except Exception:                                          # noqa: BLE001
        bound = {}
    shared: dict = {}
    try:
        rows = await fetch_all(db, """SELECT a AS p, COUNT(DISTINCT b) AS n FROM cloud_share
            GROUP BY a""", [], db=db)
        for r in rows or []:
            shared[r["p"]] = shared.get(r["p"], 0) + int(r["n"])
    except Exception:                                          # noqa: BLE001
        shared = {}
    items = []
    for p in registry(enabled_map=em)["plugins"]:
        key = p["key"]
        b = bound.get(key, 0)
        s = shared.get(key, 0)
        if group and p["group"] != group:
            continue
        if only_on and not p["enabled"]:
            continue
        if q and (q.lower() not in key.lower() and q.lower() not in str(p["cn"]).lower()):
            continue
        state = ("预留" if p["reserved"] else
                 ("在岗·已绑" if (p["enabled"] and b) else
                  ("在岗·未绑" if p["enabled"] else "停用")))
        items.append({"key": key, "family": GROUP_CN[p["group"]], "group": p["group"],
                      "slot": p["slot"], "slug": p["slug"], "cn": p["cn"],
                      "enabled": bool(p["enabled"]), "egress": egress_of(key),
                      "bound_tentacles": b, "shared_links": s,
                      "model_ready": (not p["reserved"]) and bool(p["cf_id"]),
                      "state": state})
    return {"count": len(items), "total": len(PLUGIN_IDS), "items": items,
            "families": {g: GROUP_CN[g] for g in GROUPS},
            "columns": ["槽位/模型", "族", "启用", "IP 出口", "绑定触手",
                        "内部互绑", "模型状态", "连接状态"]}
