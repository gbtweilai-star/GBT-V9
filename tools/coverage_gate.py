# tools/coverage_gate.py —— 覆盖率回归门禁：CI 判失败 + 夜间 issue 通知
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径（主人 2026-10-06）：某次提交覆盖率比上次下降超过阈值 →
#   ①CI job 判失败 ②面板标红并记告警 ③夜间 issue 通知
#   ④数字人语音播报（走 body/event_feeds.CoverageSnapshotFeed → 情绪喂料 → VoiceDirector.notify）
#
# 基线：state/coverage_baseline.json（可用 COVERAGE_BASELINE_FILE 覆盖）；阈值 COVERAGE_DROP_WARN（默认 2.0）
# 用法：
#   python tools/coverage_gate.py --measure        # 跑 pytest --cov 取真实覆盖率并更新基线
#   python tools/coverage_gate.py --check          # 与基线比较；回归则 exit 1（CI 门禁）
#   python tools/coverage_gate.py --check --issue  # 回归时写 issue 正文（夜间 workflow 用）
#   python tools/coverage_gate.py --set 87.5 --note "手工基线"
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = Path(os.environ.get("COVERAGE_BASELINE_FILE",
                               str(ROOT / "state" / "coverage_baseline.json")))
THRESHOLD = float(os.environ.get("COVERAGE_DROP_WARN", "2.0"))
REPORT_REL = "state/coverage.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _git_sha() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        return (r.stdout or "").strip()
    except Exception:                                        # noqa: BLE001
        return os.environ.get("GITHUB_SHA", "")[:7]


def measure() -> dict | None:
    """跑 pytest --cov。pytest-cov 缺失 → 如实返回 None（不编造数字）。"""
    try:
        import pytest_cov  # noqa: F401
    except Exception:                                        # noqa: BLE001
        print("[coverage] 未安装 pytest-cov：跳过测量（不编造数字）", file=sys.stderr)
        return None
    # 命令全字面量：不接受任何外部输入参与拼接。
    # 带 --basetemp：Windows 上 pytest 默认临时目录建符号链接会 PermissionError（本机已知问题）。
    rc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q",
         "--basetemp=state/pytest-cov-tmp",
         "--cov=core", "--cov=body", "--cov=panel", "--cov=senses",
         "--cov=audit", "--cov=scan", "--cov=skills",
         "--cov-report=json:state/coverage.json"],
        cwd=str(ROOT)).returncode
    if rc != 0:
        print(f"[coverage] pytest 退出码 {rc}（测试没过就不谈覆盖率门禁）", file=sys.stderr)
        return None
    report = ROOT / REPORT_REL
    if not report.exists():
        print("[coverage] 没生成覆盖报告", file=sys.stderr)
        return None
    data = json.loads(report.read_text(encoding="utf-8"))
    pct = float((data.get("totals") or {}).get("percent_covered") or 0.0)
    return {"overall_percent": round(pct, 2), "commit_sha": _git_sha(),
            "generated_at": _now(), "source": "pytest-cov"}


def load_baseline() -> dict | None:
    if BASELINE.exists():
        try:
            return json.loads(BASELINE.read_text(encoding="utf-8"))
        except Exception:                                    # noqa: BLE001
            return None
    return None


def save_baseline(entry: dict) -> None:
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    hist = []
    if BASELINE.exists():
        try:
            hist = json.loads(BASELINE.read_text(encoding="utf-8")).get("history", [])
        except Exception:                                    # noqa: BLE001
            hist = []
    hist = (hist + [entry])[-50:]
    BASELINE.write_text(json.dumps({**entry, "history": hist}, ensure_ascii=False,
                                   indent=2), encoding="utf-8")
    print(f"[coverage] 基线已更新 → {BASELINE}（{entry['overall_percent']}%）")


def compare(now: dict, base: dict | None) -> tuple[bool, float, str]:
    """(是否回归, 下降百分点, 说明)。没有基线 → 首次即基线，不算回归。"""
    if not base:
        return False, 0.0, "首次运行：作为基线"
    drop = float(base.get("overall_percent", 0.0)) - float(now.get("overall_percent", 0.0))
    if drop > THRESHOLD:
        return True, round(drop, 2), (f"覆盖率回归：{base.get('overall_percent')}% → "
                                      f"{now.get('overall_percent')}%"
                                      f"（-{drop:.2f} 个百分点，阈值 {THRESHOLD}）")
    return False, round(drop, 2), (f"覆盖率 {now.get('overall_percent')}%"
                                   f"（较基线 {drop:+.2f} 个百分点）")


def issue_body(now: dict, base: dict | None, drop: float) -> str:
    base_pct = base.get("overall_percent") if base else "-"
    base_sha = (base or {}).get("commit_sha") or "-"
    run_url = (f"{os.environ.get('GITHUB_SERVER_URL', '')}/"
               f"{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/"
               f"{os.environ.get('GITHUB_RUN_ID', '')}")
    return (f"## 覆盖率回归告警\n\n"
            f"- 当前：**{now.get('overall_percent')}%**（commit `{now.get('commit_sha') or '-'}`）\n"
            f"- 基线：{base_pct}%（commit `{base_sha}`）\n"
            f"- 下降：**{drop} 个百分点**（阈值 {THRESHOLD}）\n"
            f"- 时间：{now.get('generated_at')}\n"
            f"- 运行：{run_url}\n\n"
            f"面板会同步标红并记一条告警；数字人也会播报这条回归。\n"
            f"处置建议：先看新增文件的未覆盖行，补测试或回退本次改动。\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="覆盖率回归门禁")
    ap.add_argument("--measure", action="store_true", help="跑测试取真实覆盖率并写基线")
    ap.add_argument("--check", action="store_true", help="与基线比较；回归则 exit 1")
    ap.add_argument("--issue", action="store_true", help="回归时输出 issue 正文")
    ap.add_argument("--set", type=float, default=None, help="手工设基线百分比")
    ap.add_argument("--note", default="", help="备注（写进基线）")
    args = ap.parse_args(argv)

    if args.set is not None:
        save_baseline({"overall_percent": float(args.set), "commit_sha": _git_sha(),
                       "generated_at": _now(), "source": "manual", "note": args.note})
        return 0

    now = measure()
    if now is None:
        print("[coverage] 无法测量：门禁跳过（不静默判过，也不假报失败）")
        return 0
    if args.note:
        now["note"] = args.note
    base = load_baseline()
    regressed, drop, why = compare(now, base)
    print("[coverage]", why)
    if regressed:
        body = issue_body(now, base, drop)
        Path(ROOT / "state").mkdir(parents=True, exist_ok=True)
        (ROOT / "state" / "coverage_issue.md").write_text(body, encoding="utf-8")
        print("[coverage] issue 正文已写到 state/coverage_issue.md")
        print("::error title=覆盖率回归::" + why)
        if args.issue:
            print(body)
        save_baseline(now)                     # 记下这次（面板/语音可读）
        if args.check:
            return 1                           # ★CI job 判失败
    if args.measure or args.check:
        save_baseline(now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
