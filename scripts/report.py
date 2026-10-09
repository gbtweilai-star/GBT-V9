# report.py —— 扫描结束后生成对账报告：哪些没扫到？谁的责任？
def reconciliation_report(ledger, targets, tentacle_ids):
    done = {}                                  # target -> 扫过的触手
    for ts, scanner, target, status, detail, v in ledger.all_rows():
        if status in ("scanned", "vuln", "cross"):
            done.setdefault(target, []).append(scanner)
    missing = sorted(set(targets) - set(done))
    blocked = ledger.by_status("blocked")
    vulns   = ledger.by_status("vuln")
    n_t = max(len(tentacle_ids), 1)
    lines = [
        "# 触手扫描对账报告", "",
        f"| 指标 | 数值 |", f"|---|---|",
        f"| 应扫目标 | {len(targets)} |",
        f"| 已扫目标 | {len(set(targets) & set(done))} |",
        f"| **覆盖率** | **{len(done) / max(len(targets),1)*100:.1f}%** |",
        f"| 疑似漏洞 | {len(vulns)} | 卡点 | {len(blocked)} |",
        "", "## 漏扫目标（硬指标未达标项）", ""]
    if missing:
        lines += [f"- `{m}` → **责任触手未分工**，建议派给 {tentacle_ids[hash(m) % n_t]}"
                  for m in missing]
    else:
        lines.append("✅ 无漏扫，覆盖率 100%，硬指标达标")
    if blocked:
        lines += ["", "## 卡点清单（待主脑裁决）"] + [f"- `{t}`: {d}" for _,t,d in blocked]
    if vulns:
        lines += ["", "## 漏洞清单"] + [f"- `{t}` ({s}): {d}" for _,t,s,d in vulns]
    report = "\n".join(lines)
    open("reconciliation_report.md", "w", encoding="utf-8").write(report)
    return report
