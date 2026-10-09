# core/dh_boot.py —— 数字人启动口：一开机就灌记忆、开口带路（用户只给架构）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「数字人要把整个框架的使用知识都存入她的记忆，启动之后一步一步教用户；
#   用户和她交互只需要把架构给她，就没用户什么事了。」
# 本件把记忆(core.dh_memory) + 带路(core.dh_teach) 接到**启动那一刻**，并给面板提供口：
#   boot()      → 启动时调用：灌记忆 + 说第一句 + 给当前这一步
#   greeting()  → 她的开场白（含记忆条数与下一步）
#   panel()     → 给 /api/dh/boot 的整包读数
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "dh_boot.jsonl"


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def boot(*, ingest: bool = True) -> dict:
    """启动自检 + 灌记忆 + 定位当前该教哪一步（幂等，可每次开机跑）。"""
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "dh_boot"}
    try:
        from core import dh_memory as DM
        out["记忆"] = DM.ingest() if ingest else DM.stats()
    except Exception as e:  # noqa: BLE001
        out["记忆"] = {"错误": "%s: %s" % (type(e).__name__, str(e)[:80])}
    try:
        from core import dh_teach as DT
        nxt = DT.next_step()
        out["当前这一步"] = nxt.get("下一步")
        out["已完成步"] = nxt.get("已完成") or []
    except Exception as e:  # noqa: BLE001
        out["当前这一步"] = {"错误": type(e).__name__}
    out["开场白"] = greeting(step=(out.get("当前这一步") or {}).get("步"))
    _log({k: out[k] for k in ("at", "抓")} | {"步": (out.get("当前这一步") or {}).get("步")})
    return out


def greeting(step: int | None = None) -> str:
    """她的开场白：先报自己记了多少，再给下一步要做什么。"""
    try:
        from core import dh_memory as DM
        from core import dh_teach as DT
        n = DM.stats()["记忆条数"]
        s = next((x for x in DT.plan() if x["步"] == step), None) or DT.next_step().get("下一步")
        if not s:
            return "我是 GBT小土豆V9 的数字人。框架知识我记着 %d 条；六步带路都走完了，我进入值守。" % n
        return ("我是 GBT小土豆V9 的数字人。框架知识我记着 %d 条。"
                "下一步（第 %d 步·%s）：%s。\n要你做的只有一件：把架构/项目给我；"
                "剩下的我按模块拆开、逐条跑闭环、有红黄我追根因。想停就说「停」。"
                % (n, s["步"], s["标题"], s["她说"]))
    except Exception as e:  # noqa: BLE001
        return "（数字人启动口异常：%s）" % type(e).__name__


def panel() -> dict:
    """给面板的口：一次性把记忆/带路/硬规定读数交出去。"""
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        from core import dh_memory as DM
        out["记忆"] = DM.stats()
    except Exception as e:  # noqa: BLE001
        out["记忆"] = {"错误": type(e).__name__}
    try:
        from core import dh_teach as DT
        out["带路"] = {"步数": DT.status()["步数"], "下一步": DT.next_step().get("下一步")}
    except Exception as e:  # noqa: BLE001
        out["带路"] = {"错误": type(e).__name__}
    out["开场白"] = greeting()
    return out


__all__ = ["boot", "greeting", "panel", "LEDGER"]
