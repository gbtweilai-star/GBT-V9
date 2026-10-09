# tools/install_dh_rig.py —— 把「数字人骨架绑定工具链」从母仓装进本仓（带出处与哈希）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要专门一个装机器（照 V8 tools/codex-scripts/install-design-mastery.py 的口径）：
#   1. 工具链原件在母仓 GBT小土豆V8/tools/codex-scripts/，本仓 V9 没有 —— 直接跨仓调用等于
#      把 V9 的命门挂在 V8 上；装进来才是本仓自己的。
#   2. 「装了」要留机器可复核证据：逐文件 sha256 + 字节数 + 落盘时间 + 原件路径 → 写回执。
#   3. 装完必须**真跑一次**（rig-gate-check 拿本仓真网格跑），跑不通不算装好。
#
# 用法：
#   python tools/install_dh_rig.py --check      # 只核对（默认）
#   python tools/install_dh_rig.py --install    # 装机 + 回执 + 真跑门限检查
# 退出码：0 = 齐 + 门限脚本真跑通；1 = 有缺件或跑不通。
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
V8 = Path(r"C:\Users\ADMIN\Desktop\GBT小土豆V8")
SRC = V8 / "tools" / "codex-scripts"
DST = ROOT / "tools" / "codex-scripts"
RECEIPT = ROOT / "state" / "dh-rig-install-receipt.json"
VENDOR_MD = DST / "VENDOR.md"

# 最小可用集合：量骨架 → 算权重 → 迁移 → 姿势 → 门限 → 动画 → 渲染读数
FILES = (
    "rig-gate-check.py", "autorig-skeleton.py", "autorig-skin.py",
    "autorig-transfer.py", "autorig-pose-hi.py", "avatar-orient.py",
    "anim-make.py", "anim-idle.py", "anim-talk.py",
    "shot-glb.py", "measure-clarity.py", "audit-dh-status.py",
)
SKILL = "digihuman-pipeline"
SKILL_SRC = Path.home() / ".agents" / "skills" / SKILL
SKILL_DST = ROOT / ".dsh" / "skills" / SKILL
PROBE_GLB = ROOT / "state" / "tripo" / "out2" / "tripo-out" / "state-tripo-ref-full-5215f8bc" / "model.glb"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check() -> dict:
    rows = []
    for name in FILES:
        s, d = SRC / name, DST / name
        if not s.is_file():
            rows.append({"文件": name, "状态": "母仓缺件"})
        elif not d.is_file():
            rows.append({"文件": name, "状态": "本仓缺"})
        else:
            rows.append({"文件": name, "状态": "一致" if _sha(s) == _sha(d) else "不一致",
                         "字节": d.stat().st_size})
    skill_ok = SKILL_DST.is_dir() and (SKILL_DST / "SKILL.md").is_file()
    ok = all(r["状态"] == "一致" for r in rows) and skill_ok
    return {"ok": ok, "目标": str(DST), "文件数": len(rows), "明细": rows,
            "技能": SKILL, "技能已装": skill_ok, "跑门限的网格": str(PROBE_GLB)}


def install() -> dict:
    DST.mkdir(parents=True, exist_ok=True)
    rows = []
    for name in FILES:
        s, d = SRC / name, DST / name
        if not s.is_file():
            continue
        shutil.copy2(s, d)
        rows.append({"文件": name, "字节": d.stat().st_size, "sha256": _sha(d),
                     "原件": str(s)})
    if SKILL_SRC.is_dir():
        if SKILL_DST.is_dir():
            shutil.rmtree(SKILL_DST)
        shutil.copytree(SKILL_SRC, SKILL_DST,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    lines = ["<!-- 本文件由 tools/install_dh_rig.py 生成，别手改 -->",
             "# 数字人骨架绑定工具链 · 出处", "",
             "原件母仓：" + str(SRC),
             "装机时间：" + datetime.now().astimezone().isoformat(timespec="seconds"),
             "", "| 文件 | 字节 | sha256 |", "|---|---|---|"]
    lines += ["| %s | %d | %s |" % (r["文件"], r["字节"], r["sha256"][:32]) for r in rows]
    VENDOR_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    receipt = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "母仓": str(SRC), "本仓落点": str(DST), "技能落点": str(SKILL_DST),
        "文件": rows,
        "技能": {"名": SKILL, "源": str(SKILL_SRC), "件数": (
            sum(1 for _ in SKILL_DST.rglob("*")) if SKILL_DST.is_dir() else 0)},
    }
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(receipt, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"装了": [r["文件"] for r in rows], "回执": str(RECEIPT), "出处表": str(VENDOR_MD)}


def run_gate() -> int:
    """真跑门限检查（工具链的第一个可判定出口）。"""
    gate = DST / "rig-gate-check.py"
    if not gate.is_file() or not PROBE_GLB.is_file():
        print("[真跑] 缺件，跳过（门脚本或探针网格不在）")
        return 1
    import os as _os
    env = dict(_os.environ)
    env["PYTHONIOENCODING"] = "utf-8"          # 子进程里的 ✅/✗ 在 GBK 控制台下会炸
    r = subprocess.run([sys.executable, str(gate), str(PROBE_GLB)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=600, shell=False, env=env)
    print("[真跑] rig-gate-check.py 退出码 =", r.returncode)
    print((r.stdout or "").strip())
    if r.returncode != 0:
        print((r.stderr or "")[-800:])
    return int(r.returncode)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="store_true")
    a = ap.parse_args()
    if a.install:
        r = install()
        print("装机：%d 个脚本 → %s" % (len(r["装了"]), DST))
        print("出处表：" + r["出处表"])
        print("回执：" + r["回执"])
    c = check()
    print("核对：%d 个脚本 —— %s；技能 %s %s" % (
        c["文件数"], "全部一致" if c["ok"] else "有缺/不一致", c["技能"],
        "已装" if c["技能已装"] else "未装"))
    for x in c["明细"]:
        if x["状态"] != "一致":
            print("  -", x["文件"], x["状态"])
    rc = run_gate()
    return 0 if (c["ok"] and rc == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
