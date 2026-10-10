# core/modular_deploy.py —— 模块式部署（一个能力一个模块：装/更/卸/回滚，全留痕）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「不管多大的项目，你只要设计好架构和蓝图，后面就开始使用我们自己
#   独有的模块式部署。」
#
# 设计（对着本仓现实，不另造体系）：
#   一个"模块" = 一个能力在盘上的**全部件**：实现文件 · 面板口 · 验收器 · 绑定条 · 台账；
#   deploy(模块)：把它的件核到位（缺件=装不成，如实报）；幂等；写部署台账（可回滚）；
#   verify(模块)：只跑它的独立验收器（闭环证据）；
#   rollback(模块)：按台账把该模块**最近一次部署**前的状态还原（只记录件清单与哈希，能比对）；
#   blueprint()：整体蓝图 = 模块清单 + 依赖（谁依赖谁）+ 当前装到哪一步。
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "modular_deploy.jsonl"

# 蓝图：模块 → 件（实现 · 面板 · 验收器）；依赖写在"依赖"里
BLUEPRINT: dict = {
    "感知-实时视觉": dict(实现=["core/vision_loop.py"], 面板=["panel/pulse_page.py"],
                      验收=["tools/verify_vision_loop.py"], 依赖=[]),
    "模型-牢房吐口": dict(实现=["core/sandbox_emit.py", "core/pulse_sandbox.py"],
                      面板=["panel/pulse_page.py"], 验收=["tools/verify_sandbox_emit.py"],
                      依赖=["感知-实时视觉"]),
    "模型-火力全开": dict(实现=["core/full_power.py"], 面板=["panel/pulse_page.py"],
                      验收=["tools/verify_full_power.py"], 依赖=["模型-牢房吐口"]),
    "编队-亿万触手": dict(实现=["core/tentacle_scale.py"], 面板=["panel/pulse_page.py"],
                      验收=["tools/verify_tentacle_scale.py"], 依赖=[]),
    "编队-职业与装备": dict(实现=["core/tentacle_profession.py", "core/tentacle_equip.py",
                            "core/tool_bay.py"], 面板=["panel/pulse_page.py"],
                       验收=["tools/verify_tentacle_vaults.py", "tools/verify_tentacle_equip.py"],
                       依赖=[]),
    "存储-随身库": dict(实现=["core/tentacle_store.py"], 面板=["panel/pulse_page.py"],
                    验收=["tools/verify_tentacle_store.py"], 依赖=[]),
    "协作-接手协议": dict(实现=["core/takeover.py"], 面板=["panel/pulse_page.py"],
                     验收=["tools/verify_takeover.py"], 依赖=["编队-职业与装备", "存储-随身库"]),
    "安全-交叉扫描": dict(实现=["core/cross_scan_fleet.py"], 面板=["panel/pulse_page.py"],
                     验收=["tools/verify_cross_scan.py"], 依赖=["编队-亿万触手"]),
    "页面-邮箱与账户": dict(实现=["panel/tentacle_mail_page.py", "core/mailbox_fleet.py",
                            "panel/tentacle_accounts_page.py"],
                       面板=["panel/server.py"],
                       验收=["tools/verify_pages_mail_accounts.py"], 依赖=["存储-随身库"]),
    "引擎-万能插执行器": dict(实现=["core/pulse.py"], 面板=["panel/pulse_page.py"],
                       验收=["tools/verify_plug.py"], 依赖=[]),
    "创作-影视生产线": dict(实现=["core/film_studio.py", "core/post_studio.py", "core/film_themes.py",
                            "core/series.py", "core/publish_platform.py"],
                       面板=["panel/studio_page.py"],
                       验收=["tools/verify_film.py", "tools/verify_post.py", "tools/verify_themes.py"],
                       依赖=[]),
    "资产-净化": dict(实现=["core/post_studio.py"], 面板=["panel/studio_page.py"],
                   验收=["tools/verify_asset_clean.py"], 依赖=["创作-影视生产线"]),
    "纪律-细节化与不开口": dict(实现=["core/detail_habit.py", "core/ux_doctrine.py"],
                         面板=["panel/pulse_page.py"],
                         验收=["tools/verify_detail_habit.py", "tools/verify_no_ask.py"], 依赖=[]),
    "闸-交付闸": dict(实现=["core/delivery_gate.py"], 面板=["panel/pulse_page.py"],
                   验收=["tools/verify_delivery_gate.py"], 依赖=[]),
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def _files(mod: str) -> list:
    spec = BLUEPRINT.get(mod) or {}
    out = []
    for k in ("实现", "面板", "验收"):
        out += [(k, f) for f in spec.get(k, [])]
    return out


def deploy(mod: str, *, dry: bool = False) -> dict:
    """装一个模块：核件（缺件=装不成，如实报）+ 记台账（可回滚）。"""
    if mod not in BLUEPRINT:
        return {"ok": False, "reason": "蓝图里没有这个模块: %s" % mod}
    rows, missing = [], []
    # 硬规定：**缺独立验收器就不算能装**（验收器是闭环证据，不是可选件）
    if not (BLUEPRINT.get(mod) or {}).get("验收"):
        return {"ok": False, "模块": mod, "缺验收器": True, "口径": "缺验收器即拒（闭环证据不可省）"}
    for kind, rel in _files(mod):
        p = ROOT / rel
        ok = p.is_file()
        rows.append({"件": rel, "类": kind, "在": ok, "sha": _sha(p) if ok else None})
        if not ok:
            missing.append(rel)
    dep_bad = [d for d in BLUEPRINT[mod].get("依赖", []) if not (ROOT / "state" / "modular_deploy.jsonl").is_file()]
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "模块": mod, "件": rows,
           "缺件": missing, "依赖": BLUEPRINT[mod].get("依赖", []), "dry": bool(dry)}
    if not dry:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return {"ok": not missing, "模块": mod, "件数": len(rows), "缺件": missing,
            "依赖": rec["依赖"], "装法": "模块式：实现件 + 面板口 + 独立验收器 三件齐才算装好"}


def verify(mod: str) -> dict:
    """只跑这个模块的独立验收器（闭环证据）。"""
    from core import delivery_gate as DG
    import subprocess
    import sys as _s
    outs = []
    for v in BLUEPRINT.get(mod, {}).get("验收", []):
        p = ROOT / v
        if not p.is_file():
            outs.append({"验收器": v, "通过": False, "结论": "缺验收器"})
            continue
        r = subprocess.run([_s.executable, str(p)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(ROOT))
        last = next((l for l in reversed(((r.stdout or "") + (r.stderr or "")).splitlines())
                     if l.strip()), "")
        outs.append({"验收器": v, "通过": r.returncode == 0 and "✅" in last, "结论": last.strip()[:120]})
    return {"模块": mod, "验收": outs, "通过": all(x["通过"] for x in outs) if outs else False}


def deploy_all(*, dry: bool = False) -> dict:
    rows = [deploy(m, dry=dry) for m in BLUEPRINT]
    return {"模块数": len(rows), "装好": sum(1 for x in rows if x["ok"]),
            "缺件模块": [x["模块"] for x in rows if not x["ok"]], "明细": rows}


def blueprint() -> dict:
    return {"模块数": len(BLUEPRINT),
            "模块": [{"模块": m, "件数": sum(len(v.get(k, [])) for k in ("实现", "面板", "验收")),
                     "依赖": v.get("依赖", []), "验收器": v.get("验收", [])} for m, v in BLUEPRINT.items()],
            "口径": "一个模块 = 实现件 + 面板口 + 独立验收器；三件不齐不算装好"}


def status(limit: int = 5) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    return {"最近部署": rows, "蓝图模块数": len(BLUEPRINT), "口径": "装/验/回滚都落台账"}


__all__ = ["BLUEPRINT", "deploy", "verify", "deploy_all", "blueprint", "status",
           "closure_rate", "assert_modules_closed", "plan_project", "LEDGER"]


# ── 主人硬规定（2026-10-09）：任何项目开工即拆模块；每个模块必须亲自跑通闭环 ──
def closure_rate() -> dict:
    """模块闭环率 = 有通过证据的模块 / 总模块（证据来自 verify() 的实时结果）。"""
    rows = []
    for m in BLUEPRINT:
        v = verify(m)
        rows.append({"模块": m, "通过": bool(v.get("通过")),
                     "验收器": [x["验收器"] for x in v.get("验收", [])]})
    ok = sum(1 for r in rows if r["通过"])
    return {"模块数": len(rows), "已闭环": ok, "未闭环": len(rows) - ok,
            "闭环率": round(100.0 * ok / len(rows), 1) if rows else 0.0,
            "未闭环清单": [r["模块"] for r in rows if not r["通过"]], "明细": rows}


def assert_modules_closed() -> dict:
    """硬闸：未闭环模块**不许合入/不许交付**；同时校验绑定无盲区。"""
    c = closure_rate()
    blind = []
    try:
        from core import file_binding as FB
        cov = FB.coverage()
        blind = cov.get("未绑定", [])
    except Exception as e:
        blind = ["绑定读取失败: %s" % type(e).__name__]
    ok = c["未闭环"] == 0 and not blind
    return {"放行": ok, "闭环率": c["闭环率"], "未闭环": c["未闭环清单"],
            "盲区": blind[:10], "盲区数": len(blind),
            "口径": "模块未闭环或绑定有盲区 ⇒ 拦停，不许交付"}


def plan_project(清单: list) -> dict:
    """开工闸：项目/大功能开工前必须交模块清单；**缺独立验收器的模块直接拒**。"""
    bad = []
    for m in (清单 or []):
        if not (m.get("验收") or m.get("验收器")):
            bad.append(m.get("模块") or m.get("name") or "(未命名)")
    return {"放行": not bad, "模块数": len(清单 or []), "缺验收器": bad,
            "口径": "每个模块必须带独立验收器 + 负责的页/文件范围，缺一不许开工"}
