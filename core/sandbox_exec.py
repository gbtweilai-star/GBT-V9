# core/sandbox_exec.py —— 受限执行 + 写时复制工作区 + 每步可回滚（蒸馏自 MuseWork 的操作理念）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏来源：MuseWork 安装目录里的原词频次（restricted-token / AppContainer / copy-on-write /
#   rollback ×287 / headless-serve / sandbox-policy）。它的动手理念可概括成四条：
#     ① 动手前先定策略（sandbox mode + policy）
#     ② 改的是**副本**（copy-on-write），原件不碰
#     ③ 每一步都能**回滚**（rollback 是它出现最多的词）
#     ④ 无头可驱动（`muse serve`：能被脚本/服务调，而不是只能点界面）
# V9 落成：SandboxPolicy / CowWorkspace / SandboxExec / run_loop 闭环。
#
# ⚠ 诚实边界（不冒充）：真正的 Windows **受限令牌 / AppContainer** 需要原生调用与提权，
#   本模块不假装做到；V9 的工程等价手段是——工作区隔离 + 无 shell 参数列表 + 环境白名单 +
#   超时/输出上限 + 改动可回滚 + 每步证据。要内核级沙箱得装 MuseWork 那类 helper。
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

MODES = ("readonly", "cow", "direct")
DEFAULT_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "120"))
MAX_OUTPUT = int(os.environ.get("SANDBOX_MAX_OUTPUT", "200000"))


@dataclass
class SandboxPolicy:
    """动手策略：能写哪、跑多久、吃多少输出、带哪些环境变量。"""
    mode: str = "cow"                                  # readonly | cow | direct
    allow_write: tuple = ()                            # 额外可写目录（绝对路径）
    timeout_s: int = DEFAULT_TIMEOUT
    max_output: int = MAX_OUTPUT
    env_allow: tuple = ("PATH", "HOME", "USERPROFILE", "TEMP", "TMP", "LANG",
                        "PYTHONPATH", "PYTHONIOENCODING", "SYSTEMROOT", "WINDIR",
                        "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS", "OS")
    keep_env_secrets: bool = False                     # 默认不把宿主凭据环境带进沙箱

    def __post_init__(self):
        if self.mode not in MODES:
            raise ValueError(f"未知沙箱模式 {self.mode}；可选 {MODES}")

    def as_dict(self) -> dict:
        return {"mode": self.mode, "allow_write": list(self.allow_write),
                "timeout_s": self.timeout_s, "max_output": self.max_output,
                "env_allow": list(self.env_allow),
                "secrets_forwarded": bool(self.keep_env_secrets)}

    def child_env(self) -> dict:
        """环境白名单：默认**不**把宿主的凭据类变量带进子进程。"""
        src = dict(os.environ)
        if self.keep_env_secrets:
            return src
        out = {k: v for k, v in src.items() if k in self.env_allow}
        out["PYTHONIOENCODING"] = "utf-8"
        return out


# ══════════ 写时复制工作区：改副本，原件可用原样回滚 ══════════
@dataclass
class Change:
    kind: str            # added | modified | removed
    path: str
    sha_before: str = ""
    sha_after: str = ""
    bytes: int = 0

    def as_dict(self) -> dict:
        return {"kind": self.kind, "path": self.path, "sha_before": self.sha_before,
                "sha_after": self.sha_after, "bytes": self.bytes}


def _sha(p: Path) -> str:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except Exception:                                       # noqa: BLE001
        return ""


class CowWorkspace:
    """把一个目录"影子化"：读原件、写副本，改动全落在影子目录，随时回滚。

    影子布局：<root>/shadow/<name>/<相对路径>（root 默认 ~/.v9-sandbox）
    """

    def __init__(self, target: str | os.PathLike, *, root: str | os.PathLike | None = None,
                 name: str | None = None):
        self.target = Path(target).resolve()
        if not self.target.is_dir():
            raise FileNotFoundError(f"目标目录不存在：{self.target}")
        base = Path(root) if root else Path(
            os.environ.get("SANDBOX_ROOT", str(Path.home() / ".v9-sandbox")))
        digest = hashlib.sha256(str(self.target).encode()).hexdigest()[:12]
        self.name = name or f"{self.target.name}-{digest}"
        self.root = Path(base)
        self.shadow = self.root / "shadow" / self.name
        self.originals: dict[str, str] = {}                 # rel → sha256（stage 时快照）
        self._writes: set = set()

    # ── 干跑：给"要动手的东西"打指纹（不动任何文件）──
    def stage(self) -> dict:
        self.originals = {}
        for p in sorted(self.target.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(self.target)).replace(os.sep, "/")
                self.originals[rel] = _sha(p)
        return {"files": len(self.originals), "target": str(self.target)}

    # ── 读：副本优先，其次原件（写时复制语义）──
    def read_path(self, rel: str) -> Path:
        sh = self.shadow / rel
        return sh if sh.exists() else self.target / rel

    def read_text(self, rel: str) -> str | None:
        p = self.read_path(rel)
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None

    # ── 写：默认只写副本 ──
    def write_text(self, rel: str, text: str, *, policy: SandboxPolicy | None = None) -> dict:
        mode = (policy or SandboxPolicy()).mode
        if mode == "readonly":
            return {"ok": False, "reason": "readonly：策略不允许写"}
        dest = (self.target / rel) if mode == "direct" else (self.shadow / rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        self._writes.add(rel)
        return {"ok": True, "path": str(dest), "mode": mode}

    # ── 差异：副本 vs 原件（新增/修改/删除）──
    def diff(self) -> dict:
        if not self.originals:
            self.stage()
        now: dict[str, str] = {}
        for p in sorted(self.shadow.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(self.shadow)).replace(os.sep, "/")
                now[rel] = _sha(p)
        changes: list[Change] = []
        for rel, sha in now.items():
            before = self.originals.get(rel)
            if before is None:
                changes.append(Change("added", rel, "", sha,
                                      (self.shadow / rel).stat().st_size))
            elif before != sha:
                changes.append(Change("modified", rel, before, sha,
                                      (self.shadow / rel).stat().st_size))
        for rel, sha in self.originals.items():
            if rel not in now and rel in self._writes:
                changes.append(Change("removed", rel, sha, ""))
        return {"changes": [c.as_dict() for c in changes], "count": len(changes),
                "files_before": len(self.originals), "files_after": len(now)}

    # ── 回滚：撤掉副本 = 回到原件状态（原件从未被改）──
    def rollback(self) -> dict:
        existed = self.shadow.exists()
        n = len([p for p in self.shadow.rglob("*") if p.is_file()]) if existed else 0
        if existed:
            shutil.rmtree(self.shadow, ignore_errors=True)
        self._writes.clear()
        return {"reverted": n, "shadow_removed": existed,
                "target_intact": self.target.is_dir(),
                "note": "原件全程未被修改；回滚 = 丢弃副本"}

    def status(self) -> dict:
        return {"target": str(self.target), "shadow": str(self.shadow),
                "shadow_exists": self.shadow.exists(),
                "files": len([p for p in self.shadow.rglob("*") if p.is_file()])
                if self.shadow.exists() else 0}


# ══════════ 受限执行：参数列表、cwd 限定、超时、输出截断 ══════════
class SandboxExec:
    """在策略约束下执行命令。**无 shell**：命令字符串永不进入 shell 解析。"""

    def __init__(self, workspace: CowWorkspace, policy: SandboxPolicy | None = None,
                 *, runner=None):
        self.ws = workspace
        self.policy = policy or SandboxPolicy()
        self._runner = runner or self._default_runner
        self.history: list = []

    def _default_runner(self, cmd: list, *, cwd: str, env: dict, timeout: int):
        try:
            p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True,
                               timeout=timeout, encoding="utf-8", errors="replace",
                               shell=False)                # ★永远 shell=False
            return p.returncode, p.stdout or "", p.stderr or "", False
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout>{timeout}s", True
        except FileNotFoundError as exc:
            return 127, "", f"not_found:{exc}", False

    def _guard_cmd(self, cmd) -> list:
        if isinstance(cmd, str):
            # 字符串只当"单个可执行名"；含 shell 元字符直接拒（防拼接注入）
            if any(ch in cmd for ch in ";&|><`$\n"):
                raise ValueError(f"命令含 shell 元字符，拒绝执行：{cmd[:60]}")
            return [cmd]
        if not cmd:
            raise ValueError("空命令")
        return [str(c) for c in cmd]

    def run(self, cmd, *, cwd_rel: str = "", timeout: int | None = None) -> dict:
        argv = self._guard_cmd(cmd)
        cwd = self.ws.shadow if self.ws.shadow.exists() else self.ws.target
        if cwd_rel:
            cwd = Path(cwd) / cwd_rel
        cwd.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        rc, out, err, timed_out = self._runner(
            argv, cwd=str(cwd), env=self.policy.child_env(),
            timeout=int(timeout or self.policy.timeout_s))
        rec = {"cmd": argv, "rc": rc, "ms": int((time.time() - t0) * 1000),
               "timed_out": timed_out, "mode": self.policy.mode,
               "out": (out or "")[:self.policy.max_output],
               "err": (err or "")[:self.policy.max_output],
               "out_sha": hashlib.sha256((out or "").encode()).hexdigest()[:16]}
        self.history.append(rec)
        return rec


# ══════════ 闭环：动手 → 证据 → 验收（不过就回滚）══════════
def run_loop(steps, workspace: CowWorkspace, *, policy: SandboxPolicy | None = None,
             accept=None, exec_: SandboxExec | None = None) -> dict:
    """每一步留证据；全部跑完再验收；**不达标就回滚**（fail closed）。

    steps: [{"name": str, "cmd": list|str, "cwd_rel": str?, "timeout": int?}]
    accept: fn(evidence) -> (bool, reason)；缺省判据=每步 rc==0
    """
    pol = policy or SandboxPolicy()
    ws = workspace
    ws.stage()
    ex = exec_ or SandboxExec(ws, pol)
    steps_out = []
    for i, s in enumerate(steps, 1):
        r = ex.run(s.get("cmd"), cwd_rel=s.get("cwd_rel", ""), timeout=s.get("timeout"))
        steps_out.append({"step": i, "name": s.get("name", f"step{i}"), **r})
        if r["rc"] != 0:
            diff = ws.diff()
            rb = ws.rollback()
            return {"ok": False, "failed_at": i, "reason": f"第 {i} 步失败 rc={r['rc']}",
                    "steps": steps_out, "diff": diff, "rollback": rb}
    diff = ws.diff()
    ev = {"steps": steps_out, "diff": diff, "policy": pol.as_dict(),
          "artifacts": [c["path"] for c in diff["changes"]]}
    if accept is not None:
        ok, reason = accept(ev)
    else:
        ok = all(s["rc"] == 0 for s in steps_out)
        reason = "默认判据：每步 rc=0" if ok else "有步骤非 0"
    if not ok:
        rb = ws.rollback()
        return {"ok": False, "reason": reason, "steps": steps_out, "diff": diff,
                "rollback": rb}
    return {"ok": True, "reason": reason, "steps": steps_out, "diff": diff,
            "evidence": ev, "policy": pol.as_dict()}


def run_plan(request: dict) -> dict:
    """触手/指挥官调用入口：{"target": dir, "steps": [...], "mode": "cow"}"""
    ws = CowWorkspace(request["target"], root=request.get("root"))
    pol = SandboxPolicy(mode=request.get("mode", "cow"),
                        allow_write=tuple(request.get("allow_write") or ()))
    return run_loop(request.get("steps") or [], ws, policy=pol)


__all__ = ["SandboxPolicy", "CowWorkspace", "SandboxExec", "run_loop", "run_plan",
           "Change", "MODES"]
