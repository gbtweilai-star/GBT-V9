# tools/inventory_snapshot.py —— 能力/文件登记台账：指纹快照 + 变更 diff（追责底座）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人 2026-10-09：「大模型每次哪里修改、那里新增、哪里扫描过，所有的一切都要给我做登记」
#   —— 这个仓**没有 git 历史**，所以"东西被谁删了"根本查不出来（这是真问题，不是我以为的）。
# 本工具给两样东西：
#   ① **指纹快照**：把全仓文件（大小 + mtime + sha256）落 state/inventory/<时间>.json；
#   ② **变更 diff**：跟上一份快照比，逐条列出 **新增 / 修改 / 删除**（连 sha256 变没变都写清）。
# 用法：
#   python tools/inventory_snapshot.py                # 拍一份快照并跟上一份比
#   python tools/inventory_snapshot.py --label 用GLM前 # 拍带标签的快照（便于事后举证）
#   python tools/inventory_snapshot.py --diff-only     # 只看变化，不拍新快照
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "inventory"
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache",
             "win-unpacked", "release", "dist", ".venv", "venv"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".tmp", ".part", ".zip", ".7z", ".rar"}


def walk() -> dict:
    out = {}
    for p in ROOT.rglob("*"):
        if p.is_dir() or any(d in SKIP_DIRS for d in p.parts) or p.suffix.lower() in SKIP_EXT:
            continue
        try:
            st = p.stat()
            rel = str(p.relative_to(ROOT)).replace("\\", "/")
            out[rel] = {"size": st.st_size, "mtime": int(st.st_mtime)}
        except OSError:
            continue
    return out


def sha_of(rel: str, cache: dict) -> str:
    p = ROOT / rel
    try:
        if p.stat().st_size > 8 * 1024 * 1024:      # 大件只按大小+mtime 判，不算哈希（省时）
            return ""
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except OSError:
        return ""


def latest() -> Path | None:
    files = sorted(OUT.glob("inv-*.json"))
    return files[-1] if files else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    ap.add_argument("--diff-only", action="store_true")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    prev_p = latest()
    prev = json.loads(prev_p.read_text(encoding="utf-8")) if prev_p else {"files": {}}
    now = walk()
    for rel in now:                                   # 哈希只对变化的文件算（先粗筛）
        old = (prev.get("files") or {}).get(rel)
        now[rel]["sha"] = "" if (old and old.get("size") == now[rel]["size"]
                                and old.get("mtime") == now[rel]["mtime"]) else sha_of(rel, {})
        if not now[rel]["sha"] and old:
            now[rel]["sha"] = old.get("sha", "")
    added = sorted(set(now) - set(prev.get("files") or {}))
    removed = sorted(set(prev.get("files") or {}) - set(now))
    changed = sorted(r for r in set(now) & set(prev.get("files") or {})
                     if now[r].get("sha") and now[r].get("sha") != (prev["files"][r] or {}).get("sha"))
    print("== 文件/能力登记台账 ==")
    print("  上一份: %s" % (prev_p.name if prev_p else "（无，这是第一份）"))
    print("  这一份: %d 个文件" % len(now))
    print("  新增 %d · 修改 %d · 删除 %d" % (len(added), len(changed), len(removed)))
    for tag, rows in (("＋新增", added), ("✎修改", changed), ("－删除", removed)):
        for r in rows[:30]:
            print("   %s %s" % (tag, r))
        if len(rows) > 30:
            print("   %s …还有 %d 条" % (tag, len(rows) - 30))
    if not a.diff_only:
        ts = time.strftime("%Y%m%d-%H%M%S")
        f = OUT / ("inv-%s%s.json" % (ts, ("-" + a.label) if a.label else ""))
        f.write_text(json.dumps({"at": ts, "label": a.label, "root": str(ROOT),
                                 "files": now, "diff_vs": prev_p.name if prev_p else "",
                                 "added": added, "changed": changed, "removed": removed},
                                ensure_ascii=False), encoding="utf-8")
        print("  快照: %s" % f.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
