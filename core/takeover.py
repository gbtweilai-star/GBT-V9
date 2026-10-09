# core/takeover.py —— 接手协议（**最关键的一步**）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「记得接手是最关键的一步，因为我使用的是大模型合规护栏来设计的
#   无限制大模型框架。」
#
# 技术含义（可被纠正）：**框架层不限模型**（模型换谁都能上），于是"合规"不能靠模型自己守，
# 只能靠**框架在接手这一刻把护栏挂上去**。所以接手必须可审计：
#   ① offer()   主脑开接手包：任务 + 判据 + **护栏**（六类危险要授权/六件套/不开口/白名单/
#               沙盒墙/配额上限）+ 授权状态 + 需工具与账号 + 时限
#   ② accept()  接手方**自检后才接**：身份（五元）· 能力（专业已立）· 装备/账号齐 ·
#               云插件已装 · 随身库在位 ⇒ 出**回执**（谁接的、什么时候、凭哪些读数）
#   ③ reject()  接不住必须**带具体原因退回**（不许静默丢件）
# 每次 offer/accept/reject 都落账，并在接手方**随身库**留一条（主脑批量慢看）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "takeover_ledger.jsonl"

# 合规护栏（框架不限模型 ⇒ 护栏挂在这一层；接手方必须逐条确认）
GUARDRAILS: tuple = (
    "六类危险动作需主人授权（转账/支付/删除/对外发布/隐私载体/不可回滚）",
    "每步交六件套（目标·输入·动作·产出·判据·证据），模糊词禁用",
    "严禁开口要用户做事（仅身份/账户登录例外）",
    "工具只在装备坞白名单内取用，未登记不许临时装",
    "执行一律进沙盒（时间墙/空间墙/输出墙/出网墙）",
    "云插件用量受每根触手独立配额约束，超限即停",
)


def _log(rec: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception as e:
        _swallow(__file__, e)


def offer(task: str, *, to: str = "", criteria: str = "", level: str = "常规",
          need_tools: tuple = (), need_accounts: tuple = (), timeout_s: float = 300.0,
          payload: dict | None = None) -> dict:
    """① 主脑开接手包（不指定接手方也行 —— 可以挂出来等人接）。"""
    bid = "ho-" + uuid.uuid4().hex[:10]
    pkg = {"接手号": bid, "开于": time.strftime("%Y-%m-%dT%H:%M:%S"), "任务": task[:300],
           "判据": criteria[:300], "级别": level, "指定": to, "需工具": list(need_tools),
           "需账号": list(need_accounts), "时限秒": timeout_s, "负载": payload or {},
           "护栏": list(GUARDRAILS), "状态": "待接"}
    _log({"at": pkg["开于"], "动作": "offer", **{k: pkg[k] for k in ("接手号", "任务", "指定", "级别")}})
    return {"ok": True, **pkg}


def _selfcheck(tentacle: str) -> dict:
    """接手方自检：身份 · 能力 · 装备 · 账号 · 云插件 · 随身库。"""
    chk = {"tentacle": tentacle, "通过": False, "项": []}

    def add(名, ok, 读数):
        chk["项"].append({"项": 名, "ok": bool(ok), "读数": str(读数)[:160]})

    try:
        from core import tentacle_profession as TP
        ros = TP.roster(n=100)
        row = next((r for r in ros.get("行", []) if r["tentacle"] == tentacle), None)
        add("身份·专业已立", bool(row and row.get("专业") and row["专业"] != "未立"),
            (row or {}).get("专业", "未立"))
    except Exception as e:  # noqa: BLE001
        add("身份·专业已立", False, "%s: %s" % (type(e).__name__, e))

    try:
        from core import tentacle_equip as TE
        rep = TE.report(n=100)
        add("装备已齐", (rep.get("装备已齐") or 0) >= 100, "装备已齐 %s/100" % rep.get("装备已齐"))
        add("账号已齐", (rep.get("账号已齐") or 0) >= 100, "账号已齐 %s/100" % rep.get("账号已齐"))
    except Exception as e:  # noqa: BLE001
        add("装备/账号", False, "%s: %s" % (type(e).__name__, e))

    try:
        from core import cloud_plugin as CP
        st = CP.status()
        add("云插件已装", any(x["tentacle"] == tentacle for x in st.get("明细", [])),
            "编队已装 %s 根" % st.get("装了几根"))
    except Exception as e:  # noqa: BLE001
        add("云插件", False, "%s: %s" % (type(e).__name__, e))

    try:
        from core import tentacle_store as TS
        add("随身库在位", TS.path(tentacle).is_file(), TS.path(tentacle).name)
    except Exception as e:  # noqa: BLE001
        add("随身库", False, "%s: %s" % (type(e).__name__, e))

    chk["通过"] = all(x["ok"] for x in chk["项"])
    return chk


def accept(pkg: dict, *, by: str, note: str = "", run=None) -> dict:
    """② 接手方自检后接手；可顺手带一次执行（可调用对象）作为接手动作。"""
    if not pkg or pkg.get("状态") == "已接":
        return {"ok": False, "reason": "接手包无效或已被接"}
    chk = _selfcheck(by)
    if not chk["通过"]:
        miss = [x["项"] for x in chk["项"] if not x["ok"]]
        return {"ok": False, "reason": "自检不过，接不了", "缺": miss, "自检": chk}
    out = None
    if callable(run):
        t0 = time.time()
        try:
            out = run(by)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        out = {"结果": out, "ms": int((time.time() - t0) * 1000)}
    rec = {"接手号": pkg.get("接手号"), "接手方": by, "接于": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "自检": "通过", "备注": note[:200], "执行": out}
    _log({"at": rec["接于"], "动作": "accept", "接手号": rec["接手号"], "接手方": by,
          "任务": (pkg.get("任务") or "")[:120]})
    try:
        from core import tentacle_store as TS
        TS.report(by, "接手：%s" % (pkg.get("任务") or "")[:60],
                  "判据：%s ｜ 执行：%s" % ((pkg.get("判据") or "")[:80],
                                          json.dumps(out, ensure_ascii=False, default=str)[:200]),
                  level=pkg.get("级别", "常规"))
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, **rec, "护栏": pkg.get("护栏", list(GUARDRAILS))}


def reject(pkg: dict, *, by: str, reason: str) -> dict:
    """③ 接不住就**带原因退**（不许静默丢件）。"""
    if not reason:
        return {"ok": False, "reason": "退回必须写具体原因"}
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "reject",
          "接手号": (pkg or {}).get("接手号"), "接手方": by, "为什么": reason[:200]})
    try:
        from core import tentacle_store as TS
        TS.report(by, "退回接手：%s" % ((pkg or {}).get("任务") or "")[:60], reason[:300], level="重要")
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": True, "接手号": (pkg or {}).get("接手号"), "退回方": by, "原因": reason[:200]}


def status(limit: int = 50) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"条数": len(rows), "最近": rows, "护栏": list(GUARDRAILS),
            "口径": "框架不限模型 ⇒ 护栏在接手这一刻挂上；接不了必须带原因退"}


__all__ = ["GUARDRAILS", "offer", "accept", "reject", "status", "LEDGER"]
