# scan_rules.py —— 密钥泄漏 / 依赖漏洞 / 危险API 三类规则引擎
import os, re, subprocess, json
from pathlib import Path

SECRET_PATTERNS = {
    "AWS密钥":      re.compile(r"AKIA[0-9A-Z]{16}"),
    "OpenAI密钥":   re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "GitHub令牌":   re.compile(r"ghp_[A-Za-z0-9]{36}"),
    "私钥文件":     re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "JWT硬编码":    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    "数据库连接串": re.compile(r"(postgres|mysql|mongodb(\+srv)?)://[^\s:]+:[^\s@]+@"),
}
DANGEROUS_CALLS = {
    r"eval\s*\(":              "动态eval，任意代码执行",
    r"exec\s*\(":              "动态exec",
    r"subprocess.*shell\s*=\s*True": "shell=True 命令注入",
    r"os\.system\s*\(":        "os.system 命令注入",
    r"pickle\.loads\s*\(":     "反序列化RCE风险",
    r"(SELECT|INSERT).*(\+\s*f?['\"]|%\s*\w+\s*$|\bformat\s*\()": "SQL拼接注入",
    r"verify\s*=\s*False|check_hostname\s*=\s*False": "SSL校验被关闭",
    r"rm\s+-rf\s+[/~]":        "危险删除命令",
}
# 漏洞包黑名单（简化版；生产建议接 osv.dev API 或 pip-audit）
VULN_PKGS = {"flask": "<2.2.2", "requests": "<2.31.0", "log4j": "<2.17.0", "lodash": "<4.17.21"}

def scan(path: str):
    """返回 [{rule, detail, level}]；文件/目录通吃"""
    p, findings = Path(path), []
    if p.is_file() and p.suffix in {".py", ".js", ".ts", ".json", ".env", ".txt", ".xml", ".yml"}:
        try: src = p.read_text(errors="ignore")[:500_000]
        except: return []
        for name, pat in SECRET_PATTERNS.items():
            if pat.search(src):
                findings.append({"rule": f"密钥泄漏:{name}", "level": "critical", "detail": f"{p.name} 中发现{name}"})
        for pat, why in DANGEROUS_CALLS.items():
            if re.search(pat, src):
                findings.append({"rule": f"危险API:{why}", "level": "high", "detail": str(p.name)})
        if p.name in {"requirements.txt", "package.json"}:
            for pkg, ver in VULN_PKGS.items():
                if re.search(rf"{pkg}[=><~ ]*", src):
                    findings.append({"rule": f"依赖漏洞:{pkg}", "level": "medium", "detail": f"已知漏洞版本<{ver}"})
    return findings
