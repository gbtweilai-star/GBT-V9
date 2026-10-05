# scan/scan_rules.py —— 扫描规则：密钥泄漏 / 依赖风险 / 危险 API
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纯标准库；规则是 (pattern, 名称, 级别, 说明) 的清单；命中即产出 finding。
import re

RULES: tuple[tuple[re.Pattern, str, str, str], ...] = (
    (re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16})"),
     "secret_leak", "critical", "疑似可用的 API 密钥/访问键明文出现在源码里"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "private_key", "critical", "私钥明文入库"),
    (re.compile(r"\beval\s*\(|\bexec\s*\(|os\.system\s*\(|shell\s*=\s*True"),
     "dangerous_call", "high", "动态执行/命令拼接调用"),
    (re.compile(r"DROP\s+TABLE|DELETE\s+FROM\s+\w+\s*;"),
     "destructive_sql", "high", "破坏性 SQL（须走确认门禁）"),
    (re.compile(r"http://(?!127\.0\.0\.1|localhost)"),
     "insecure_http", "medium", "明文 http 端点（非本机）"),
)

DEP_VULN_HINTS: tuple[tuple[str, str], ...] = (
    ("pyyaml==5.", "已知不安全版本线 (<6)"),
    ("requests==2.19", "已知 CVE 版本线"),
)

TEXT_SUFFIXES = (".py", ".js", ".ts", ".tsx", ".json", ".yml", ".yaml", ".env",
                 ".sh", ".sql", ".md", ".txt", ".ini", ".toml")


def scan_text(path: str, data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []
    out = []
    for rx, name, level, note in RULES:
        for m in rx.finditer(text):
            line = text[: m.start()].count("\n") + 1
            out.append({"target": path, "rule": name, "level": level,
                        "line": line, "detail": note})
    # 依赖提示（仅看依赖声明文件）
    low = path.lower()
    if low.endswith(("requirements.txt", "package.json", "pyproject.toml")):
        for needle, note in DEP_VULN_HINTS:
            if needle in text:
                out.append({"target": path, "rule": "dep_vuln", "level": "medium",
                            "detail": f"{needle}: {note}"})
    return out


def rules_for_sweep(path: str, data: bytes) -> list[dict]:
    """供 scan.scanner.full_sweep(rules=...) 使用；非文本一律跳过。"""
    if not path.lower().endswith(TEXT_SUFFIXES):
        return []
    return scan_text(path, data)


def scan(path: str) -> list[dict]:
    """便利封装：读文件并按规则扫描（cross_sweep 的默认复核函数）。
    读不到/不可读返回空清单，不抛——复核结论由调用方落账。"""
    if not path.lower().endswith(TEXT_SUFFIXES):
        return []
    try:
        with open(path, "rb") as f:
            return scan_text(path, f.read())
    except OSError:
        return []


__all__ = ["RULES", "scan_text", "rules_for_sweep", "scan"]
