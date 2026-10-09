# core/sider_sockets.py —— 把她五个产品面接成插座，脑子换成**我们自己的模型**
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「可以你直接操作就可以了，就使用我们自己的模型」。
# 口径：**不碰 Sider 的鉴权、不过 Cloudflare、不要 key** ——
#   她的产品面（chat / code / hand / create / wisebase）由**我们的模型能源**驱动：
#     brain  = 万能插 model 插座（本地 ollama / 云插件，沙盒里跑，产物带 sha256）
#     hand   = 万能插 process 插座（真跑命令，带准入名单）
#     wisebase = 她自己的记忆（dh_memory）检索 + 模型综合（查不到就如实说，不编）
#   对外只露我们的口：/api/sider/* 与 /dh-input
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "sider_sockets.jsonl"
BRAIN_SYSTEM = ("你是 GBT小土豆V9 的数字人，客户看到的是「Sider 面」。"
                "开发者：自由的风。用简体中文，直接给结论，不客套，不反问用户。"
                "你知道框架：100 根触手、26 个面板页、29 项能力各有独立验收器、"
                "六条硬规定（模块闭环/视觉钉死/双向绑定/报警追根因/不许求用户/停机闸）。")


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def brain(prompt: str, *, backend: str = "auto", timeout: float = 180.0) -> dict:
    """**她的脑子**：默认 Agnes 云脑，失败如实回落本地 ollama（core.brain_v9.think）。"""
    from core import brain_v9 as B
    r = B.think(prompt, system=BRAIN_SYSTEM, prefer=("agnes" if backend in ("auto", "agnes", "cloud") else "local"))
    out = {"ok": bool(r.get("ok")), "后端": r.get("后端"), "秒": r.get("秒"),
           "沙盒": r.get("沙盒"), "产物": r.get("产物"), "谁答的": r.get("谁答的"),
           "字": (r.get("字") or "")[:800]}
    return out


def chat(msg: str) -> dict:
    r = brain("用户问：%s" % msg)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.chat", "问": msg[:120], **r}
    _log(rec)
    return rec


def code(task: str) -> dict:
    r = brain("写代码任务：%s\n只给可运行的最小实现，附一行用法。" % task)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.code", "任务": task[:120], **r}
    _log(rec)
    return rec


def create(brief: str) -> dict:
    r = brain("创作任务：%s\n给三段式计划：剧本要点 → 分镜要点 → 生产清单。" % brief)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.create", "题材": brief[:120], **r}
    _log(rec)
    return rec


def wisebase(query: str) -> dict:
    """先查**她自己的记忆**（有依据才敢说），再让模型综合；查不到就如实说没存到。"""
    from core import dh_memory as DM
    import re as _re
    q = query or ""
    toks = [t for t in _re.split(r"[^0-9A-Za-z一-鿿]+", q) if len(t) >= 2]
    # 中文没有分隔符 ⇒ 补 2~4 字 n-gram（"视觉钉死是什么" → 视觉/钉死/视觉钉/视觉钉死…）
    for t in list(toks):
        if _re.fullmatch(r"[一-鿿]+", t):
            for n in (4, 3, 2):
                toks += [t[i:i + n] for i in range(max(0, len(t) - n + 1))]
    toks = list(dict.fromkeys(toks))
    items, seen = [], set()
    for t in (toks or [query]):
        for h in (DM.search(t, limit=3).get("条目") or []):
            k = (h.get("标题"), h.get("来源"))
            if k not in seen:
                seen.add(k)
                items.append(h)
    hit = {"ok": bool(items), "命中": len(items), "条目": items[:4]}
    ctx = "；".join("%s：%s" % (h.get("标题"), str(h.get("正文"))[:120]) for h in (hit.get("条目") or []))
    if not hit.get("ok"):
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.wisebase", "问": query[:120],
               "命中": 0, "字": "记忆里没存到这一条（我不编）。要学的话把资料递给我。", "ok": True}
        _log(rec)
        return rec
    r = brain("只依据以下记忆回答（不要编）：%s\n\n问题：%s" % (ctx, query))
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.wisebase", "问": query[:120],
           "命中": hit["命中"], "依据": [h.get("标题") for h in (hit.get("条目") or [])], **r}
    _log(rec)
    return rec


ALLOW_CMDS = ("python", "git", "node", "cmd", "pwsh")   # 内建命令(dir/echo)由 cmd /c 代跑


def hand(task: str) -> dict:
    """**手**：真跑命令（准入名单），不是聊天。"""
    import shlex
    cmd = shlex.split(task)
    if cmd and cmd[0].lower() in ("dir", "echo", "type", "copy", "move", "del"):
        cmd = ["cmd", "/c"] + cmd            # Windows 内建：交给 cmd /c
    if not cmd or Path(cmd[0]).name.lower().replace(".exe", "") not in ALLOW_CMDS:
        rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.hand", "任务": task[:120],
               "ok": False, "拒动": True, "原因": "准入名单：%s" % (", ".join(ALLOW_CMDS))}
        _log(rec)
        return rec
    from core import pulse as P
    r = P.plug_and_run("t002", kind="process", action="run", args={"cmd": cmd}, timeout=180)
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "插座": "sider.hand", "任务": task[:120],
           "ok": bool(r.get("ok")), "码": r.get("码"),
           "出字": (r.get("stdout") or "")[-300:], "误": (r.get("stderr") or "")[-160:]}
    _log(rec)
    return rec


def status() -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-6:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"最近": rows, "插座": ["chat", "code", "hand", "create", "wisebase"],
            "能源": "我们自己的模型（万能插 model 插座：本地 ollama / 云插件）",
            "口径": "不碰鉴权、不过 CF、不要 key；对外只露 /api/sider/* 与 /dh-input"}


__all__ = ["brain", "chat", "code", "create", "wisebase", "hand", "status", "LEDGER"]
