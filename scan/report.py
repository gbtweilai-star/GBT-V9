# scan/report.py —— 覆盖率对账报告：标出漏扫目标和责任触手
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import time
from pathlib import Path

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
            L.append(f"- `{target}`: {detail[:200]}")

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
