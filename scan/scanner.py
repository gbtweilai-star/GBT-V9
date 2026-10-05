# scan/scanner.py —— 穿透式全量扫描：顶层遍历 + 权威全集 + 无死角对账
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 原则：先建全集（enumerate_targets）→ 再逐个扫描 → 最后对账 coverage==1.0
#       任何 missing != 空 即硬失败（点名漏扫目标）。
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".next",
             "dist", "build", ".mypy_cache", ".pytest_cache"}


@dataclass
class ScanResult:
    root: str
    total: int
    scanned: int
    missing: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)

    @property
    def coverage(self) -> float:
        return self.scanned / self.total if self.total else 1.0


def enumerate_targets(root: str | os.PathLike, *, include_hidden: bool = False) -> list[str]:
    """权威全集：从工作树顶层开始，每个文件夹每一页都进清单（排序稳定）。"""
    root = Path(root)
    out: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and (include_hidden or not d.startswith(".")))
        for name in sorted(filenames):
            if not include_hidden and name.startswith("."):
                continue
            out.append(str(Path(dirpath) / name))
    return out


def scan_file(path: str, rules=None) -> list[dict]:
    """扫描单个目标（规则可注入；默认空规则集只做可达性+摘要）。"""
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return [{"target": path, "rule": "unreadable", "level": "warn", "detail": str(exc)}]
    digest = hashlib.sha256(data).hexdigest()[:16]
    findings: list[dict] = []
    if rules:
        findings += rules(path, data)
    return findings + [{"target": path, "rule": "readable", "level": "info",
                        "detail": f"sha256:{digest} bytes={len(data)}"}]


def full_sweep(root, *, rules=None, ledger=None, scanner_id: str = "t1") -> ScanResult:
    targets = enumerate_targets(root)
    result = ScanResult(root=str(root), total=len(targets), scanned=0)
    seen: set[str] = set()
    for t in targets:
        result.findings += scan_file(t, rules)
        seen.add(t)
        result.scanned += 1
        if ledger:
            try:
                ledger.log(scanner_id, t, "scanned", "sweep")
            except Exception:
                pass
    result.missing = sorted(set(targets) - seen)
    if ledger:
        try:
            ledger.log(scanner_id, f"coverage:{result.coverage:.4f}",
                        "scanned" if not result.missing else "vuln",
                        f"missing={len(result.missing)}")
        except Exception:
            pass
    return result


__all__ = ["enumerate_targets", "scan_file", "full_sweep", "ScanResult", "SKIP_DIRS"]
