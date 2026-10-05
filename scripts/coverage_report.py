#!/usr/bin/env python3
"""汇总 coverage.py JSON；可选写快照并清理过期快照。

asyncpg >= 0.18.0 支持 DSN 查询参数 sslmode=require；本脚本用
asyncpg.connect(dsn)，原样传递该参数。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import argparse, asyncio, json, os, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def dsn_is_supported(dsn: str) -> bool:
    try:
        parsed = urlsplit(dsn)
        return parsed.scheme in {"postgres", "postgresql"} and bool(parsed.hostname)
    except ValueError:
        return False


def module_for(path: str) -> str:
    parts = Path(path.replace("\\", "/")).parts
    roots = {"body", "migrations", "panel", "skills"}
    for index, part in enumerate(parts):
        if part in roots:
            rest = parts[index + 1:]
            if rest and not rest[0].endswith(".py"):
                return f"{part}/{rest[0]}"
            return part
    return "/".join(parts[:2]) if len(parts) > 1 else (parts[0] if parts else "unknown")


def load_summary(path: Path, backend: str, threshold: float,
                 label: str | None) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    lines_total = int(totals.get("num_statements", 0))
    lines_covered = int(totals.get("covered_lines", 0))
    overall = float(totals.get(
        "percent_covered",
        100.0 if lines_total == 0 else 100.0 * lines_covered / lines_total))

    grouped: dict[str, dict[str, Any]] = {}
    for filename, item in data.get("files", {}).items():
        stats = item.get("summary", {})
        total = int(stats.get("num_statements", 0))
        covered = int(stats.get("covered_lines", 0))
        module = module_for(filename)
        group = grouped.setdefault(module, {
            "module": module, "lines_total": 0, "lines_covered": 0, "files": []})
        group["lines_total"] += total
        group["lines_covered"] += covered
        group["files"].append({
            "path": filename,
            "percent": float(stats.get("percent_covered",
                              100.0 if total == 0 else 100.0 * covered / total)),
            "lines_total": total, "lines_covered": covered,
            "missing_lines": item.get("missing_lines", []),
        })

    for package in ("body", "migrations", "panel", "skills"):
        grouped.setdefault(package, {"module": package, "lines_total": 0,
                                     "lines_covered": 0, "files": []})

    modules = []
    for group in grouped.values():
        total, covered = group["lines_total"], group["lines_covered"]
        group["percent"] = round(100.0 if total == 0 else 100.0 * covered / total, 1)
        group["files"].sort(key=lambda item: item["path"])
        modules.append(group)
    modules.sort(key=lambda item: item["module"])

    generated_epoch = int(time.time())
    return {
        "schema_version": 1, "backend": backend,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_epoch": generated_epoch,
        "threshold": threshold, "passed": overall >= threshold,
        "overall_percent": round(overall, 1),
        "lines_total": lines_total, "lines_covered": lines_covered,
        "label": label, "modules": modules,
        "below_threshold": [m["module"] for m in modules if m["percent"] < threshold],
    }


async def persist(dsn: str, summary: dict[str, Any], *, prune_days: int) -> int:
    import asyncpg

    if not dsn_is_supported(dsn):
        raise ValueError("unsupported PostgreSQL DSN scheme or host")

    modules_json = json.dumps(summary["modules"], ensure_ascii=False, separators=(",", ":"))
    files = [f for m in summary["modules"] for f in m["files"]]
    files_json = json.dumps(files, ensure_ascii=False, separators=(",", ":"))

    conn = await asyncpg.connect(dsn)
    try:
        async with conn.transaction():
            # label 列可能不存在 -> 自适应，不带它也能写
            label_exists = await conn.fetchrow(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema=current_schema() "
                "AND table_name='coverage_snapshots' AND column_name='label'")
            columns = ["snapshot_id", "backend", "generated_epoch", "overall_percent",
                       "modules_json", "files_json", "lines_total", "lines_covered",
                       "threshold", "passed", "commit_sha", "run_url"]
            values: list[Any] = [
                uuid.uuid4().hex, summary["backend"], summary["generated_epoch"],
                summary["overall_percent"], modules_json, files_json,
                summary["lines_total"], summary["lines_covered"],
                summary["threshold"], int(summary["passed"]),
                os.getenv("GITHUB_SHA"), os.getenv("GITHUB_RUN_URL")]
            if label_exists is not None:
                columns.append("label"); values.append(summary.get("label"))

            placeholders = ",".join(f"${i}" for i in range(1, len(values) + 1))
            await conn.execute(
                f"INSERT INTO coverage_snapshots ({','.join(columns)}) "
                f"VALUES ({placeholders})", *values)

            deleted = 0
            if prune_days > 0:
                cutoff = int(time.time()) - prune_days * 86400
                result = await conn.execute(
                    "DELETE FROM coverage_snapshots WHERE generated_epoch < $1", cutoff)
                deleted = int(result.rsplit(" ", 1)[-1])
        return deleted
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, required=True)
    parser.add_argument("--backend", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--threshold", type=float,
                        default=float(os.getenv("COVERAGE_THRESHOLD", "80")))
    parser.add_argument("--db", help="可选：持久 PostgreSQL DSN")
    parser.add_argument("--prune-days", type=int, default=90, help="0 关闭清理")
    parser.add_argument("--label", help="可选：分支/运行时标签")
    args = parser.parse_args()

    if args.prune_days < 0:
        parser.error("--prune-days must be >= 0 (0 disables pruning)")
    if not 0 <= args.threshold <= 100:
        parser.error("--threshold must be in range 0..100")
    if not args.coverage.is_file():
        parser.error(f"coverage JSON not found: {args.coverage}")

    summary = load_summary(args.coverage, args.backend, args.threshold, args.label)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    print(f"{summary['backend']}: {summary['overall_percent']:.1f}% "
          f"(threshold {summary['threshold']:.1f}%)")
    for module in summary["modules"]:
        print(f"  {module['module']}: {module['percent']:.1f}%")
    print(f"Summary: {args.out}")

    if args.db:
        deleted = asyncio.run(persist(args.db, summary, prune_days=args.prune_days))
        print(f"Snapshot saved; pruned {deleted} old rows.")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
