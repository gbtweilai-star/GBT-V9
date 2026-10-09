# core/repo_ways.py —— 「同一个项目，6 种打开方式」融进触手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：把 GitDiagram / DeepWiki / Gitingest / GitHub1s / github.dev /
#   StackBlitz 这六种"打开一个项目的方式"融合进触手 —— 由触手自己实现，不依赖外部服务。
#
# 六种方式 → 触手能力（全部本地、可审计）：
#   way1 看结构  repo_structure   目录树 + 模块依赖图（谁 import 谁）
#   way2 读讲解  repo_brief       逐模块讲解（规则摘要；可选用网关润色，绝不编造）
#   way3 交给AI  repo_ingest      按预算把项目打成"上下文包"（给触手/AI 用）
#   way4 读源码  repo_source      带行号的源码读取（只读）
#   way5 改文件  repo_edit        受控修改：先备份 → 改 → 留 diff → 落账（走钩子）
#   way6 跑项目  repo_run         白名单命令在项目目录里跑（超时杀掉，输出留证）
#
# 纪律：只读为主；改与跑必须过 core/hooks；越出项目根的路径一律拒绝（防目录穿越）。
import os
import re
import time
from pathlib import Path

from core import hooks as H
from core.swallow import swallow as _swallow

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEXT_SUFFIX = (".py", ".js", ".ts", ".tsx", ".md", ".json", ".yml", ".yaml", ".toml",
               ".ini", ".cfg", ".html", ".css", ".sh", ".bat", ".sql", ".txt", ".rst")
SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".venv", "venv", "dist", "build",
             ".next", ".pytest_cache", "release"}


def _root(repo: str) -> Path:
    """解析并锁定项目根：必须在白名单里（默认就是 V9 自己），杜绝任意目录。"""
    p = Path(str(repo or "")).resolve()
    allowed = [ROOT, ROOT.parent]
    for a in allowed:
        try:
            if str(p).startswith(str(a)) and p.is_dir():
                return p
        except OSError:
            continue
    raise H.HookError(f"项目根不在允许范围内：{p}（默认只允许 V9 自己的项目）")


def _safe(root: Path, rel: str) -> Path:
    p = (root / str(rel or "").replace("\\", "/")).resolve()
    if not str(p).startswith(str(root)):
        raise H.HookError(f"路径越出项目根：{rel}")
    return p


# ─────────── way1 看结构 ───────────
def structure(repo: str = "", *, max_files: int = 400) -> dict:
    root = _root(repo)
    files, imports = [], {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel_dir = Path(dirpath).relative_to(root)
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = str((rel_dir / fn)).replace("\\", "/")
            if fn.endswith(".py") and len(files) < max_files:
                files.append(rel)
                try:
                    src = p.read_text(encoding="utf-8", errors="replace")
                    mods = sorted(set(re.findall(
                        r"^\s*(?:from|import)\s+([a-zA-Z_][\w.]*)", src, re.M)))
                    own = {f.split("/")[-1][:-3] for f in files}
                    deps = [m for m in mods if m.split(".")[0] in own]
                    if deps:
                        imports[rel] = deps
                except OSError as e:
                    _swallow(__file__, e)

    groups: dict = {}
    for f in files:
        g = f.split("/")[0] if "/" in f else "(根)"
        groups[g] = groups.get(g, 0) + 1
    return {"项目根": str(root), "文件数": len(files), "分组": groups,
            "依赖图": imports, "Python文件": files[:60],
            "口径": "结构=真实 os.walk + import 解析；不是画的"}


# ─────────── way2 读讲解 ───────────
def brief(repo: str = "", *, modules: int = 8) -> dict:
    root = _root(repo)
    st = structure(repo)
    picks = st["Python文件"][: max(1, int(modules))]
    out = []
    for rel in picks:
        try:
            src = (_root(repo) / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        doc = re.search(r'"""(.*?)"""', src, re.S)
        classes = re.findall(r"^class\s+(\w+)", src, re.M)[:6]
        funcs = re.findall(r"^def\s+(\w+)|^\s{4}def\s+(\w+)", src, re.M)[:8]
        fn = [a or b for a, b in funcs]
        out.append({"模块": rel, "行数": src.count("\n") + 1,
                    "doc": (doc.group(1).strip()[:160] if doc else "（无 docstring）"),
                    "类": classes, "函数": fn})
    return {"项目根": str(root), "模块讲解": out,
            "口径": "规则摘要（docstring/类/函数名真实提取）；要更人话的讲解可再走网关润色"}


# ─────────── way3 交给AI（ingest） ───────────
def ingest(repo: str = "", *, budget_chars: int = 60000, out_name: str = "") -> dict:
    """把项目按预算打成上下文包：优先 README/入口/核心模块，写进 state 供触手取用。"""
    root = _root(repo)
    order = ["README.md", "readme.md", "package.json", "main.py", "brain.py"]
    st = structure(repo)
    order += [f for f in st["Python文件"] if f not in order]
    used, parts = 0, []
    for rel in order:
        p = root / rel
        if not p.is_file():
            continue
        try:
            src = p.read_text(encoding="utf-8", errors="replace")[: budget_chars - used]
        except OSError:
            continue
        if not src.strip():
            continue
        block = f"\n\n===== {rel} =====\n{src}"
        parts.append(block)
        used += len(block)
        if used >= budget_chars:
            break
    pack = f"# 项目上下文包（{root.name}）· {used} 字符\n" + "".join(parts)
    outp = ROOT.joinpath("state", "repo_packs", (out_name or root.name) + ".md")
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(pack, encoding="utf-8")
    return {"ok": True, "包文件": str(outp), "字符": used, "预算": budget_chars,
            "含文件": len(parts), "给谁用": "触手/AI 直接读这个包就能上手项目"}


# ─────────── way4 读源码 ───────────
def source(repo: str, rel: str, *, start: int = 1, lines: int = 120) -> dict:
    root = _root(repo)
    p = _safe(root, rel)
    if not p.is_file():
        return {"ok": False, "reason": f"没有这个文件：{rel}"}
    src = p.read_text(encoding="utf-8", errors="replace").split("\n")
    a = max(1, int(start))
    seg = src[a - 1: a - 1 + max(1, int(lines))]
    return {"ok": True, "文件": rel, "起": a, "行": [
        {"n": a + i, "text": t[:300]} for i, t in enumerate(seg)]}


# ─────────── way5 改文件（受控：备份 + diff + 落账 + 钩子） ───────────
def edit(repo: str, rel: str, new_text: str, *, by: str = "触手") -> dict:
    root = _root(repo)
    p = _safe(root, rel)
    if p.suffix not in TEXT_SUFFIX:
        return {"ok": False, "reason": f"只允许改文本文件（{p.suffix} 不在白名单）"}
    g = H.Guard(f"repo_edit:{rel}", owner=by, must_steps=("备份", "写入", "留账"))
    old = ""
    with g.step("备份", expect="旧内容留底") as s:
        old = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
        bak = p.with_suffix(p.suffix + ".bak")
        if old:
            bak.write_text(old, encoding="utf-8")
        s.evidence(备份=str(bak), fingerprint=H.fingerprint(old[:200]))
    with g.step("写入", expect="新内容真的落盘") as s:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(new_text or ""), encoding="utf-8")
        now = p.read_text(encoding="utf-8", errors="replace")
        if now != str(new_text or ""):
            raise H.HookError("写入后复读不一致")
        s.evidence(新行数=now.count("\n") + 1, fingerprint=H.fingerprint(now[:200]))
    with g.step("留账", expect="变更进台账") as s:
        try:
            from core import deploy_ledger as DL
            DL.record_change(str(p), detail=f"repo_edit by {by}", kind="modify")
            s.evidence(台账="已记", fingerprint=H.fingerprint(rel))
        except Exception as exc:                           # noqa: BLE001
            raise H.HookError(f"落账失败：{type(exc).__name__}") from exc
    a = g.finish()
    return {"ok": True, "文件": str(p), "旧行数": old.count("\n") + 1,
            "新行数": str(new_text or "").count("\n") + 1, "备份": str(p.with_suffix(p.suffix + ".bak")),
            "钩子": {"通过": a["通过"], "证据条数": a["证据条数"]}}


# ─────────── way6 跑项目（白名单命令） ───────────
_RUN_ALLOW = (
    ("pytest", ("python", "-m", "pytest", "-q", "-p", "no:cacheprovider")),
    ("compileall", ("python", "-m", "compileall", "-q", "core", "panel", "senses", "body", "audit", "tools", "common", "media", "scan", "skills", "workflows")),
)


def run_project(repo: str, command: str = "pytest", *, timeout: int = 600) -> dict:
    """在项目目录里跑**白名单**命令（参数列表、无 shell），输出留证。"""
    root = _root(repo)
    key = str(command or "").strip().lower()
    if key not in dict(_RUN_ALLOW):
        return {"ok": False, "reason": f"命令只认白名单：{[k for k, _ in _RUN_ALLOW]}"}
    argv = list(dict(_RUN_ALLOW)[key])
    t0 = time.time()
    try:
        r = __import__("subprocess").run(argv, cwd=str(root), capture_output=True,
                                         timeout=int(timeout), shell=False)
        out = (r.stdout or b"").decode("utf-8", "replace")[-4000:]
        err = (r.stderr or b"").decode("utf-8", "replace")[-1500:]
    except __import__("subprocess").TimeoutExpired as exc:
        return {"ok": False, "reason": f"超时（>{timeout}s）已杀掉", "输出": str(exc)[:200]}
    ok = r.returncode == 0
    H.gate("命令执行", True)
    try:
        from core import deploy_ledger as DL
        DL.record("scan", f"repo_run:{key}",
                  detail=f"rc={r.returncode} 耗时 {int((time.time()-t0))}s",
                  before="", after=out[-80:], ok=ok, reason=err[-160:])
    except Exception as e:
        _swallow(__file__, e)
    return {"ok": ok, "命令": argv, "rc": r.returncode, "耗时s": int(time.time() - t0),
            "输出尾": out[-800:], "错误尾": err[-400:]}


WAYS = (("way1", "看结构", structure), ("way2", "读讲解", brief),
        ("way3", "交给AI", ingest), ("way4", "读源码", source),
        ("way5", "改文件", edit), ("way6", "跑项目", run_project))


def status() -> dict:
    return {"六种打开方式": [{"way": w, "名称": n, "能力": getattr(f, "__name__", "")}
                             for w, n, f in WAYS],
            "纪律": "只读为主；改与跑走钩子+白名单；路径锁死在项目根内",
            "口径": "不依赖外部服务，全部触手本地可实现"}


__all__ = ["WAYS", "structure", "brief", "ingest", "source", "edit", "run_project", "status"]
