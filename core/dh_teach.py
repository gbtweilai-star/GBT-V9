# core/dh_teach.py —— 数字人带路：启动后一步一步教用户（用户只给架构，剩下她来）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「启动之后一步一步教用户怎么做，用户和她交互只需要把架构给她，
#   就没用户什么事了 —— AI 操控 AI 那个交互肯定百分百契合。」
# 口径：步骤**从她的记忆现算**（core/dh_memory），每步都给：说什么·跑什么命令·看什么读数·什么算成功·常见错。
from __future__ import annotations

import json
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "dh_teach.jsonl"


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def plan() -> list:
    """六步带路（每步都有真命令与判据；内容取自 her 记忆与真件）。"""
    from core import dh_memory as DM
    n_pages = DM.stats()["分类"].get("页面", 0)
    n_caps = DM.stats()["分类"].get("能力", 0)
    n_mod = DM.stats()["分类"].get("模块", 0)
    return [
        {"步": 1, "标题": "认识她（这套框架是什么）",
         "她说": "我是 GBT小土豆V9 的数字人。这套框架是「主脑 + 100 根触手」，每根触手有职业、有账号、有随身库；我记着 %d 条框架知识。" % DM.stats()["记忆条数"],
         "跑": "python -c \"import sys;sys.path.insert(0,'.');from core import dh_memory as D;print(D.stats())\"",
         "看": "记忆条数与分类（文档/能力/页面/模块/硬规定）",
         "成功": "记忆条数 > 0 且分类齐全", "常见错": "条数为 0 ⇒ 跑一次 ingest（她启动会自灌）"},
        {"步": 2, "标题": "起面板（看得见）",
         "她说": "把面板起起来，%d 个页面，一眼能看全。" % n_pages,
         "跑": "uvicorn panel.server:app --port 8765",
         "看": "浏览器打开 http://127.0.0.1:8800/fleet-live",
         "成功": "页面 200 且编队面板出现 100 个格子", "常见错": "404 ⇒ 该页路由没挂，跑它的验收器看自报"},
        {"步": 3, "标题": "看编队（谁在干活）",
         "她说": "绿色=有落账在干活；灰=没落账（不是坏了）；紫=待授权。",
         "跑": "python -c \"import sys;sys.path.insert(0,'.');from core import fleet_live as F;import json;print(json.dumps(F.status(),ensure_ascii=False))\"",
         "看": "统计键（工作/待命/休息/离线/待授权）",
         "成功": "能读出统计；点进任一根能看时间线", "常见错": "全离线 ⇒ 还没有落账，先跑一条真动作"},
        {"步": 4, "标题": "交活（把你的架构给她）",
         "她说": "你把架构/项目丢给我，我按模块拆开，逐模块跑通闭环才交。",
         "跑": "python -c \"import sys;sys.path.insert(0,'.');from core import modular_deploy as M;print(M.plan_project([{'模块':'你的模块','实现':['x.py'],'验收':['tools/verify_no_ask.py']}]))\"",
         "看": "放行=True（缺验收器会直接拒）",
         "成功": "每个模块都带独立验收器；%d 个模块全部闭环" % n_mod,
         "常见错": "缺验收器被拒 ⇒ 补一个 tools/verify_<模块>.py 再开工"},
        {"步": 5, "标题": "验收（%d 项能力逐条闭环）" % n_caps,
         "她说": "我不口头说做好了，拿验收器说话。",
         "跑": "python tools/daily_triage.py",
         "看": "交付闸可交付数 / 模块闭环率 / 静默吞异常 / 瞎子缝 / 停机闸",
         "成功": "daily_triage 全绿（退出码 0）", "常见错": "有红 ⇒ 它会给根因/处置/证据三件，照着修"},
        {"步": 6, "标题": "之后不用你管",
         "她说": "停机闸只认两种停：部署完成(带证据) 或 真需要你操作；其余我继续；主人随时可喊停（一句「停」即可）。",
         "跑": "python -c \"import sys;sys.path.insert(0,'.');from core import stop_policy as S;import json;print(json.dumps(S.may_stop('轮次结束'),ensure_ascii=False)[:200])\"",
         "看": "能停=False（整体没完善时）· 缺口清单",
         "成功": "缺口能被列出来并逐条消掉", "常见错": "别把『不影响』当理由 —— 停机闸不认"},
    ]


def status() -> dict:
    return {"步数": len(plan()), "步骤": plan(),
            "口径": "从她的记忆现算；每步都给命令与判据；用户只给架构，剩下她带"}


def next_step(*, mark: bool = False) -> dict:
    done = set()
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("动作") == "done":
                    done.add(rec.get("步"))
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    for s in plan():
        if s["步"] not in done:
            if mark:
                _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "done", "步": s["步"]})
            return {"下一步": s, "已完成": sorted(done)}
    return {"下一步": None, "已完成": sorted(done), "判": "六步都走完了（她进入值守）"}


def say(step: int) -> dict:
    s = next((x for x in plan() if x["步"] == step), None)
    if not s:
        return {"ok": False, "原因": "没有这一步"}
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "say", "步": step})
    return {"ok": True, "她说的话": s["她说"], "跑": s["跑"], "看": s["看"], "成功": s["成功"]}


__all__ = ["plan", "status", "next_step", "say", "LEDGER"]
