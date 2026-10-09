# core/no_begging.py —— 「禁止向用户求助」硬闸
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09，最后一次强调）：「框架里面凡是必须用户求助帮忙的，全部给我禁止掉。
#   真正懂 AI 逻辑的大众用户有多少？你开口要求用户的时候你就输了。」
#
# 口径（本闸判什么）：
#   · **求助句式**：请/需要/麻烦 + 你/您/主人 + (提供|设置|配置|输入|执行|点击|手动|帮忙|告诉|自行|登入)；
#     "等你/请主人/you need to/please provide/ask the user"…
#   · **阻塞等输入**：input(...) 让人在终端敲字；
#   · **唯一例外**：主人**自己的身份证/账户登入**（这是"授权"，不是"帮忙"），且必须写成
#     「已登记待授权」而不是「请你登入」——文案也不许求人。
# 处置：能自动降级/自愈的就改掉；改不动的**登记**（红=阻塞、黄=文案），逐条清零，不许无声放过。
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "no_begging_audit.jsonl"
DIRS = ("core", "tools", "panel", "body", "senses", "audit", "media", "skills", "actuator", "alert", "parity", "scan")
SUFFIX = (".py", ".html", ".js", ".md")

# 红：阻塞式等人输入（真会把用户拽进来）
RED = ((r"(?<![\"\'])binput\s*\(", "终端等输入 input()"),)
# 黄：求助句式（文案层）
YELLOW = (
    (r"请(你|您|主人)[^\n]{0,12}(提供|设置|配置|输入|执行|点击|手动|帮忙|告诉|登入|登录|填入|粘贴)", "请你做…"),
    (r"需要(你|您|主人)", "需要你做…"),
    (r"等(你|您|主人)", "等你…"),
    (r"麻烦(你|您|主人)", "麻烦你…"),
    (r"(请|需)手动", "请手动…"),
    (r"请自行", "请自行…"),
    (r"you (need|have) to", "you need to…"),
    (r"please (provide|set|configure|enter|run|click)", "please …"),
    (r"ask the user", "ask the user"),
)
# 允许（唯一例外 + 非求助语境）
ALLOW = (
    r"身份|身份证|账号登入|账户登入|登录 ID|login",     # 主人自己的身份登入（授权类）
    r"no_begging|verify_no_begging|ux_doctrine|禁令|不许|禁止|口径|判据",
    r"不需要|无需|不用你|不打断你|不喊你|_COAX|bad = \[|反例|样本|ALLOWED|PATTERNS|规则",  # 否定句/反例/词表
    '全禁|正则|检测用|r"',   # 规则定义/正则字面量本身（不是求助出口）
    "^" + chr(92) + "s*#", chr(34) * 3,   # 注释 / 文档串
)


def _allowed(line: str) -> bool:
    return any(re.search(p, line) for p in ALLOW)


def scan(*, limit: int = 400) -> dict:
    reds, yellows = [], []
    for d in DIRS:
        base = ROOT / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if p.suffix not in SUFFIX or "__pycache__" in p.parts:
                continue
            rel = str(p.relative_to(ROOT)).replace(chr(92), "/")
            if "no_begging" in rel:
                continue
            try:
                lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for i, line in enumerate(lines, 1):
                if _allowed(line):
                    continue
                for pat, why in RED:
                    if re.search(pat, line):
                        reds.append({"位置": "%s:%d" % (rel, i), "类": "红·" + why, "行": line.strip()[:110]})
                for pat, why in YELLOW:
                    if re.search(pat, line):
                        yellows.append({"位置": "%s:%d" % (rel, i), "类": "黄·" + why, "行": line.strip()[:110]})
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "红": reds[:limit], "黄": yellows[:limit],
           "红数": len(reds), "黄数": len(yellows)}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": rec["at"], "红数": rec["红数"], "黄数": rec["黄数"]}, ensure_ascii=False) + chr(10))
    return rec


def status(limit: int = 3) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"最近": rows, "口径": "红=阻塞等人；黄=求助文案；唯一例外=主人自己的身份证登入（且不许写成求你）"}


__all__ = ["RED", "YELLOW", "ALLOW", "scan", "status", "LEDGER"]
