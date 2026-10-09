# core/agent_loop.py —— 让接入的大模型**真的调动她的能力**（模型出决策 → 真执行 → 回喂 → 再决策）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「看看我们接入的大模型能不能调动她的能力」。
# 口径：模型只出**决策 JSON**，执行一律走她的真插座（万能插/五面插座/框架能力）；
#       每步落账（谁决策·调了什么·真读数）；模型说"没这工具"就如实拒绝，不假装调过。
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "agent_loop.jsonl"

TOOLS = {
    "fleet_status": {"说": "看编队：100 根触手谁在工作/待命/休息", "参数": {}},
    "search_memory": {"说": "查她自己的框架记忆（先查后答，查不到就说不编）", "参数": {"q": "关键词"}},
    "read_file": {"说": "读仓内某个文件（限 40KB）", "参数": {"path": "相对路径"}},
    "run_cmd": {"说": "真跑一条命令（准入名单 python/git/node/cmd/pwsh）", "参数": {"cmd": "命令字符串"}},
    "scan_path": {"说": "穿透扫描一个目录（仓内或仓外都行）", "参数": {"path": "绝对或相对路径", "limit": "文件数上限"}},
    "gen_code": {"说": "让她的脑子写一段最小实现", "参数": {"task": "任务"}},
}


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def _exec(tool: str, args: dict) -> dict:
    """真执行：全部走她自己的插座/框架能力。"""
    from core import sider_sockets as SS
    try:
        if tool == "fleet_status":
            from core import fleet_live as FL
            s = FL.status()
            return {"ok": True, "统计": s["统计"], "在工作": s["工作"][:8], "待授权": s["待授权"][:5]}
        if tool == "search_memory":
            from core import dh_memory as DM
            r = DM.search(str(args.get("q") or ""), limit=3)
            return {"ok": bool(r.get("ok")), "命中": r.get("命中"),
                    "条目": [h.get("标题") for h in (r.get("条目") or [])]}
        if tool == "read_file":
            p = (ROOT / str(args.get("path"))).resolve()
            if not str(p).startswith(str(ROOT)):
                return {"ok": False, "拒动": True, "原因": "只读仓内文件"}
            t = p.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "字节": len(t), "节选": t[:1200]}
        if tool == "run_cmd":
            return SS.hand(str(args.get("cmd") or ""))
        if tool == "scan_path":
            from core import cross_scan_fleet as CS
            base = Path(str(args.get("path")))
            if not base.is_absolute():
                base = ROOT / base
            files = [f for f in base.rglob("*") if f.is_file() and f.suffix in (".py", ".js", ".ts", ".json", ".md")][: int(args.get("limit") or 120)]
            if not files:
                return {"ok": False, "原因": "没有可扫的文件"}
            sh = CS.shards(files, 100)
            return {"ok": True, "文件": len(files), "分片": len(sh)}
        if tool == "gen_code":
            r = SS.code(str(args.get("task") or ""))
            return {"ok": bool(r.get("ok")), "谁答的": r.get("谁答的"), "字": (r.get("字") or "")[:400]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "错": "%s: %s" % (type(e).__name__, str(e)[:120])}
    return {"ok": False, "原因": "没有这个工具", "工具": tool}


def _ask(prompt: str) -> dict:
    from core import brain_v9 as B
    sys = ("你是一个能调动工具的智能体。只输出**一行 JSON**，不要解释、不要代码块。"
           "可用工具：" + json.dumps({k: v["参数"] for k, v in TOOLS.items()}, ensure_ascii=False) +
           chr(10) + "决策格式：{\"tool\": \"工具名\", \"args\": {…}}；"
           "任务完成时输出：{\"tool\": \"final\", \"args\": {\"answer\": \"给用户的答复\"}}")
    return B.think(prompt, system=sys, prefer="agnes")


def _parse(txt: str) -> dict:
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return {}


def run(task: str, *, max_steps: int = 6) -> dict:
    """跑一个真任务：模型决策 → 真执行 → 回喂 → 再决策，直到 final。"""
    t0 = time.time()
    trace, obs = [], ""
    for step in range(1, max_steps + 1):
        prompt = ("任务：%s" % task) + (chr(10) + "上一步结果：" + obs if obs else "")
        if obs and step >= 3:
            prompt += chr(10) + "（已经跑了几步了：**如果信息够了就直接 final**，不许再重复同一个工具）"
        r = _ask(prompt)
        d = _parse(r.get("字") or "")
        tool = str(d.get("tool") or "")
        args = d.get("args") or {}
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "步": step, "谁答的": r.get("谁答的"),
               "后端": r.get("后端"), "决策工具": tool, "参数": args}
        if tool == "final":
            rec["答复"] = str(args.get("answer") or "")[:300]
            trace.append(rec)
            _log(rec)
            return {"ok": True, "步数": step, "答复": rec["答复"], "轨迹": trace,
                    "秒": round(time.time() - t0, 1)}
        if tool not in TOOLS:
            rec["拒动"] = True
            rec["读数"] = "模型给了不存在的工具（如实拒动，不假装调过）"
            trace.append(rec)
            _log(rec)
            obs = "工具不存在，请从可用工具里选。"
            continue
        out = _exec(tool, args)
        rec["读数"] = {k: v for k, v in out.items() if k != "节选"}
        if "节选" in out:
            rec["节选头"] = out["节选"][:200]
        trace.append(rec)
        _log(rec)
        obs = json.dumps(out, ensure_ascii=False)[:1200]
    return {"ok": False, "原因": "达到步数上限未收尾", "轨迹": trace, "秒": round(time.time() - t0, 1)}


def status() -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-8:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"工具": list(TOOLS), "最近": rows,
            "口径": "模型只出决策 JSON；执行全走她的真插座；不存在的工具如实拒动"}


__all__ = ["TOOLS", "run", "status", "_exec", "LEDGER"]
