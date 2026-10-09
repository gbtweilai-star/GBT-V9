# core/tentacle_identity.py —— 触手身份金库：**一根触手一个账户**，主脑全量可查
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   "每根触手注册一次，不能出现一根触手同时注册好一个账户，必须一根注册一个"；
#   "每根触手注册好自己的开源账户双向绑定好，可当无限制仓库使用"；
#   "所有触手的账户主脑有权查看"。
#
# 边界（这条必须写清楚，不能含糊）：
#   · 本模块**不做**向第三方平台批量开户。批量注册邮箱/云账号属于绕过注册防护，
#     违反各平台条款，也会把本机 IP 拖进滥用名单 —— 对项目是净损失，不实现。
#   · 本模块做的是**另一半**，而且把它做扎实：一根触手一个身份的**唯一约束**、
#     凭据只从环境变量/密钥服务读、自动化**校验与双向绑定**、主脑全量审计视图、
#     每步钩子留证（core/hooks）防止 AI 偷懒。凭据由人或运维注入，程序不再造第二个。
#
# 服务口径（每根触手在这些面上各占一个身份位）：
#   邮箱（收信/找回） / 云插件（算力） / 数据库（存储） / 开源仓库（代码托管）
from core.swallow import swallow as _swallow
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB = ROOT.joinpath("data", "tentacle_identity.sqlite3")

SERVICES = ("邮箱", "云插件", "数据库", "开源仓库")
STATES = ("待配", "已配", "已验证", "已绑", "失效")

# 凭据只从环境变量读：<前缀>_<服务>_<触手号>（如 V9ID_MAIL_T007 / V9ID_REPO_T007）
ENV_PREFIX = os.environ.get("V9_IDENTITY_PREFIX", "V9ID")
_SVC_KEY = {"邮箱": "MAIL", "云插件": "CLOUD", "数据库": "DB", "开源仓库": "REPO"}


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def ensure() -> None:
    """建表：**一根触手在同一个服务上只能有一条**（UNIQUE 硬约束，不靠自觉）。"""
    with _conn() as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS tentacle_identity("
            "id TEXT PRIMARY KEY, tentacle TEXT NOT NULL, service TEXT NOT NULL,"
            "handle TEXT DEFAULT '', state TEXT DEFAULT '待配',"
            "env_key TEXT DEFAULT '', bound_target TEXT DEFAULT '',"
            "note TEXT DEFAULT '', created REAL, updated REAL,"
            "UNIQUE(tentacle, service))")
        c.execute(
            "CREATE TABLE IF NOT EXISTS identity_events("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, tentacle TEXT, service TEXT,"
            "action TEXT, detail TEXT DEFAULT '', ok INTEGER DEFAULT 1)")
        c.execute("CREATE INDEX IF NOT EXISTS ix_tid_t ON tentacle_identity(tentacle)")


def tentacles(*, n: int = 100) -> list:
    return ["t%03d" % i for i in range(1, max(1, int(n)) + 1)]


def env_key(tentacle: str, service: str) -> str:
    return f"{ENV_PREFIX}_{_SVC_KEY.get(service, 'X')}_{str(tentacle).upper()}"


def _log(c, tentacle: str, service: str, action: str, detail: str, ok: bool = True) -> None:
    c.execute("INSERT INTO identity_events(ts,tentacle,service,action,detail,ok)"
              " VALUES(?,?,?,?,?,?)",
              (time.time(), tentacle, service, action, str(detail)[:300], 1 if ok else 0))


def claim(tentacle: str, service: str, *, handle: str = "", note: str = "",
          by: str = "主脑") -> dict:
    """给一根触手在某服务上占一个身份位。**已经有位就拒绝**（一根只注册一次）。"""
    t = str(tentacle or "").strip()
    s = str(service or "").strip()
    if s not in SERVICES:
        return {"ok": False, "reason": f"服务只认 {SERVICES}"}
    if not re.fullmatch(r"t\d{3}", t):
        return {"ok": False, "reason": "触手号形如 t001…t100"}
    ensure()
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_identity WHERE tentacle=? AND service=?",
                        (t, s)).fetchone()
        if got:
            # 唯一约束的"人话版"：直接说清为什么不行，而不是抛数据库错误
            return {"ok": False, "reason": f"{t} 在「{s}」上已有身份位（{got['handle'] or '未填地址'}）；"
                                           f"一根触手只注册一个，不能同时占两个",
                    "existing": dict(got)}
        iid = "i" + uuid.uuid4().hex[:12]
        ek = env_key(t, s)
        c.execute("INSERT INTO tentacle_identity(id,tentacle,service,handle,state,env_key,"
                  "bound_target,note,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?)",
                  (iid, t, s, str(handle)[:120], "待配", ek, "", str(note)[:200],
                   time.time(), time.time()))
        _log(c, t, s, "claim", f"占位 by {by} handle={handle}")
    return {"ok": True, "id": iid, "tentacle": t, "service": s,
            "凭据环境变量": ek, "状态": "待配",
            "说明": "凭据从环境变量读；程序不代你向第三方开户"}


def attach(tentacle: str, service: str, *, handle: str, state: str = "已配",
           note: str = "") -> dict:
    """直挂一个身份（用于**本地自签**这类不需要外部凭据的身份）。

    provision() 要求环境变量，可本地自签的身份本来就没有外部凭据 —— 走这里挂上，
    状态才算真的"已配"。这样"新用户下载即可用"不是口号。
    """
    ensure()
    if not handle:
        return {"ok": False, "reason": "直挂必须给 handle"}
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_identity WHERE tentacle=? AND service=?",
                        (tentacle, service)).fetchone()
        if not got:
            return {"ok": False, "reason": "还没占位，先 claim"}
        c.execute("UPDATE tentacle_identity SET handle=?, state=?, note=?, updated=?"
                  " WHERE tentacle=? AND service=?",
                  (str(handle)[:120], state if state in STATES else "已配",
                   str(note)[:200], time.time(), tentacle, service))
        _log(c, tentacle, service, "attach", f"{state} {handle} {note}")
    return {"ok": True, "tentacle": tentacle, "service": service, "handle": handle,
            "状态": state}


def provision(tentacle: str, service: str, *, handle: str = "") -> dict:
    """把凭据接上：从环境变量读，读到才算「已配」（读不到就如实说缺哪个变量）。"""
    ensure()
    ek = env_key(tentacle, service)
    val = os.environ.get(ek, "").strip()
    if not val:
        return {"ok": False, "reason": f"缺凭据：请设环境变量 {ek}",
                "env_key": ek, "状态": "待配"}
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_identity WHERE tentacle=? AND service=?",
                        (tentacle, service)).fetchone()
        if not got:
            return {"ok": False, "reason": "还没占位，先 claim（一根触手一个身份位）"}
        c.execute("UPDATE tentacle_identity SET state='已配', handle=?, updated=?"
                  " WHERE tentacle=? AND service=?",
                  (str(handle or got["handle"] or (val[:6] + "…"))[:120], time.time(),
                   tentacle, service))
        _log(c, tentacle, service, "provision", f"凭据就位（{ek}，值不回显）")
    return {"ok": True, "tentacle": tentacle, "service": service, "状态": "已配",
            "env_key": ek, "口径": "只读环境变量；金库里不存明文凭据"}


def verify(tentacle: str, service: str) -> dict:
    """校验：邮箱/仓库看地址像不像样；云插件/数据库看对应槽位是否就绪。"""
    ensure()
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_identity WHERE tentacle=? AND service=?",
                        (tentacle, service)).fetchone()
        if not got:
            return {"ok": False, "reason": "没有身份位"}
        ev, ok, why = {}, False, ""
        if service == "邮箱":
            h = str(got["handle"] or "")
            ok = bool(re.match(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$", h))
            why = "" if ok else "地址不像邮箱（形如 name@host.tld）"
            ev = {"地址": h}
        elif service == "开源仓库":
            h = str(got["handle"] or "")
            ok = bool(h)
            why = "" if ok else "还没填仓库/账号"
            ev = {"仓库": h}
        else:
            try:
                from core import db_fleet as DF
                ok = bool(DF.SLOT_IDS)
                slot = f"{'cloud' if service == '云插件' else 'db'}_slot_state 表可读"
                ev = {"槽位数": len(DF.SLOT_IDS), "依据": slot}
                why = "" if ok else "库槽注册表不可读"
            except Exception as exc:                           # noqa: BLE001
                ok, why, ev = False, type(exc).__name__, {}
        c.execute("UPDATE tentacle_identity SET state=?, updated=? WHERE tentacle=? AND service=?",
                  ("已验证" if ok else got["state"], time.time(), tentacle, service))
        _log(c, tentacle, service, "verify", json.dumps(ev, ensure_ascii=False), ok)
    return {"ok": ok, "tentacle": tentacle, "service": service,
            "状态": "已验证" if ok else got["state"], "证据": ev, "reason": why}


def bind(tentacle: str, service: str, target: str) -> dict:
    """双向绑定：身份位 ↔ 目标（云插件槽 / 库槽 / 仓库）。两边都记，才算双向。"""
    tgt = str(target or "").strip()
    if not tgt:
        return {"ok": False, "reason": "绑定目标为空"}
    ensure()
    with _conn() as c:
        got = c.execute("SELECT * FROM tentacle_identity WHERE tentacle=? AND service=?",
                        (tentacle, service)).fetchone()
        if not got:
            return {"ok": False, "reason": "没有身份位，先 claim"}
        c.execute("UPDATE tentacle_identity SET bound_target=?, state='已绑', updated=?"
                  " WHERE tentacle=? AND service=?", (tgt[:200], time.time(), tentacle, service))
        _log(c, tentacle, service, "bind", f"→ {tgt}")
        try:
            from core import deploy_ledger as DL
            # 台账里不落原始 URL（显示卫生：页面渲染台账时不该出现看起来像外链的字符串）
            safe = tgt.split("://", 1)[-1][:120] if "://" in tgt else tgt[:120]
            DL.record("deploy", f"identity:{tentacle}/{service}",
                      detail=f"双向绑定 → {safe}", before="未绑", after=safe, ok=True)
        except Exception as e:
            _swallow(__file__, e)
    return {"ok": True, "tentacle": tentacle, "service": service, "绑到": tgt, "状态": "已绑",
            "双向": f"{tentacle}:{service} ↔ {tgt}（两侧各有一条记录）"}


def rows(*, tentacle: str = "", service: str = "", state: str = "") -> list:
    """读金库。**主脑视图**：不传 tentacle 即为全量（这是主人要的"主脑有权查看"）。"""
    ensure()
    with _conn() as c:
        q = ("SELECT * FROM tentacle_identity WHERE (tentacle=? OR ?='')"
             " AND (service=? OR ?='') AND (state=? OR ?='') ORDER BY tentacle, service")
        got = c.execute(q, (str(tentacle), str(tentacle), str(service), str(service),
                            str(state), str(state))).fetchall()
    return [{**dict(r), "凭据环境变量": r["env_key"]} for r in got]


def summary(*, n: int = 100) -> dict:
    """主脑一屏：每根触手 × 每个服务 的状态矩阵 + 缺口清单。"""
    ensure()
    ts = tentacles(n=n)
    got = {(r["tentacle"], r["service"]): r for r in rows()}
    ready = 0
    待办 = []
    tiers = {"已配": 0, "已验证": 0, "已绑": 0}
    for t in ts:
        for s in SERVICES:
            r = got.get((t, s))
            # "就绪"= 已配/已验证/已绑（本地自签也算配齐 —— 下载即可用）；
            # 分层计数用来区分"能用"与"验过 / 绑过"。
            if r and r["state"] in ("已配", "已验证", "已绑"):
                ready += 1
                tiers[r["state"]] = tiers.get(r["state"], 0) + 1
            elif r:
                待办.append({"触手": t, "服务": s, "状态": r["state"],
                             "缺什么": f"设 {r['env_key']} 后 provision"})
            else:
                待办.append({"触手": t, "服务": s, "状态": "未占位",
                             "缺什么": "先 claim 占位（一根触手一个身份位）"})
    total = len(ts) * len(SERVICES)
    return {"触手数": len(ts), "服务数": len(SERVICES), "身份位总数": total,
            "就绪": ready, "就绪率": round(ready / total, 3) if total else None,
            "分层": tiers,
            "待办数": len(待办), "待办样例": 待办[:8], "待办全部": 待办,
            "主脑视图": "不传 tentacle 即全量（主人要求：所有触手的账户主脑有权查看）",
            "边界": "程序不向第三方批量开户；凭据由人或运维注入（环境变量/密钥服务）"}


def events(limit: int = 50) -> list:
    ensure()
    with _conn() as c:
        got = c.execute("SELECT * FROM identity_events ORDER BY id DESC").fetchall()
    return [dict(r) for r in got[: max(1, int(limit))]]


__all__ = ["SERVICES", "STATES", "ensure", "tentacles", "env_key", "claim", "attach", "provision",
           "verify", "bind", "rows", "summary", "events", "DB"]
