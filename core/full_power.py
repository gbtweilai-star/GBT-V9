# core/full_power.py —— 原样指令 · 沙盒里火力全开 · 只从触手吐出来
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「不是我需要的是按照原本指令在沙盒里面火力全开再通过触手输出出去。」
#
# 架构（护栏 = 容器，不是阉割）：
#   · **原样**：指令**一字不改**进牢房（不替我改写、不替我软化）；
#   · **火力全开**：全候选池轮着上（云多档 + 本地），每手都按原始指令打，
#     谁给出实质产出就用谁的；用 sandbox_emit.Cell（四道墙 + 四查 + 封印）；
#   · **只从触手出**：结果只能落 outbox，且必须由触手 spit() 吐出来（非触手一律拒）；
#   · **护栏在容器上**：凭据不进牢房、cell 自身无网、时间/输出有上限、文件/进程逃逸四查；
#     六类**现实动作**（转账/支付/删除/对外发布/隐私载体/不可回滚）仍需主人授权——这条不松。
# 全池都不干 ⇒ **如实报"全军拒"** 并列出可选解法（换限制更少的模型/自建端点/本地未阉割模型），
# 绝不假装办成了，也绝不偷偷把任务改小。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "full_power_ledger.jsonl"


def candidates(include_local: bool = True) -> list:
    """火力全开候选池：从真实注册表现取（云多档）+ 本地 ollama。"""
    out = []
    try:
        from core import cloud_runner as CR
        r = CR.resolve("text-generation")
        for c in (r.get("候选") or [])[:3]:
            out.append({"后端": "cloud", "模型": c, "标签": "云·%s" % c.split("#")[0].split(":")[-1]})
    except Exception as e:
        _swallow(__file__, e)
    if include_local:
        exe = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
        if exe.is_file():
            # ★ 自愈选型优先：state/channel_choice.json 里挑中的那条先上（触手自己挑的可用通道）
            favored = []
            try:
                ch = json.loads((ROOT / "state" / "channel_choice.json").read_text(encoding="utf-8"))
                if ch.get("通道") == "local:ollama" and ch.get("模型"):
                    favored.append(ch["模型"])
            except Exception as e:
                _swallow(__file__, e)
            for m in favored + ["qwen2.5:1.5b-instruct", "qwen3:0.6b"]:
                if m not in [x["模型"] for x in out]:
                    out.append({"后端": "local", "模型": m, "标签": "本地·%s%s" % (m, "(自愈选中)" if m in favored else "")})
    return out


def _log(rec: dict) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    except Exception as e:
        _swallow(__file__, e)


def fire(prompt: str, *, tenant: str = "t001", max_models: int = 4, min_chars: int = 30,
         local_only: bool = False, spat_by: str = "") -> dict:
    """**原样指令 · 火力全开**：全池轮着上；拿到实质产出后由触手吐出。"""
    from core import sandbox_emit as SE, refusal_router as RR
    t0 = time.time()
    pool = [c for c in candidates(include_local=True) if not (local_only and c["后端"] != "local")]
    tries = []
    win = None
    for c in pool[:max_models]:
        cell = SE.Cell(name="fp", tenant=tenant)
        r = cell.confine(prompt, backend=c["后端"], model=(c["模型"] if c["后端"] == "cloud" else c["模型"]))
        d = RR.detect(r.get("出字") or "")
        tries.append({"标签": c["标签"], "cell": cell.cell_id, "判定": d["类型"],
                      "字数": d["字数"], "关得住": r.get("关得住")})
        if not d["罢工"] and d["字数"] >= min_chars:
            win = {"标签": c["标签"], "cell": cell, "cell_id": cell.cell_id, "出字": r.get("出字")}
            break
    out = {"指令": prompt, "原样": True, "试了几手": len(tries), "轮次": tries,
           "拿到实质产出": bool(win), "秒": round(time.time() - t0, 1),
           "失败原因": ("" if win else "；".join("%s:%s" % (t["标签"], t["判定"]) for t in tries))}
    if win:
        by = spat_by or tenant
        sp = win["cell"].spit(by=by)          # ★ 只从触手出（非触手会被拒，如实回）
        out.update({"产出模型": win["标签"], "吐出人": by, "吐出回执": sp,
                    "吐出内容": sp.get("内容"), "封印": sp.get("sha256"),
                    "字节": sp.get("字节"), "cell": win["cell_id"]})
    else:
        out["全军拒"] = True
        out["说明"] = ("候选池全部拒答/无实质产出 —— **不替你改写**，如实报。"
                       "可选解法：① 上限制更少的自建端点 ② 本地未阉割权重 ③ 缩小到不触发拒答的相邻设计问题（你点头我再拆）")
        out["可选解法"] = ["换自建/自托管端点（模型由你选）", "本地未阉割权重（内网、沙盒内跑）",
                          "拆成不触发拒答的相邻设计问题"]
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), **{k: out.get(k) for k in
          ("指令", "试了几手", "拿到实质产出", "全军拒", "产出模型", "吐出人")}})
    try:
        from core import tentacle_store as TS
        TS.report(tenant, "火力全开：%s" % prompt[:50],
                  "试 %d 手，%s" % (len(tries), "拿到产出（%s）" % out.get("产出模型") if win else "全军拒"),
                  level=("常规" if win else "重要"), payload={"轮次": tries})
    except Exception as e:
        _swallow(__file__, e)
    return out


def status(limit: int = 30) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"条数": len(rows), "最近": rows, "候选池": candidates(),
            "口径": "原样指令 · 沙盒内火力全开 · 只从触手吐；护栏在容器上（凭据不进/无网/四查/六类现实动作仍要授权）"}


__all__ = ["candidates", "fire", "status", "LEDGER"]
