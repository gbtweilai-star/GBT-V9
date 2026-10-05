# tools/accept_octop_offline.py —— 53 项离线能力验收执行器（按 manifest 原文逐条核）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：python tools/accept_octop_offline.py [--workdir DIR] [--json PATH]
# 判定：
#   native_probe  → 注册表内该能力的 probe() 必须 done=True
#   ledger_row    → 先跑 operation，再查 table 行数 >= min_count（真读数）
#   artifact      → 跑 operation，查 native_artifacts 记录的文件存在且 >= min_bytes
#   audit_gap     → synthetic_capture 的已知缺口数 == expected
#   api_response  → 在进程内用 ASGI 调 V9 面板接口，须 200
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit.ledger import Ledger  # noqa: E402
from skills.caps.operations import Ops, run_operation  # noqa: E402
from skills.caps.registry import build_caps_registry, MANIFEST  # noqa: E402

# 操作执行顺序：capture 必须在 restore/segment 之前，其余无关
OP_ORDER = [
    "workflow.dag_dry_run", "coder.fake_fix_loop", "actuator.fake_actions",
    "pulse.fake_dispatch", "ledger.roundtrip", "scan.full_sweep_probe",
    "scan.cross_review", "web.scrape_fixture", "voice.synthetic_transcript",
    "scheduler.fake_gpu_queue", "devour.synthetic_capture", "devour.print_restore_probe",
]


def _count(led: Ledger, table: str) -> int:
    with led._tx() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _artifact_ok(led: Ledger, operation: str, min_bytes: int) -> tuple[bool, str]:
    with led._tx() as conn:
        rows = conn.execute(
            "SELECT path, bytes FROM native_artifacts WHERE operation=? ORDER BY id DESC",
            (operation,)).fetchall()
    for path, size in rows:
        if Path(path).exists() and int(size) >= min_bytes:
            return True, f"{path} ({size}B)"
    return False, f"no artifact >= {min_bytes}B for {operation}"


async def _api_check(path: str) -> tuple[bool, str]:
    try:
        import httpx
        from panel.server import app  # noqa: WPS433
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://panel") as client:
            r = await client.get(path)
        return (r.status_code == 200), f"{path} -> {r.status_code}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{path} -> {type(exc).__name__}: {exc}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", default=None)
    ap.add_argument("--json", default=str(ROOT / "native-capabilities" / "acceptance-report.json"))
    args = ap.parse_args()

    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="v9-accept-"))
    workdir.mkdir(parents=True, exist_ok=True)
    led = Ledger(db=str(workdir / "acceptance-ledger.db"))
    ops = Ops(ledger=led, workdir=workdir)
    reg = build_caps_registry(ledger=led, workdir=str(workdir))

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    caps = manifest["capabilities"]
    results: list[dict] = []

    # ── 1) 注册覆盖 + probe ──
    probes = reg.probe_all()
    for cap in caps:
        cid = cap["id"]
        pr = probes.get(cid, {"done": False, "detail": "not registered"})
        results.append({"capability": cid, "kind": "native_probe",
                        "pass": bool(pr["done"]), "detail": pr["detail"]})

    # ── 2) 先跑全部 operation（按依赖顺序）──
    op_results = {}
    for name in OP_ORDER:
        try:
            op_results[name] = {"ok": True, "result": run_operation(ops, name)}
        except Exception as exc:  # noqa: BLE001
            op_results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ── 3) ledger_row / artifact / audit_gap / api_response ──
    api_cache: dict[str, tuple[bool, str]] = {}
    for cap in caps:
        cid = cap["id"]
        for acc in cap.get("acceptance", []):
            kind = acc.get("kind")
            if kind == "ledger_row":
                table, need = acc["table"], int(acc.get("min_count", 1))
                have = _count(led, table)
                results.append({"capability": cid, "kind": "ledger_row",
                                "pass": have >= need, "detail": f"{table}={have} (need >= {need})"})
            elif kind == "artifact":
                opname, minb = acc.get("operation", ""), int(acc.get("min_bytes", 1))
                good, detail = _artifact_ok(led, opname, minb)
                results.append({"capability": cid, "kind": "artifact",
                                "pass": good, "detail": detail})
            elif kind == "audit_gap":
                expected = int(acc.get("expected", 1))
                got = ops.audit_gap_count()
                results.append({"capability": cid, "kind": "audit_gap",
                                "pass": got == expected, "detail": f"gaps={got} (expect {expected})"})
            elif kind == "api_response":
                path = str(acc.get("path", ""))
                if path not in api_cache:
                    api_cache[path] = asyncio.run(_api_check(path))
                good, detail = api_cache[path]
                results.append({"capability": cid, "kind": "api_response",
                                "pass": good, "detail": detail})

    # ── 4) 汇总 ──
    probes_pass = sum(1 for r in results if r["kind"] == "native_probe" and r["pass"])
    ops_pass = sum(1 for v in op_results.values() if v["ok"])
    other = [r for r in results if r["kind"] != "native_probe"]
    other_pass = sum(1 for r in other if r["pass"])

    report = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "manifest": str(MANIFEST),
        "capabilities": len(caps),
        "probe_pass": f"{probes_pass}/{len(caps)}",
        "operations_pass": f"{ops_pass}/{len(OP_ORDER)}",
        "acceptance_pass": f"{other_pass}/{len(other)}",
        "all_pass": probes_pass == len(caps) and ops_pass == len(OP_ORDER) and other_pass == len(other),
        "workdir": str(workdir),
        "ledger_rows": {t: _count(led, t) for t in
                        ("workflow_runs", "agent_runs", "cross_scan_results", "scan_coverage",
                         "web_scrape_runs", "voice_events", "media_job_events", "native_artifacts")},
        "failures": [r for r in results if not r["pass"]] + [{"kind": "operation", **v}
                                                             for v in op_results.values() if not v["ok"]],
        "results": results,
    }
    Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"== 能力验收（{report['generated_at']}）==")
    print(f"  probe      : {report['probe_pass']}")
    print(f"  operations : {report['operations_pass']}")
    print(f"  其它验收   : {report['acceptance_pass']}")
    print(f"  账本行     : {report['ledger_rows']}")
    print(f"  结果       : {'ALL PASS' if report['all_pass'] else 'FAIL'}")
    for f in report["failures"][:10]:
        print(f"   ! {f}")
    print(f"  报告 -> {args.json}")
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
