# tools/secret_guard.py —— 密钥闸（推送前必过）：仓库里不许有真密钥
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「密钥记得别给我整上去哈。」
# 判据（只读已跟踪文件 + 待提交内容，绝不外传）：
#   ① 已知前缀：sk- / ak_ / cfoat_ / cfort_ / gho_ / ghp_ / AKIA / xoxb- …
#   ② 疑似凭据赋值：api_key|secret|password|token = <长串>
#   ③ 敏感文件被跟踪：state/keys.env · state/tentacle_keys.json · state/cf_token.txt · .env …
# 处置：命中即**拦停推送**（退出码 1）；敏感文件被跟踪就 git rm --cached 并补 .gitignore。
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI 风格 key"),
    (r"ak_[A-Za-z0-9]{16,}", "abliteration key"),
    (r"cfoat_[A-Za-z0-9_\-]{20,}", "Cloudflare OAuth"),
    (r"cfort_[A-Za-z0-9_\-]{20,}", "Cloudflare refresh"),
    (r"gho_[A-Za-z0-9]{20,}", "GitHub OAuth"),
    (r"ghp_[A-Za-z0-9]{20,}", "GitHub PAT"),
    (r"AKIA[0-9A-Z]{16}", "AWS key"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "Slack token"),
    (r"(?i)(api[_-]?key|secret|password|passwd|auth[_-]?token)\s*[:=]\s*[\"']?[A-Za-z0-9_\-]{18,}", "疑似凭据赋值"),
]
SENSITIVE = ("state/keys.env", "state/tentacle_keys.json", "state/cf_token.txt", "state/cf_oauth.json",
             "state/hacker_brain_v9.json", "state/cloud_plugins.json", ".env", ".env.local",
             "config.yaml", "state/llm_role_changes.jsonl")
SAFE_HINT = ("example", "sample", "template", ".md", "test_", "tests/")


def _mask(v: str) -> str:
    return v[:4] + "…" + str(len(v)) + "字"


def scan() -> dict:
    files = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(ROOT)).stdout.splitlines()
    hits, tracked_sensitive = [], []
    for f in files:
        if f in SENSITIVE:
            tracked_sensitive.append(f)
        if any(h in f.lower() for h in SAFE_HINT):
            continue
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 3_000_000:
            continue
        try:
            s = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(s.splitlines(), 1):
            for pat, why in PATTERNS:
                m = re.search(pat, line)
                if m:
                    hits.append({"文件": f, "行": i, "类": why, "值": _mask(m.group(0))})
                    break
    return {"已跟踪文件": len(files), "命中": hits, "命中数": len(hits),
            "敏感文件被跟踪": tracked_sensitive, "通过": not hits and not tracked_sensitive}


def harden() -> dict:
    """补 .gitignore + 把已跟踪的敏感件移出索引（不动本地文件）。"""
    gi = ROOT / ".gitignore"
    need = ["state/keys.env", "state/tentacle_keys.json", "state/cf_token.txt", "state/cf_oauth.json",
            ".env", "state/*.env", "state/*token*", "state/*secret*"]
    cur = gi.read_text(encoding="utf-8", errors="replace") if gi.is_file() else ""
    added = [n for n in need if n not in cur]
    if added:
        gi.write_text(cur.rstrip() + chr(10) + chr(10) + "# 密钥闸补（tools/secret_guard.py）" + chr(10) +
                      chr(10).join(added) + chr(10), encoding="utf-8")
    r = scan()
    removed = []
    for f in r["敏感文件被跟踪"]:
        p = subprocess.run(["git", "rm", "--cached", "-q", "--ignore-unmatch", f],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
        if p.returncode == 0:
            removed.append(f)
    return {"gitignore 新增": added, "移出索引": removed, "剩余命中": scan()["命中数"]}


def main() -> int:
    r = scan()
    print("== 密钥闸 ==")
    print("  已跟踪文件:", r["已跟踪文件"], "| 命中:", r["命中数"], "| 敏感件被跟踪:", r["敏感文件被跟踪"] or "无")
    for h in r["命中"][:15]:
        print("   %s:%s [%s] %s" % (h["文件"], h["行"], h["类"], h["值"]))
    if not r["通过"]:
        print("  处置:", harden())
        r2 = scan()
        print("  复核 → 命中:", r2["命中数"], "| 通过:", r2["通过"])
        if not r2["通过"]:
            print("  结论：❌ **拦停推送**（仓库里仍有真密钥/敏感件）")
            return 1
    print("  结论：✅ 通过（仓库里无真密钥、无敏感件被跟踪）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
