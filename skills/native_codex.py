# skills/native_codex.py —— Codex 终端编程工具（V9 的一个工具，由主脑按需调用）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 定位：Codex（OpenAI 官方终端编程助手）只是 GBT小土豆V9 的【一个编程工具】——
#       终端原生 · 代码库理解 · bug修复/测试/重构；注册进工具清单后，
#       主脑/Octop Agent 可像调用其它工具一样调用它，它不是 V9 的架构或身份。
#
# 安全边界（本模块硬编码，不可由请求覆盖）：
#   - 只走非交互 exec/review/resume；参数用列表传递，绝不 shell 拼接
#   - 沙箱只允许 read-only / workspace-write；**不支持也不透传任何 bypass 选项**
#   - 每次调用落账本（谁、在哪、跑了什么、退出码、耗时）
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skills.native import Availability, SkillResult  # noqa: E402
from core.swallow import swallow as _swallow

CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
DEFAULT_TIMEOUT = int(os.environ.get("CODEX_TIMEOUT_S", "1800"))
ALLOWED_SANDBOX = ("read-only", "workspace-write")


class CodexTool:
    """把本机 Codex CLI 包成一个 V9 工具。"""

    name, version = "codex", "cli-1.0"
    description = ("Codex 终端编程工具：代码库理解 / bug 修复 / 测试 / 重构（外部 CLI，非交互执行）")

    def __init__(self, ledger=None, brain=None, default_cwd=None,
                 sandbox="workspace-write", timeout_s=None):
        self.led, self.brain = ledger, brain
        self.cwd = str(default_cwd or os.environ.get("CODEX_CWD", os.getcwd()))
        self.sandbox = sandbox if sandbox in ALLOWED_SANDBOX else "workspace-write"
        self.timeout = int(timeout_s or DEFAULT_TIMEOUT)

    # ── 自查：CLI 是否可用（真跑 --version）──
    def probe(self) -> Availability:
        exe = shutil.which(CODEX_BIN)
        if not exe:
            return Availability(ok=False, reason="未找到 codex CLI（装：npm i -g @openai/codex 或桌面版）")
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return Availability(ok=False, reason=f"codex --version 失败: {type(exc).__name__}: {exc}")
        if r.returncode != 0:
            return Availability(ok=False, reason=f"codex --version rc={r.returncode}")
        return Availability(ok=True, reason=f"{r.stdout.strip()} @ {exe} (沙箱默认={self.sandbox})")

    def spec(self) -> dict:
        """类型化声明（与 run() 签名严格对齐）；Codex 只是 V9 的编程工具，
        沙箱只允许白名单档位，bypass 类选项不支持。"""
        return {
            "inputs": {
                "task": {"type": "string", "required": True,
                         "help": "要 Codex 做什么（兼容 prompt 别名）"},
                "mode": {"type": "enum", "values": ["exec", "review"],
                         "default": "exec"},
                "cwd": {"type": "string", "help": "工作目录（缺省用实例默认）"},
                "sandbox": {"type": "enum",
                            "values": list(ALLOWED_SANDBOX),
                            "default": "workspace-write",
                            "help": "绕过审批/沙箱类选项一律不支持"},
                "timeout_s": {"type": "integer", "default": self.timeout},
            },
            "outputs": {"output": {"type": "object"},
                        "rc": {"type": "integer"}},
            "idempotent": False,
            "risk": "high",       # 会写工作区代码 → 工作流里走人工闸门
        }

    # ── 工具调用 ──
    def run(self, request: dict | None = None, ctx=None) -> SkillResult:
        req = dict(request or {})
        mode = str(req.get("mode", "exec")).lower()
        task = str(req.get("task") or req.get("prompt") or "").strip()
        cwd = str(req.get("cwd") or self.cwd)
        sandbox = str(req.get("sandbox") or self.sandbox)
        if sandbox not in ALLOWED_SANDBOX:
            return SkillResult(ok=False, error=f"沙箱只允许 {ALLOWED_SANDBOX}；收到 {sandbox!r}（bypass 类选项不支持）")
        if not task:
            return SkillResult(ok=False, error="缺少 task（要 Codex 做什么）")
        timeout = int(req.get("timeout_s") or self.timeout)
        workdir = Path(cwd)
        if not workdir.exists():
            return SkillResult(ok=False, error=f"工作目录不存在：{cwd}")

        cmd = [CODEX_BIN, "exec", "-C", str(workdir), "-s", sandbox,
               "--skip-git-repo-check", "--color", "never"]
        if mode == "review":
            cmd = [CODEX_BIN, "exec", "-C", str(workdir), "-s", sandbox,
                   "--skip-git-repo-check", "--color", "never", "review", task]
        elif mode == "resume":
            session = str(req.get("session") or "--last")
            cmd = [CODEX_BIN, "exec", "resume", session, "-s", sandbox,
                   "--skip-git-repo-check", "--color", "never", task]
        else:
            cmd += [task]

        started = time.time()
        try:
            proc = subprocess.run(cmd, cwd=str(workdir), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace",
                                  timeout=timeout)
            out, err, rc = proc.stdout or "", proc.stderr or "", proc.returncode
        except subprocess.TimeoutExpired:
            out, err, rc = "", f"超时（{timeout}s）", 124
        except Exception as exc:  # noqa: BLE001
            out, err, rc = "", f"{type(exc).__name__}: {exc}", 1
        seconds = round(time.time() - started, 1)

        # 账本：每次调用都记（工具调用也要可审计）
        if self.led is not None:
            try:
                self.led.log("codex", f"{mode}:{workdir.name}", 
                             "scanned" if rc == 0 else "vuln",
                             f"rc={rc} {seconds}s task={task[:120]}",
                             event_id=f"codex-{int(started)}")
            except Exception as e:
                _swallow(__file__, e)


        tail = "\n".join((out or err).strip().splitlines()[-40:])
        return SkillResult(ok=rc == 0,
                           output={"mode": mode, "cwd": str(workdir), "sandbox": sandbox,
                                   "rc": rc, "seconds": seconds, "tail": tail},
                           artifacts=[], warnings=[],
                           error=None if rc == 0 else (err.strip()[-300:] or f"rc={rc}"),
                           usage={"ms": int(seconds * 1000)})


def selftest() -> int:
    """python -m skills.native_codex —— 真跑自查（--version；可选一次最小 exec）"""
    tool = CodexTool()
    a = tool.probe()
    print("probe:", a.ok, "|", a.reason)
    if not a.ok:
        return 1
    if os.environ.get("CODEX_SELFTEST_EXEC") == "1":
        r = tool.run({"task": "只回复两个字：就绪", "mode": "exec", "sandbox": "read-only",
                      "timeout_s": 120, "cwd": str(Path(__file__).resolve().parents[1])})
        print("exec:", r.ok, "|", json.dumps(r.output or {}, ensure_ascii=False)[:300])
        return 0 if r.ok else 2
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
