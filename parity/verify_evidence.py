# parity/verify_evidence.py —— 在 CI 里重新读回证据并核 SHA-256
import argparse, hashlib, json, sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    a = ap.parse_args()

    report = Path(a.report).resolve()
    if not report.is_file():
        print(f"报告不存在: {report}", file=sys.stderr); return 2
    try:
        data = json.loads(report.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"报告解析失败: {e}", file=sys.stderr); return 2

    root = report.parent.resolve()          # 证据必须落在本次 run 目录内
    checked = bad = 0
    for cap in data.get("capabilities", []):
        for ck in cap.get("checks", []):
            for ev in ck.get("evidence", []):
                try:
                    p = Path(ev["path"]).resolve()
                    p.relative_to(root)                      # 拒绝越界
                    blob = p.read_bytes()                    # 真回读
                    if len(blob) != ev["bytes"]:
                        raise ValueError(f"size {len(blob)} != {ev['bytes']}")
                    if hashlib.sha256(blob).hexdigest() != ev["sha256"]:
                        raise ValueError("sha256 mismatch")
                    checked += 1
                except Exception as e:
                    bad += 1
                    print(f"证据校验失败 {ev.get('path')}: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)

    # 有 pass 却零证据 → 直接判不通过（堵"报告说绿但没证据"）
    if data.get("summary", {}).get("pass", 0) > 0 and checked == 0:
        print("存在 pass 能力但没有任何证据文件 —— 拒绝", file=sys.stderr)
        bad += 1

    print(f"证据校验：通过 {checked} 个，失败 {bad} 个")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
