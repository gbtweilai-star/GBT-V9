# scan/report.py —— 覆盖率对账报告：标出漏扫目标和责任触手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import re
import time
from pathlib import Path

_RULE_RE = re.compile(r"'rule':\s*'([^']*)'")
_LEVEL_RE = re.compile(r"'level':\s*'([^']*)'")
_LINE_RE = re.compile(r"'line':\s*(\d+)")
_NOTE_RE = re.compile(r"'detail':\s*'([^']*)'")


def _compact_finding(detail: str) -> str:
    """把落账的 finding 压成"人能照着修"的一行：规则(级别)@行号 说明。

    真机教训：目标路径一长，`detail[:200]` 会在**规则名之前**被切掉——报告里
    只剩一长串路径，看报告的人根本认不出这行是"明文密钥"还是"危险调用"。
    所以先提规则名与说明，再截断。
    """
    rules = _RULE_RE.findall(detail or "")
    if not rules:
        return (detail or "")[:200]
    levels = _LEVEL_RE.findall(detail or "")
    lines = _LINE_RE.findall(detail or "")
    notes = _NOTE_RE.findall(detail or "")
    parts = []
    for i, rule in enumerate(rules[:6]):
        lv = levels[i] if i < len(levels) else ""
        ln = lines[i] if i < len(lines) else ""
        note = notes[i] if i < len(notes) else ""
        parts.append(f"{rule}({lv or '—'})" + (f"@行{ln}" if ln else "") +
                     (f" {note[:60]}" if note else ""))
    extra = len(rules) - len(parts)
    rep = "；".join(parts) + (f"；…共 {len(rules)} 项" if extra > 0 else "")
    return rep[:200]


def reconciliation_report(ledger, targets, tentacle_ids, out_path="reconciliation_report.md"):
    """读账本，对账应扫/已扫，点名漏扫 + 建议责任触手"""
    targets = set(targets)
    done = {}                                  # target -> [scanner,...]
    for row in ledger.all_rows():
        # (ts, scanner, target, status, detail, brain_verdict)
        _, scanner, target, status, _, _ = row
        if status in ("scanned", "vuln", "cross"):
            done.setdefault(target, []).append(scanner)

    missing = sorted(targets - set(done))
    blocked = ledger.by_status("blocked")
    vulns   = ledger.by_status("vuln")
    n_t = max(len(tentacle_ids), 1)
    coverage = len(done) / max(len(targets), 1) * 100

    L = []
    L.append("# 触手扫描对账报告")
    L.append(f"\n生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 应扫目标 | {len(targets)} |")
    L.append(f"| 已扫目标 | {len(done)} |")
    L.append(f"| **覆盖率** | **{coverage:.1f}%** |")
    L.append(f"| 疑似漏洞 | {len(vulns)} |")
    L.append(f"| 卡点 | {len(blocked)} |")
    L.append(f"| 参与触手 | {len(tentacle_ids)} |")

    L.append("\n## 漏扫目标（硬指标未达标项）\n")
    if missing:
        for m in missing:
            # 按 hash 稳定分派，同名目标总建议给同一触手
            owner = tentacle_ids[hash(m) % n_t]
            L.append(f"- `{m}` → 建议派给 **{owner}**")
    else:
        L.append("✅ 无漏扫，覆盖率 100%，硬指标达标")

    if blocked:
        L.append("\n## 卡点清单（待主脑裁决）\n")
        for ts, target, detail in blocked:
            L.append(f"- `{target}`: {detail}")

    if vulns:
        L.append("\n## 漏洞清单\n")
        for ts, target, detail in vulns:
            L.append(f"- `{target}`: {_compact_finding(detail)}")

    L.append("\n## 触手分工\n")
    L.append("| 触手 | 扫描数 |")
    L.append("|---|---|")
    for tid in tentacle_ids:
        cnt = sum(1 for who in done.values() if tid in who)
        L.append(f"| {tid} | {cnt} |")

    report = "\n".join(L)
    if out_path:
        Path(out_path).write_text(report, encoding="utf-8")
    return report
