# core/detail_habit.py —— 「细节化」习惯（机器可查，不是口号）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「给她设计一个习惯，做事细节化的习惯，每一步都要细节化。」
#
# 落地成三条硬规矩 —— 每一步都要过这三道：
#   ① **六件套**：目标 · 输入 · 动作 · 产出 · 判据 · 证据；缺一件 ⇒ 这一步不算完成，
#      只算「草稿」并如实标缺什么（不许用"应该可以"糊过去）。
#   ② **模糊词禁用**：应该/大概/差不多/基本上/后续优化/先这样/不影响… —— 命中即驳回。
#      形容词必须落成**数值**（更快 → 快多少 ms；更清晰 → 亮度均值/锐度读数）。
#   ③ **回读闭环**：每步要带「命令 + 退出码 + 产物 + 读数」。没回读 = 没做完。
# 另加两条粒度规矩：
#   ④ **一步一产出**：一步自己产出的东西 ≤ 3 件，超了就该拆（可判定：产物数）。
#   ⑤ **可重跑**：每步的命令必须能原样重跑（落台账，谁都能复现）。
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "detail_ledger.jsonl"

# ① 六件套（缺一不可）
STEP_FIELDS: tuple = ("目标", "输入", "动作", "产出", "判据", "证据")

# ② 模糊词表（命中即驳回；这些是"不细节"的语言指纹）
VAGUE: tuple = ("应该可以", "应该行", "大概", "差不多", "基本上", "可能是", "估计",
                "后续优化", "先这样", "暂时这样", "不影响", "问题不大", "看着还行",
                "差不多了", "优化一下", "再看看", "感觉可以", "理论上")

# ③ 回读三件（命令 / 退出码 / 读数）—— 允许"命令"是技能调用形式
READBACK: tuple = ("命令", "退出码", "读数")

MAX_ARTIFACTS = 3      # ④ 一步产出上限（超了拆步骤）


def habit() -> dict:
    """习惯全文（面板可读、人可读）—— 这就是她的"做事方式"。"""
    return {
        "名字": "细节化",
        "一句话": "每一步都交六件套，模糊词不许出口，做完必须回读；产不出读数的结论不算结论。",
        "六件套": list(STEP_FIELDS),
        "模糊词": list(VAGUE),
        "回读三件": list(READBACK),
        "粒度规矩": {"一步产出上限": MAX_ARTIFACTS, "可重跑": "每步命令原样可复现，落台账"},
        "落地位置": {"判定": "core.detail_habit.check_step / check_text",
                     "台账": "state/detail_ledger.jsonl",
                     "面板": "/api/detail/habit · /api/detail/check"},
        "谁在用": ["film_studio 每个技能节点", "publish_platform 审计/验收/发布",
                   "series 每集登记", "所有我给出的结论"],
    }


def check_text(text: str) -> dict:
    """② 模糊词闸：一段话里出现禁用词就驳回，并指出来。"""
    t = str(text or "")
    hit = sorted({w for w in VAGUE if w in t})
    nums = len(re.findall(r"\d+(?:\.\d+)?", t))
    return {"通过": not hit, "命中": hit,
            "数值个数": nums,
            "要求": "形容词要落成数值（例：更快 → 1.23s→0.41s；更清晰 → 亮度 38.7→113.2）",
            "口径": "没有数值的结论 = 草稿"}


def check_step(step: dict, *, require_readback: bool = True) -> dict:
    """① 六件套 + ③ 回读 的逐项判定（缺什么说什么）。"""
    s = dict(step or {})
    missing = [f for f in STEP_FIELDS if not str(s.get(f) or "").strip()]
    rb_missing = [f for f in READBACK if not str(s.get(f) or "").strip()] if require_readback else []
    text = " ".join(str(s.get(f) or "") for f in STEP_FIELDS)
    vague = check_text(text)["命中"]
    arts = s.get("产出")
    n_art = len(arts) if isinstance(arts, (list, tuple)) else (1 if str(arts or "").strip() else 0)
    too_many = n_art > MAX_ARTIFACTS
    ok = not missing and not rb_missing and not vague and not too_many
    return {"步骤": s.get("步骤") or s.get("目标") or "(未命名)", "通过": ok,
            "缺六件套": missing, "缺回读": rb_missing, "模糊词": vague,
            "产出件数": n_art, "粒度超限": too_many,
            "怎么办": ("补齐缺失项；把模糊词换成数值；把超出的产出拆成多步；补命令+退出码+读数"
                       if not ok else "已达标")}


def check_steps(steps: list) -> dict:
    rows = [check_step(s) for s in (steps or [])]
    bad = [r for r in rows if not r["通过"]]
    return {"步数": len(rows), "达标": len(rows) - len(bad), "未达标": len(bad),
            "达标率": round((len(rows) - len(bad)) / len(rows), 3) if rows else 0.0,
            "明细": rows}


def record(step: dict) -> dict:
    """落台账：每一步的六件套 + 回读 + 判定结果（可回放、可审计）。"""
    chk = check_step(step)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "步骤": chk["步骤"], "判据": check_step.__name__,
           "通过": chk["通过"], "缺": chk["缺六件套"] + chk["缺回读"], "模糊词": chk["模糊词"],
           **{k: step.get(k) for k in STEP_FIELDS}, "命令": step.get("命令"),
           "退出码": step.get("退出码"), "读数": step.get("读数")}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return {"ok": True, "通过": chk["通过"], "台账": str(LEDGER.relative_to(ROOT))}


def audit(limit: int = 200) -> dict:
    """细节化达标率（面板一行看穿"最近是不是在细节化"）。"""
    if not LEDGER.is_file():
        return {"条数": 0, "达标率": None, "说明": "还没有台账（她还没在本仓按六件套干过活）"}
    rows = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()][-limit:]
    ok = sum(1 for r in rows if r.get("通过"))
    vague = [{"步骤": r.get("步骤"), "模糊词": r.get("模糊词")} for r in rows if r.get("模糊词")]
    miss = [{"步骤": r.get("步骤"), "缺": r.get("缺")} for r in rows if r.get("缺")]
    return {"条数": len(rows), "达标": ok, "未达标": len(rows) - ok,
            "达标率": round(ok / len(rows), 3) if rows else None,
            "模糊词命中": vague[:5], "缺件样本": miss[:5]}


def template(goal: str = "", inputs: str = "", action: str = "", artifact: str = "",
             criteria: str = "", evidence: str = "") -> dict:
    """六件套模板（她每开一步先填这个）。"""
    return {"步骤": goal or "(填：这一步叫什么)", "目标": goal, "输入": inputs, "动作": action,
            "产出": artifact, "判据": criteria, "证据": evidence,
            "命令": "", "退出码": "", "读数": "",
            "自检": "跑 check_step 看缺什么（缺一件就不算完成）"}


__all__ = ["STEP_FIELDS", "VAGUE", "READBACK", "MAX_ARTIFACTS", "habit", "check_text",
           "check_step", "check_steps", "record", "audit", "template", "LEDGER"]
