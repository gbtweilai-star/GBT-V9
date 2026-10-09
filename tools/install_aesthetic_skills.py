# tools/install_aesthetic_skills.py —— 把「设计全维度（审美）技能族」装进本仓的项目技能根
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径来源（严格照 V8 的 tools/codex-scripts/install-design-mastery.py，不自己发明）：
#   · 本机 DSH 的 dsh-skill-filesystem 只扫这几个根：<项目>/.dsh/skills · <项目>/.agents/skills ·
#     preset/skills · $DSH_HOME/skills · ~/.agents/skills；~/.openclaw-autoclaw/skills 不在其中。
#   · 所以项目级落点 = <本仓>/.dsh/skills/<技能名>。
# 成员表不写死：从 gbt-design-mastery/SKILL.md 里引用的技能名现算，再与本机真存在的技能目录取交集。
#   → 上游改了 SKILL.md，这里跟着变；写死一次就过期一次。
# 半成品不许装：源目录必须有 SKILL.md 且 >= 800 字节。装了要留回执（逐文件 sha256 + 字节 + 时间）。
#
# 用法：
#   python tools/install_aesthetic_skills.py --check     # 只核对（默认）
#   python tools/install_aesthetic_skills.py --install   # 装机 + 留回执
# 退出码：0 = 齐全/装好；1 = 有缺件或哈希不符。
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent                      # 本仓 = GBT小土豆V9
V8 = Path(r"C:\Users\ADMIN\Desktop\GBT小土豆V8")                # 工具母仓（只读）
MASTERY = V8 / "skills" / "gbt-design-mastery" / "SKILL.md"
TARGET = ROOT / ".dsh" / "skills"
RECEIPT = ROOT / "state" / "aesthetic-skills-install-receipt.json"
MIN_SKILL_BYTES = 800
BT = chr(96)                                                      # 反引号（避免模板/转义踩坑）
TOKEN_RE = BT + "([a-z0-9][a-z0-9._-]{2,})" + BT

SOURCES = (
    ("v8-repo", V8 / "skills"),
    ("v8-dsh", V8 / ".dsh" / "skills"),
    ("home-agents", Path.home() / ".agents" / "skills"),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def members() -> list:
    """成员表现算：mastery 文档引用的技能名 与 本机真存在的技能目录 取交集。"""
    if not MASTERY.is_file():
        return []
    text = MASTERY.read_text(encoding="utf-8", errors="replace")
    names = {m.group(1) for m in re.finditer(TOKEN_RE, text)}
    out = []
    for name in sorted(names):
        for tag, root in SOURCES:
            d = root / name
            md = d / "SKILL.md"
            if d.is_dir() and md.is_file() and md.stat().st_size >= MIN_SKILL_BYTES:
                out.append({"name": name, "来源": tag, "源目录": str(d)})
                break
    return out


def _digest(dirpath: Path) -> dict:
    out = {}
    for p in sorted(dirpath.rglob("*")):
        if p.is_file() and "__pycache__" not in p.parts:
            out[p.relative_to(dirpath).as_posix()] = {
                "字节": p.stat().st_size, "sha256": _sha256(p)}
    return out


def check() -> dict:
    rows = []
    for m in members():
        dst = TARGET / m["name"]
        src = Path(m["源目录"])
        if not dst.is_dir():
            rows.append({"技能": m["name"], "状态": "缺", "来源": m["来源"]})
            continue
        a, b = _digest(src), _digest(dst)
        rows.append({"技能": m["name"], "状态": "一致" if a == b else "不一致",
                     "来源": m["来源"], "文件数": len(b)})
    ok = bool(rows) and all(r["状态"] == "一致" for r in rows)
    return {"ok": ok, "目标根": str(TARGET), "总数": len(rows), "明细": rows}


def install() -> dict:
    TARGET.mkdir(parents=True, exist_ok=True)
    done, skipped = [], []
    for m in members():
        src, dst = Path(m["源目录"]), TARGET / m["name"]
        if dst.is_dir() and _digest(src) == _digest(dst):
            skipped.append(m["name"])
            continue
        if dst.is_dir():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        done.append(m["name"])
    files = {m["name"]: _digest(TARGET / m["name"]) for m in members()}
    receipt = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "规范来源": str(MASTERY),
        "目标根": str(TARGET),
        "成员数": len(members()),
        "本次新装": done, "已是最新": skipped,
        "逐技能": {k: {"文件数": len(v),
                       "总字节": sum(x["字节"] for x in v.values()),
                       "sha256": hashlib.sha256(
                           json.dumps(v, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]}
                   for k, v in files.items()},
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True, "装了": done, "跳过": skipped,
            "成员数": receipt["成员数"], "回执": str(RECEIPT)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true", help="真装（默认只核对）")
    a = ap.parse_args()
    if a.install:
        r = install()
        print("装机：新装 %d 条 / 跳过 %d 条 / 成员共 %d 条" % (
            len(r["装了"]), len(r["跳过"]), r["成员数"]))
        print("新装：" + ("、".join(r["装了"]) or "（无）"))
        print("回执：" + r["回执"])
    c = check()
    print("核对：目标根 %s" % c["目标根"])
    print("成员 %d 条 —— %s" % (c["总数"], "全部一致" if c["ok"] else "有缺/不一致"))
    bad = [x for x in c["明细"] if x["状态"] != "一致"]
    for x in bad[:20]:
        print("  -", x["技能"], x["状态"], x.get("来源", ""))
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
