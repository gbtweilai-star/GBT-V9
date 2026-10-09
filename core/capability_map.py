# core/capability_map.py —— 总能力图表 + 连接状态 + 云插件/数据库精准用量
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律（对应主人要求"图表 + 连接状态 + 用量必须精准不能出错"）：
#   - 每个数字要么来自**真实查询**（observed + 表名），要么明确标 unavailable + 原因；
#     **绝不把查不到写成 0**（0 与"不知道"是两件事）。
#   - 总数（分母）取真实模块读数：云插件 = cloud_plugins.PLUGIN_IDS，数据库 = db_fleet.SLOT_IDS，
#     Octop 能力 = octop_bridge.catalog()，触手 = 编队规模，算力活 = compute_router.WORKLOADS。
#   - 图表 = "总能力"（各子系统能力总数）与"已连接"（双向绑定/启用数）；连接状态含来源表。
#   - 固化/回滚走 core.solidify（版本 + sha256 + 一键回滚）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import os
import time

from common.ttl_cache import TTLCache as _TTL

from core import compute_router as _cr
from core import solidify as _solid


_TOTALS_TTL = float(os.environ.get("V9_TOTALS_TTL", "300"))
_TOTALS_CACHE = _TTL(ttl=_TOTALS_TTL, name="totals")


def _totals(*, fresh: bool = False) -> dict:
    """分母：全部来自真实模块读数（取不到则 None，不编）。

    这些是**清单规模**（云插件槽/库槽/Octop 能力/编队规模/读帧插件/只读工具），
    一个会话里不会变；但每次读要新建 100 根触手编队 + 扫多个模块 —— 实测 5.8 秒，
    面板每刷一次就重算一遍（还把事件循环堵住）。所以带 TTL 缓存：
    值仍是真读数，过期也先给旧值、后台刷新（页面不准卡）。
    """
    if fresh:
        return _compute_totals()
    return _TOTALS_CACHE.get("v", _compute_totals)


def _compute_totals() -> dict:
    out = {}
    try:
        from core.cloud_plugins import PLUGIN_IDS
        out["云插件"] = len(PLUGIN_IDS)
    except Exception as exc:                              # noqa: BLE001
        out["云插件"] = None
    try:
        from core.db_fleet import SLOT_IDS
        out["数据库槽"] = len(SLOT_IDS)
    except Exception:                                     # noqa: BLE001
        out["数据库槽"] = None
    try:
        from core.octop_bridge import catalog
        out["Octop 能力"] = int((catalog() or {}).get("counts", {}).get("total_capabilities")
                               or 0) or None
    except Exception:                                     # noqa: BLE001
        out["Octop 能力"] = None
    try:
        from core.tentacle_fleet import TentacleFleet
        f = TentacleFleet()
        out["触手"] = len(getattr(f, "tentacles", []) or [])
    except Exception:                                     # noqa: BLE001
        out["触手"] = None
    out["算力活"] = len(_cr.WORKLOADS)
    try:
        from senses import frame_readers as fr
        out["读帧插件"] = len(getattr(fr, "READERS", []) or []) or None
    except Exception:                                     # noqa: BLE001
        out["读帧插件"] = None
    try:
        import body.tools.base as tb
        tb.ensure_registered()                            # 显式注册，别依赖 import 顺序
        out["只读工具"] = len(getattr(tb, "TOOLS", {}) or {}) or None
    except Exception:                                     # noqa: BLE001
        out["只读工具"] = None
    return out


def _m(value, source: str, *, where: str = "", why: str = "") -> dict:
    """一格读数：observed（有表名）或 unavailable（有原因）。"""
    if value is None:
        return {"value": None, "source": "unavailable", "why": why or "读取失败"}
    return {"value": int(value), "source": source, "where": where}


async def _counts(db) -> dict:
    """真实计数：每项独立 try，读不到就 unavailable（不写 0）。

    口径关键：绑定表**两个方向各存一行**（cloud/db 用 t2d·d2t，octop 用 t2c·c2t，
    share 用 a2b·b2a）。所以「绑定对数」必须按方向取（= 真实配对数），
    另报「双向行数」（= 对数 × 2），两者都写明来源，避免把 2 倍行数当成配对数。
    """
    from common.db import fetch_all
    got = {}

    async def one(key, table, sql):
        try:
            rows = await fetch_all(db, sql)
            got[key] = _m(rows[0]["n"] if rows else 0, "observed", where=table)
        except Exception as exc:                          # noqa: BLE001
            got[key] = _m(None, "unavailable", why=f"{type(exc).__name__}", where=table)

    await one("云插件启用", "cloud_plugin_state",
              "SELECT COUNT(*) AS n FROM cloud_plugin_state WHERE enabled=1")
    await one("云插件绑定对", "cloud_binding(direction=t2p)",
              "SELECT COUNT(*) AS n FROM cloud_binding WHERE direction='t2p'")
    await one("云插件绑定去重对", "cloud_binding(direction=t2p, distinct)",
              "SELECT COUNT(DISTINCT tentacle||'|'||plugin) AS n FROM cloud_binding"
              " WHERE direction='t2p'")
    await one("云插件双向行", "cloud_binding",
              "SELECT COUNT(*) AS n FROM cloud_binding")
    await one("云插件互绑对", "cloud_share(direction=a2b)",
              "SELECT COUNT(*) AS n FROM cloud_share WHERE direction='a2b'")
    await one("云插件互绑去重对", "cloud_share(direction=a2b, distinct)",
              "SELECT COUNT(DISTINCT a||'|'||b) AS n FROM cloud_share WHERE direction='a2b'")
    await one("库槽启用", "db_slot_state",
              "SELECT COUNT(*) AS n FROM db_slot_state WHERE enabled=1")
    await one("库槽绑定对", "db_binding(direction=t2d)",
              "SELECT COUNT(*) AS n FROM db_binding WHERE direction='t2d'")
    await one("库槽绑定去重对", "db_binding(direction=t2d, distinct)",
              "SELECT COUNT(DISTINCT tentacle||'|'||slot) AS n FROM db_binding"
              " WHERE direction='t2d'")
    await one("库槽双向行", "db_binding",
              "SELECT COUNT(*) AS n FROM db_binding")
    await one("Octop 绑定对", "octop_binding(direction=c2t)",
              "SELECT COUNT(*) AS n FROM octop_binding WHERE direction='c2t'")
    await one("Octop 绑定去重对", "octop_binding(direction=c2t, distinct)",
              "SELECT COUNT(DISTINCT tentacle||'|'||capability) AS n FROM octop_binding"
              " WHERE direction='c2t'")
    await one("Octop 双向行", "octop_binding",
              "SELECT COUNT(*) AS n FROM octop_binding")
    return got


def _ledger_counts(led) -> dict:
    """扫描账本里的真实读数（fleet_drive 等）。led._tx() 给的是**连接**，不是游标。

    每条查询都**字面量内联**在 execute 处（不走变量/形参传递）：既是安全纪律，
    也避免扫描器把"SQL 走参数"误判成拼接。
    """
    got = {}
    if led is None:
        return {k: _m(None, "unavailable", why="无账本连接") for k in
                ("驱动次数", "驱动成功", "驱动失败", "平均耗时ms")}
    try:
        with led._tx() as con:                            # noqa: SLF001
            row = con.execute("SELECT COUNT(*) AS n FROM fleet_drive").fetchone()
        got["驱动次数"] = _m(row[0] if row else 0, "observed", where="fleet_drive")
    except Exception as exc:                              # noqa: BLE001
        got["驱动次数"] = _m(None, "unavailable", why=f"{type(exc).__name__}",
                             where="fleet_drive")
    try:
        with led._tx() as con:                            # noqa: SLF001
            row = con.execute(
                "SELECT COUNT(*) AS n FROM fleet_drive WHERE ok=1").fetchone()
        got["驱动成功"] = _m(row[0] if row else 0, "observed", where="fleet_drive.ok=1")
    except Exception as exc:                              # noqa: BLE001
        got["驱动成功"] = _m(None, "unavailable", why=f"{type(exc).__name__}",
                             where="fleet_drive.ok=1")
    try:
        with led._tx() as con:                            # noqa: SLF001
            row = con.execute(
                "SELECT COUNT(*) AS n FROM fleet_drive WHERE ok=0").fetchone()
        got["驱动失败"] = _m(row[0] if row else 0, "observed", where="fleet_drive.ok=0")
    except Exception as exc:                              # noqa: BLE001
        got["驱动失败"] = _m(None, "unavailable", why=f"{type(exc).__name__}",
                             where="fleet_drive.ok=0")
    try:
        with led._tx() as con:                            # noqa: SLF001
            row = con.execute(
                "SELECT AVG(ms) AS a FROM fleet_drive WHERE ms IS NOT NULL").fetchone()
        v = row[0] if row else None
        got["平均耗时ms"] = _m(round(v) if v is not None else None,
                               "observed" if v is not None else "unavailable",
                               where="fleet_drive.ms", why="窗口内无耗时样本")
    except Exception as exc:                              # noqa: BLE001
        got["平均耗时ms"] = _m(None, "unavailable", why=f"{type(exc).__name__}",
                               where="fleet_drive.ms")
    return got


def _db_files() -> dict:
    """100 个库的**真实磁盘占用**（db_fleet 落盘位置），逐个 stat。"""
    import os
    out = {"库文件数": None, "库总字节": None, "why": "读取失败"}
    try:
        from core.db_fleet import DB_ROOT as _DB_ROOT
        root = str(_DB_ROOT)
        n, total = 0, 0
        for base, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".sqlite3"):
                    n += 1
                    try:
                        total += os.path.getsize(os.path.join(base, f))
                    except OSError as e:
                        _swallow(__file__, e)
        out = {"库文件数": n, "库总字节": total, "root": root, "why": ""}
    except Exception as exc:                              # noqa: BLE001
        out["why"] = f"{type(exc).__name__}"
    return out


def _dup(rows_val, uniq_val) -> dict:
    """重复行 = 按方向的行数 − 去重对数（0 表示无重复；>0 就是真的重复绑定了）。"""
    if rows_val is None or uniq_val is None:
        return _m(None, "unavailable", why="缺行数或去重对数")
    return _m(max(0, int(rows_val) - int(uniq_val)), "computed",
              where="行数 − 去重对数", why="")


async def usage(db, led=None) -> dict:
    """云插件 + 数据库的**精准用量**（逐项带来源；未知 = unavailable + 原因）。"""
    counts = await _counts(db)
    files = _db_files()
    totals = _totals()
    cv = lambda k: (counts.get(k) or {}).get("value")           # noqa: E731
    cp_uniq, cl_uniq = cv("云插件绑定去重对"), cv("云插件互绑去重对")
    plugin_usage = {
        "总数": _m(totals.get("云插件"), "observed", where="cloud_plugins.PLUGIN_IDS"),
        "启用数": counts["云插件启用"],
        "插入绑定对数": counts["云插件绑定对"],
        "插入绑定去重对数": counts["云插件绑定去重对"],
        "插入绑定重复行": _dup(cv("云插件绑定对"), cp_uniq),
        "双向绑定行数": counts["云插件双向行"],
        "内部互绑对数": counts["云插件互绑对"],
        "内部互绑去重对数": counts["云插件互绑去重对"],
        "内部互绑重复行": _dup(cv("云插件互绑对"), cl_uniq),
        "理论插入绑定上限": _m((totals.get("云插件") or 0) * (totals.get("触手") or 0),
                               "computed", where="云插件 × 触手") if (totals.get("云插件")
                                                       and totals.get("触手"))
        else _m(None, "unavailable", why="缺少云插件或触手总数"),
    }
    nu, du = cv("库槽绑定对"), cv("库槽绑定去重对")
    db_usage = {
        "总数": _m(totals.get("数据库槽"), "observed", where="db_fleet.SLOT_IDS"),
        "启用数": counts["库槽启用"],
        "插入绑定对数": counts["库槽绑定对"],
        "插入绑定去重对数": counts["库槽绑定去重对"],
        "插入绑定重复行": _dup(nu, du),
        "双向绑定行数": counts["库槽双向行"],
        "超出理论上限": (_m(max(0, int(nu) - (totals.get("数据库槽") or 0)
                               * (totals.get("触手") or 0)), "computed",
                            where="绑定对数 − 库槽×触手")
                         if (nu is not None and totals.get("数据库槽")
                             and totals.get("触手")) else _m(None, "unavailable",
                                                            why="缺分母")),
        "磁盘文件数": _m(files.get("库文件数"), "observed", where=files.get("root", ""),
                          why=files.get("why")) if files.get("库文件数") is not None
        else _m(None, "unavailable", why=files.get("why")),
        "磁盘总字节": _m(files.get("库总字节"), "observed", where=files.get("root", ""),
                         why=files.get("why")) if files.get("库总字节") is not None
        else _m(None, "unavailable", why=files.get("why")),
    }
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "云插件用量": plugin_usage, "数据库用量": db_usage,
            "驱动用量": _ledger_counts(led),
            "口径": "逐项标注 source：observed=真实查询，computed=由两项相乘，"
                    "unavailable=读不到（附原因，不写 0）"}


async def connection_status(db, led=None) -> dict:
    """连接状态：子系统 → 总数 / 已连接 / 状态 / 证据（真实表名）。"""
    counts = await _counts(db)
    totals = _totals()
    led_counts = _ledger_counts(led)
    cloud_total, db_total = totals.get("云插件"), totals.get("数据库槽")
    tent_total = totals.get("触手")
    rows = [
        {"子系统": "触手 ↔ 云插件", "总数": cloud_total,
         "已连接": (counts["云插件绑定去重对"] or {}).get("value"),
         "证据": "cloud_binding（direction=t2p，去重对数）", "状态": ""},
        # 云插件内部互绑是**全排列**关系：理论上限 = C(n,2)。原实现没算出来，
        # 所以面板只能写"分母未知" —— 那就是显示层的缺陷，这里补上真分母。
        {"子系统": "云插件 ↔ 云插件",
         "总数": ((cloud_total * (cloud_total - 1)) // 2) if cloud_total else None,
         "已连接": (counts["云插件互绑去重对"] or {}).get("value"),
         "证据": "cloud_share（direction=a2b，去重对数；分母=插件数两两组合 C(n,2)）",
         "状态": ""},
        {"子系统": "触手 ↔ 数据库槽", "总数": db_total,
         "已连接": (counts["库槽绑定去重对"] or {}).get("value"),
         "证据": "db_binding（direction=t2d，去重对数）", "状态": ""},
        {"子系统": "触手 ↔ Octop 能力", "总数": totals.get("Octop 能力"),
         "已连接": (counts["Octop 绑定去重对"] or {}).get("value"),
         "证据": "octop_binding（direction=c2t，去重对数）", "状态": ""},
        {"子系统": "触手 × 驱动器", "总数": tent_total,
         "已连接": (led_counts.get("驱动次数") or {}).get("value"),
         "证据": "fleet_drive（历史驱动次数）", "状态": ""},
    ]
    for r in rows:
        c, t = r.get("已连接"), r.get("总数")
        if c is None:
            r["状态"] = "无法确认"
        elif t is None:
            r["状态"] = "已连接（分母未知）"
        elif t and c >= t:
            r["状态"] = "全通"
        elif c > 0:
            r["状态"] = "部分连通"
        else:
            r["状态"] = "未连接"
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "行": rows, "总数来源": totals, "驱动": led_counts}


async def chart(db, led=None) -> dict:
    """总能力图表数据：每个子系统的能力总数 + 已接通量 + 条形占比（逐项显式对应）。"""
    totals = _totals()
    counts = await _counts(db)
    conn = await connection_status(db, led)
    _v = lambda k: (counts.get(k) or {}).get("value")          # noqa: E731
    tent = totals.get("触手")
    bars = [
        # (名称, 自身数, 已接通, 证据, 分母是否 × 触手)
        ("云插件", totals.get("云插件"), _v("云插件绑定去重对"),
         "cloud_binding（触手↔插件，去重对数）", True),
        ("数据库槽", totals.get("数据库槽"), _v("库槽绑定去重对"),
         "db_binding（触手↔库槽，去重对数）", True),
        ("Octop 能力", totals.get("Octop 能力"), _v("Octop 绑定去重对"),
         "octop_binding（能力↔触手，去重对数）", True),
        ("触手", tent, tent, "tentacle_fleet 编队规模", False),
        # 这三类**按设计不与环境绑定**（按需调用），所以"就绪 = 自身可用数"，
        # 免得条形空白被误读成"未达标"。
        ("读帧插件", totals.get("读帧插件"), totals.get("读帧插件"),
         "senses.frame_readers.READERS（按设计不绑定，按需读取；就绪=全部）", False),
        ("算力活", totals.get("算力活"), totals.get("算力活"),
         "compute_router.WORKLOADS（按设计不绑定；路由表就绪=全部）", False),
        ("只读工具", totals.get("只读工具"), totals.get("只读工具"),
         "body.tools.domains（按设计不绑定；按需调用，就绪=已注册）", False),
    ]
    subs = []
    for name, total, on, ev, bind in bars:
        denom = (total * tent) if (bind and total and tent) else total
        subs.append({"名称": name, "总数": total, "分母": denom, "已接通": on, "证据": ev,
                     "条形占比": (round(min(1.0, on / denom), 4)
                                  if (on is not None and denom) else None)})
    usage_rows = await usage(db, led)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "通道": _cr.policy(), "子系统": subs, "连接状态": conn["行"],
            "驱动": conn["驱动"], "用量": usage_rows}


# ═══════════ 内置明细（每个能力组都做成 V9 页面内的面板，不外跳）═══════════
def _frame_readers() -> dict:
    """5 个读帧插件：元数据 + 真实读数（读不到就 ok=False + 原因，不报 0）。"""
    try:
        from senses import frame_readers as fr
        rows = []
        for key, meta in (getattr(fr, "PLUGINS", {}) or {}).items():
            entry = {"插件": key, "名称": meta.get("label", key),
                     "口径": meta.get("desc", ""), "读数": None, "ok": None, "原因": ""}
            try:
                res = (fr.READERS or {}).get(key)
                out = res() if callable(res) else None
                if isinstance(out, dict):
                    entry["ok"] = out.get("ok")
                    entry["原因"] = out.get("reason") or ""
                    entry["读数"] = {k: v for k, v in out.items()
                                     if k not in ("ok", "reason")}
            except Exception as exc:                      # noqa: BLE001
                entry["ok"] = False
                entry["原因"] = f"{type(exc).__name__}"
            rows.append(entry)
        return {"总数": len(rows), "行": rows}
    except Exception as exc:                              # noqa: BLE001
        return {"总数": None, "行": [], "原因": f"{type(exc).__name__}"}


def _read_tools(db_rows) -> dict:
    """只读工具：注册表 + 域 + 最近调用统计（来自 read_tool_audit）。"""
    try:
        import body.tools.base as tb
        tb.ensure_registered()                            # 显式注册，别依赖 import 顺序
        names = sorted((getattr(tb, "TOOLS", {}) or {}).keys())
        domains = getattr(tb, "DOMAINS", {}) or {}
        rows = [{"工具": n, "域": domains.get(n, "") if isinstance(domains, dict) else ""}
                for n in names]
        return {"总数": len(rows), "行": rows, "调用统计": db_rows}
    except Exception as exc:                              # noqa: BLE001
        return {"总数": None, "行": [], "调用统计": db_rows,
                "原因": f"{type(exc).__name__}"}


_BLE_TTL = float(os.environ.get("V9_BLE_BLOCK_TTL", "120"))
_BLE_CACHE = _TTL(ttl=_BLE_TTL, name="ble_block")


def _ble_block(*, fresh: bool = False) -> dict:
    """蓝牙（AI 操控）：适配器 + 设备数 + 写操作是否持授权；扫描失败如实写原因。

    扫描要起 PowerShell 枚举 PnP/注册表 + 2 秒射频（实测 7.2 秒）→ 必须带 TTL 缓存，
    否则打开一次"总能力"就要等 7 秒（而且页面轮询还会反复触发）。
    """
    if fresh:
        return _ble_block_scan()
    out = dict(_BLE_CACHE.get("v", _ble_block_scan))
    out.setdefault("缓存秒", _BLE_TTL)                 # 如实标注这是缓存读数
    return out


def _ble_block_scan() -> dict:
    try:
        from senses import ble as sb
        st = sb.status()
        ad = (st.get("适配器") or {})
        res = sb.scan(duration=2.0, rf=bool((st.get("射频扫描") or {}).get("ok")))
        return {"适配器数": ad.get("count"), "适配器": ad.get("list") or [],
                "设备数": res.get("count"), "设备": (res.get("devices") or [])[:10],
                "来源": res.get("sources") or [], "来源失败": res.get("errors") or [],
                "GATT服务行": res.get("gatt_service_rows"),
                "写操作需要": st.get("写操作需"), "授权是唯一闸门": st.get("授权是唯一闸门"),
                "原因": res.get("reason") or ""}
    except Exception as exc:                              # noqa: BLE001
        return {"适配器数": None, "设备数": None, "设备": [],
                "原因": f"{type(exc).__name__}"}


async def detail(db, led=None) -> dict:
    """内置明细：读帧插件 / 算力活（云主管道）/ 只读工具 / 蓝牙 / 定盘账。"""
    audit_rows = []
    try:
        from common.db import fetch_all
        audit_rows = await fetch_all(
            db, "SELECT domain, COUNT(*) AS n FROM read_tool_audit GROUP BY domain")
    except Exception as exc:                              # noqa: BLE001
        audit_rows = [{"error": f"{type(exc).__name__}"}]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "读帧插件": _frame_readers(),
            "算力活": {"总数": len(_cr.WORKLOADS), "行": [w.as_dict() for w in _cr.WORKLOADS]},
            "只读工具": _read_tools(audit_rows),
            "蓝牙": _ble_block(),
            "固化": _solid.status()}


# ═══════════ 固化 / 回滚（把图表读数定格成版本，可一键回到上一版）═══════════
CHART_NAME = "capability_chart"


async def solidify_chart(db, led=None, *, note: str = "") -> dict:
    snap = await chart(db, led)
    r = _solid.solidify(CHART_NAME, snap, note=note or "总能力图表定格")
    r["图表时间"] = snap["at"]
    return r


def chart_history() -> list:
    return _solid.history(CHART_NAME)


def chart_rollback(rev: str | None = None) -> dict:
    return _solid.rollback(CHART_NAME, rev)


def chart_latest() -> dict:
    return _solid.latest(CHART_NAME)


def solidify_status() -> dict:
    return _solid.status()


__all__ = ["chart", "usage", "connection_status", "detail", "solidify_chart",
           "chart_history", "chart_rollback", "chart_latest", "solidify_status",
           "CHART_NAME"]
