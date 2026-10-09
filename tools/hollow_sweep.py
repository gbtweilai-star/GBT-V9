# tools/hollow_sweep.py —— 全仓"空壳/占位/漏洞/后门"排查器（可复跑，输出可复核清单）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 排查口径（主人要求：排除故障与异常、疑是占位空壳、漏洞、病毒木马）：
#   ① 空壳/占位：TODO/FIXME/XXX/HACK、NotImplementedError、裸 pass、只有 return None 的假实现、
#      0 字节或 <1KB 的假素材文件（真机踩过 146 个 5 字节假帧）
#   ② 被吞的异常：`except: pass`（真故障会被吃掉，最难查）
#   ③ 危险调用：eval/exec/compile、os.system、subprocess(shell=True)、pickle.loads、
#      yaml.load（不带 SafeLoader）、关闭 TLS 校验
#   ④ 凭据泄漏：源码里的真密钥字面量（sk-/AKIA/私钥块）
#   ⑤ 后门/持久化可疑点：计划任务里指向临时/可写目录、编码执行的命令
# 说明：本文件是**检测器**，下面的模式串只是"被搜索的文本"，不参与任何网络请求；
#      其中"关闭 TLS 校验"那条按片段拼出，免得把检测规则本身误判成真的关了校验。
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SKIP_DIRS = {"node_modules", ".git", "__pycache__", "release", "win-unpacked", ".next",
             "state", "devoured", "data", "archived_files",
             # 以下都是**第三方/打包产物/历史归档**，不是本项目源码，扫进来只会淹没真问题
             "resources", "portable", "packages", "caps", "legacy_spec", "venv", ".venv",
             "site-packages", "dist", "build", "octop-home",
             # _archive = 历史归档（含隔离的危险遗留原文）；隔离区不参与"当前代码"排查
             "_archive"}
CODE_EXT = {".py", ".js", ".ts", ".sh", ".cmd", ".bat", ".ps1", ".yml", ".yaml", ".json"}
SELF = {"hollow_sweep.py"}

# 检测目标片段（拼出来，避免误报）
_TLS_OFF = "verify" + chr(61) + "False"
_SHELL_ON = "shell" + chr(61) + "True"

PATTERNS = (
    ("空壳·TODO/FIXME", "warn", re.compile(r"(TODO|FIXME|XXX|HACK)\b")),
    ("空壳·未实现", "warn", re.compile(r"raise\s+NotImplementedError|NotImplemented\b")),
    ("空壳·裸 pass 兜底", "warn", re.compile(r"^\s*pass\s*(#.*)?$")),
    ("吞异常 except pass", "high", re.compile(r"except[^:]*:\s*(#.*)?$\n\s*pass\b")),
    ("危险调用 eval/exec", "high",
     # 排除 re.compile( / .execute( / subprocess.run( 这些正常用法，只抓真的动态求值
     re.compile(r"(?<![.\w])(eval|exec)\s*\(|(?<![.\w])compile\s*\((?![^)]*\bcode\b)")),
    ("危险调用 os.system", "high", re.compile(r"os\.system\s*\(")),
    ("危险调用 shell=True", "high", re.compile(re.escape(_SHELL_ON))),
    ("危险调用 pickle.loads", "high", re.compile(r"pickle\.loads?\s*\(")),
    ("危险调用 yaml.load", "high", re.compile(r"yaml\.load\s*\((?![^)]*SafeLoader)")),
    ("危险调用 关证书校验", "high", re.compile(re.escape(_TLS_OFF))),
    ("凭据疑似·sk-", "high", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("凭据疑似·AKIA", "high", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("凭据疑似·私钥块", "high", re.compile(r"BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY")),
)


# 已知良性（逐条写明理由；命中这些位置不再算高危，但仍在"已知良性"里列出，便于复核）
KNOWN_BENIGN = (
    ("scan_rules.py", "本项目自己的检测规则文件：命中的是**被搜索的规则串**"),
    ("core/coder.py", "已修复：现为 shell=False + 列表参数；命中来自修复说明注释"),
    ("parity/operations.py", "parity 验收夹具：故意写入危险样例供扫描器识别"),
    ("tests/test_e2e.py", "端到端测试夹具：故意写入危险样例与 AWS **官方示例**密钥"),
    ("AKIAIOSFODNN7EXAMPLE", "AWS 官方文档里的示例密钥（非真实凭据）"),
)


def _benign(pos: str, frag: str) -> str:
    """已知良性判定：路径统一成 / 再匹配（Windows 上是反斜杠，别漏）。"""
    p = str(pos).replace("\\", "/")
    f = str(frag)
    for needle, why in KNOWN_BENIGN:
        if needle in p or needle in f:
            return why
    return ""


def _iter_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            p = Path(base).joinpath(f)
            if p.suffix.lower() in CODE_EXT and p.name not in SELF:
                yield p


def scan_code() -> list:
    hits = []
    for p in _iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, level, rx in PATTERNS:
            for m in rx.finditer(text):
                line = text[:m.start()].count("\n") + 1
                lines = text.splitlines()
                frag = lines[line - 1].strip()[:100] if 0 < line <= len(lines) else ""
                hits.append({"类别": name, "级别": level,
                             "位置": f"{p.relative_to(ROOT)}:{line}", "片段": frag})
    return hits


def scan_placeholder_files() -> list:
    """占位素材：0 字节或 <1KB 的图片/音视频（真机踩过 146 个 5 字节假帧）。

    跳过归档区/素材区，避免把"归档后的占位"再扫一遍（自指）。
    """
    hits = []
    exts = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".wav", ".mp3", ".gif"}
    skip = {"node_modules", ".git", "__pycache__", "_archive", "state", "devoured",
            "release", "win-unpacked"}
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip]
        for f in files:
            p = Path(base).joinpath(f)
            if p.suffix.lower() in exts:
                try:
                    sz = p.stat().st_size
                except OSError:
                    continue
                if sz < 1024:
                    hits.append({"类别": "占位素材（<1KB）", "级别": "warn",
                                 "位置": str(p.relative_to(ROOT)), "片段": f"{sz} 字节"})
    return hits


def scan_persistence() -> list:
    """可疑常驻/持久化：计划任务里指向临时/可写目录、或编码执行的命令。"""
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-ScheduledTask | Where-Object {$_.State -ne 'Disabled'} | "
             "ForEach-Object { $_.TaskName + '|' + (($_.Actions | ForEach-Object "
             "{ $_.Execute + ' ' + $_.Arguments }) -join ' ') }"],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        text = (out.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:                                  # noqa: BLE001
        return [{"类别": "持久化检查", "级别": "info", "位置": "scheduler",
                 "片段": f"读取失败 {type(exc).__name__}"}]
    sus = re.compile(r"\\Temp\\|\\Downloads\\|\\Users\\Public\\|AppData\\Local\\Temp"
                     r"|\.vbs|powershell\s+-enc|FromBase64String", re.I)
    hits = []
    for line in text.splitlines():
        if sus.search(line):
            hits.append({"类别": "持久化可疑", "级别": "high", "位置": "计划任务",
                         "片段": line.strip()[:160]})
    return hits


def report() -> dict:
    code = scan_code()
    ph = scan_placeholder_files()
    per = scan_persistence()
    benign, real = [], []
    for h in code + per:
        why = _benign(h["位置"], h["片段"])
        (benign if why else real).append({**h, "已知良性理由": why} if why else h)
    allh = real + ph
    by_level: dict = {}
    for h in allh:
        by_level[h["级别"]] = by_level.get(h["级别"], 0) + 1
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "扫描文件数": sum(1 for _ in _iter_files()),
            "命中总数": len(allh), "按级别": by_level,
            "已知良性": benign,
            "按类别": {k: sum(1 for h in allh if h["类别"] == k)
                      for k in sorted({h["类别"] for h in allh})},
            "明细": allh[:400],
            "高危": [h for h in allh if h["级别"] == "high"][:120],
            "注": "只读扫描：不改代码；high=需人工确认；已知良性项附理由单独列出",
            "exit_code": 1 if any(h["级别"] == "high" for h in allh) else 0}


def main(argv=None) -> int:
    rep = report()
    print(json.dumps({k: v for k, v in rep.items() if k != "明细"},
                     ensure_ascii=False, indent=1))
    for h in rep["高危"][:40]:
        print(f'  [{h["级别"]}] {h["类别"]} @ {h["位置"]} :: {h["片段"]}')
    return rep["exit_code"]


if __name__ == "__main__":                                     # pragma: no cover
    sys.exit(main())
