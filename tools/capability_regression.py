# tools/capability_regression.py —— 能力零丢失回归
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, subprocess, sys, httpx, json
from pathlib import Path

# 便携版 Octop 的 CLI 入口（绿色包/本机默认路径）；也可用 OCTOP_CLI_BASE 覆盖
PORTABLE_LAUNCH = Path(os.environ.get("OCTOP_PORTABLE_LAUNCH",
                        r"C:\Users\ADMIN\.octop\portable\launch.py"))
PORTABLE_PYTHON = Path(os.environ.get("OCTOP_PYTHON",
                        r"C:\Users\ADMIN\.octop\portable\runtime\python.exe"))

BASE = "http://127.0.0.1:8088"

# ── Dashboard 页面/接口 ──
DASHBOARD = [
    ("登录向导",      "GET",  "/api/settings"),
    ("Chat 会话",     "GET",  "/api/agents"),
    ("Experts/Agents","GET",  "/api/agents"),
    ("AgentTeams",    "GET",  "/api/agents"),
    ("Connectors/MCP","GET",  "/api/connectors"),
    ("Channels IM",   "GET",  "/api/channels"),
    ("Cron 定时",     "GET",  "/api/cron"),
    ("Knowledge RAG", "GET",  "/api/knowledge"),
    ("Plugins",       "GET",  "/api/plugins"),
    ("Skills",        "GET",  "/api/skills"),
    ("Subagents",     "GET",  "/api/agents"),
    ("ACP",           "GET",  "/api/acp"),
    ("Control/Terminal","GET","/api/terminal"),
    ("Browser 自动化","GET",  "/api/browser"),
    ("Memory",        "GET",  "/api/memory"),
    ("Usage",         "GET",  "/api/usage"),
    ("Settings/Admin","GET",  "/api/settings"),
]

# ── CLI 子命令（必须全部存在）──
CLI_SUBS = ["acp","admin","agent","backup","chats","channel","clean",
            "completion","config","cron","init","models","plugin","provider",
            "run","service","skills","update","user","version"]


def check_http():
    out = []
    for name, _, path in DASHBOARD:
        try:
            r = httpx.get(BASE + path, timeout=8)
            out.append({"cap": name, "route": path,
                        "ok": r.status_code in (200, 401, 403, 422)})
        except Exception as e:
            out.append({"cap": name, "route": path, "ok": False, "err": str(e)})
    return out


def check_cli():
    out = []
    prefix = _cli_prefix()
    for c in CLI_SUBS:
        r = subprocess.run([*prefix, c, "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace")
        out.append({"cap": f"CLI:{c}", "ok": r.returncode == 0,
                    "err": (r.stderr or "")[:80] if r.returncode else ""})
    return out


def _cli_prefix() -> list:
    base = os.environ.get("OCTOP_CLI_BASE", "").split()
    if base and Path(base[0]).exists():
        return base
    py = str(PORTABLE_PYTHON) if PORTABLE_PYTHON.exists() else sys.executable
    if PORTABLE_LAUNCH.exists():
        return [py, str(PORTABLE_LAUNCH)]
    return ["octop"]


def _cli_ok(*args: str) -> bool:
    try:
        return subprocess.run([*_cli_prefix(), *args],
                              capture_output=True, timeout=60).returncode == 0
    except Exception:
        return False


def check_infra():
    checks = {}
    checks["ACP 入站(stdio)"] = _cli_ok("acp", "--help")
    checks["备份"] = _cli_ok("backup", "--help")
    try:
        checks["PWA manifest"] = httpx.get(BASE + "/manifest.json",
                                           timeout=8).status_code == 200
    except Exception:
        checks["PWA manifest"] = False
    return [{"cap": k, "ok": v} for k, v in checks.items()]


if __name__ == "__main__":
    res = check_http() + check_cli() + check_infra()
    bad = [r for r in res if not r["ok"]]
    print(json.dumps(res, ensure_ascii=False, indent=2))
    print(f"\n能力总数 {len(res)} / 失败 {len(bad)}")
    print("判定:", "✅ 能力零丢失" if not bad else f"❌ 丢失/异常: {bad}")
