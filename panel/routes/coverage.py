"""覆盖率快照 API；SQL 用 ? 占位符，由 PostgresDb 转成 $n。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from panel.deps import db

router = APIRouter()
_JSON_COLUMNS = ("modules_json", "files_json")


def _decode(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return fallback


def _row(raw: Any) -> dict[str, Any]:
    result = dict(raw)
    for key in _JSON_COLUMNS:
        if key in result:
            result[key] = _decode(result[key], [])
    return result


def _missing_table(exc: Exception) -> bool:
    text = str(exc).lower()
    return "coverage_snapshots" in text and any(
        marker in text
        for marker in ("no such table", "does not exist", "undefined table", "not found"))


_label_cache: tuple[int, str, bool] | None = None


async def _has_label_column() -> bool:
    """检测一次可选 label 列；同一进程同一 DB 适配器复用结果。"""
    global _label_cache
    key = (id(db), str(getattr(db, "dialect", "")))
    if _label_cache is not None and _label_cache[:2] == key:
        return _label_cache[2]

    if db.dialect == "sqlite":
        columns = await db.fetch_all("PRAGMA table_info(coverage_snapshots)")
        exists = any(r["name"] == "label" for r in columns)
    else:
        row = await db.fetch_one(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=current_schema() "
            "AND table_name=? AND column_name=?",
            ("coverage_snapshots", "label"))
        exists = row is not None

    _label_cache = (key[0], key[1], exists)
    return exists


def _where(*, backend: str | None = None, label: str | None = None,
           commit_sha: str | None = None,
           has_label: bool) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []

    if backend:
        clauses.append("backend=?"); params.append(backend)
    if label:
        if has_label:
            clauses.append("label=?"); params.append(label)
        else:
            clauses.append("1=0")          # 无 label 列时筛选应返回空，而非忽略筛选
    if commit_sha:
        clauses.append("commit_sha=?"); params.append(commit_sha)

    return ("WHERE " + " AND ".join(clauses) if clauses else "", tuple(params))


async def _commit_summaries(limit: int) -> list[dict[str, Any]]:
    """聚合 SQL 双后端兼容；backends/run_url 在 Python 里补，避开方言函数。"""
    aggregates = await db.fetch_all(
        "SELECT commit_sha, MAX(generated_epoch) AS generated_epoch, "
        "MIN(overall_percent) AS min_overall_percent, "
        "SUM(CASE WHEN passed=0 THEN 1 ELSE 0 END) AS failed_count "
        "FROM coverage_snapshots WHERE commit_sha IS NOT NULL "
        "GROUP BY commit_sha "
        "ORDER BY generated_epoch DESC, commit_sha DESC LIMIT ?",
        (limit,))
    if not aggregates:
        return []

    commits = [str(r["commit_sha"]) for r in aggregates]
    marks = ",".join("?" for _ in commits)
    snapshots = await db.fetch_all(
        "SELECT commit_sha, backend, generated_epoch, run_url "
        f"FROM coverage_snapshots WHERE commit_sha IN ({marks}) "
        "ORDER BY generated_epoch DESC, snapshot_id DESC",
        tuple(commits))

    by_commit: dict[str, dict[str, Any]] = {}
    for snap in snapshots:
        sha = str(snap["commit_sha"])
        item = by_commit.setdefault(sha, {"backends": [], "run_url": None,
                                          "generated_epoch": 0})
        name = str(snap["backend"])
        if name not in item["backends"]:
            item["backends"].append(name)
        epoch = int(snap["generated_epoch"])
        if epoch >= item["generated_epoch"]:
            item["generated_epoch"] = epoch
            item["run_url"] = snap.get("run_url")

    result = []
    for agg in aggregates:
        sha = str(agg["commit_sha"])
        extra = by_commit.get(sha, {})
        result.append({
            "commit_sha": sha,
            "run_url": extra.get("run_url"),
            "generated_epoch": int(agg["generated_epoch"]),
            "backends": sorted(extra.get("backends", [])),
            "min_overall_percent": float(agg["min_overall_percent"]),
            "all_passed": int(agg["failed_count"] or 0) == 0,
        })
    return result


@router.get("/coverage/facets")
async def coverage_facets(limit: int = Query(20, ge=1, le=100)):
    try:
        has_label = await _has_label_column()
        backends = await db.fetch_all(
            "SELECT DISTINCT backend FROM coverage_snapshots ORDER BY backend")
        labels = []
        if has_label:
            labels = await db.fetch_all(
                "SELECT DISTINCT label FROM coverage_snapshots "
                "WHERE label IS NOT NULL AND label<>'' ORDER BY label")
        return {
            "backends": [r["backend"] for r in backends],
            "labels": [r["label"] for r in labels] if has_label else [],
            "recent_commits": await _commit_summaries(limit),
            "table_missing": False,
            "label_available": has_label,
        }
    except Exception as exc:
        if _missing_table(exc):
            return {"backends": [], "labels": [], "recent_commits": [],
                    "table_missing": True, "label_available": False}
        raise


@router.get("/coverage/commits")
async def coverage_commits(limit: int = Query(50, ge=1, le=200)):
    try:
        return {"items": await _commit_summaries(limit), "table_missing": False}
    except Exception as exc:
        if _missing_table(exc):
            return {"items": [], "table_missing": True}
        raise


@router.get("/coverage/commit/{commit_sha}")
async def coverage_commit_detail(commit_sha: str):
    try:
        rows = await db.fetch_all(
            "SELECT * FROM coverage_snapshots WHERE commit_sha=? "
            "ORDER BY backend, generated_epoch DESC, snapshot_id DESC",
            (commit_sha,))
    except Exception as exc:
        if _missing_table(exc):
            return {"table_missing": True, "items": [], "rollup": None}
        raise

    if not rows:
        raise HTTPException(404, detail="coverage_commit_not_found")

    items = [_row(r) for r in rows]
    latest = max(items, key=lambda r: int(r["generated_epoch"]))
    return {
        "items": items,
        "rollup": {
            "commit_sha": commit_sha,
            "backends": sorted({str(r["backend"]) for r in items}),
            "min_overall_percent": min(float(r["overall_percent"]) for r in items),
            "all_passed": all(bool(r["passed"]) for r in items),
            "run_url": latest.get("run_url"),
            "generated_epoch": max(int(r["generated_epoch"]) for r in items),
        },
        "table_missing": False,
    }


@router.get("/coverage/series")
async def coverage_series(backend: str | None = None, label: str | None = None,
                          commit_sha: str | None = None,
                          limit: int = Query(60, ge=1, le=500)):
    try:
        has_label = await _has_label_column()
        where, params = _where(backend=backend, label=label,
                               commit_sha=commit_sha, has_label=has_label)
        rows = await db.fetch_all(
            "SELECT backend, generated_epoch, overall_percent, passed, "
            "commit_sha, run_url "
            f"FROM coverage_snapshots {where} "
            "ORDER BY generated_epoch DESC, snapshot_id DESC LIMIT ?",
            params + (limit,))
        rows.reverse()
        return {"items": [dict(r) for r in rows], "table_missing": False}
    except Exception as exc:
        if _missing_table(exc):
            return {"items": [], "table_missing": True}
        raise


@router.get("/coverage")
async def coverage_overview(backend: str | None = None, label: str | None = None,
                            commit_sha: str | None = None,
                            limit: int = Query(20, ge=1, le=200)):
    try:
        has_label = await _has_label_column()
        where, params = _where(backend=backend, label=label,
                               commit_sha=commit_sha, has_label=has_label)
        rows = await db.fetch_all(
            "SELECT * FROM coverage_snapshots "
            f"{where} ORDER BY generated_epoch DESC, snapshot_id DESC", params)

        latest_by_backend: dict[str, Any] = {}
        for row in rows:
            latest_by_backend.setdefault(str(row["backend"]), row)

        return {
            "items": [_row(r) for r in latest_by_backend.values()],
            "recent": [_row(r) for r in rows[:limit]],
            "table_missing": False,
            "label_available": has_label,
        }
    except Exception as exc:
        if _missing_table(exc):
            return {"items": [], "recent": [], "table_missing": True,
                    "label_available": False}
        raise


# ★ 兜底路由必须放最后：FastAPI 按定义顺序匹配。
@router.get("/coverage/{snapshot_id}")
async def coverage_detail(snapshot_id: str):
    try:
        row = await db.fetch_one(
            "SELECT * FROM coverage_snapshots WHERE snapshot_id=?", (snapshot_id,))
    except Exception as exc:
        if _missing_table(exc):
            return {"table_missing": True}
        raise

    if row is None:
        raise HTTPException(404, detail="coverage_snapshot_not_found")

    item = _row(row)
    files = item.get("files_json", [])
    threshold = float(item.get("threshold") or 0)
    item["files_below_threshold"] = [
        f for f in files
        if float(f.get("percent", 0)) < threshold
    ] if isinstance(files, list) else []
    return item
