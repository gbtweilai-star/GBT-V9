# body/coverage_watch.py
"""覆盖率回归告警桥接：持久状态、告警状态机与数字人语音。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
from typing import Any

from body.coverage_voice import coverage_voice_line


def _is_missing_table(exc: Exception, table: str) -> bool:
    message = str(exc).lower()
    return table.lower() in message and any(
        marker in message
        for marker in ("no such table", "does not exist", "undefined table"))


def _decode_state(value: Any, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _dialect(db: Any) -> str:
    return {"postgresql": "postgres"}.get(db.dialect, db.dialect)


async def _table_exists(db: Any, table: str) -> bool:
    dialect = _dialect(db)
    if dialect == "sqlite":
        row = await db.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,))
    elif dialect == "postgres":
        row = await db.fetch_one(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name=?", (table,))
    else:
        raise RuntimeError(f"unsupported DB dialect: {db.dialect!r}")
    return row is not None


async def coverage_status(db: Any) -> dict[str, Any]:
    """数字人只读工具：最新覆盖率 + 未确认回归证据。缺表不崩。"""
    result: dict[str, Any] = {"backends": [], "pending_regressions": [],
                              "table_missing": False}
    try:
        if not await _table_exists(db, "coverage_snapshots"):
            result["table_missing"] = True
            return result

        rows = await db.fetch_all(
            "SELECT backend,overall_percent,threshold,passed,commit_sha,run_url,"
            "generated_epoch FROM coverage_snapshots "
            "ORDER BY generated_epoch DESC,snapshot_id DESC")
        latest: dict[str, dict[str, Any]] = {}
        for raw in rows:
            row = dict(raw)
            latest.setdefault(str(row["backend"]), {
                "backend": row["backend"],
                "overall_percent": float(row["overall_percent"]),
                "threshold": float(row["threshold"]) if row.get("threshold") is not None else None,
                "passed": bool(row["passed"]),
                "commit_sha": row.get("commit_sha"),
                "run_url": row.get("run_url"),
                "generated_epoch": int(row["generated_epoch"]),
            })
        result["backends"] = list(latest.values())

        if not await _table_exists(db, "coverage_alerts"):
            result["table_missing"] = True
            return result

        alerts = await db.fetch_all(
            "SELECT alert_id,backend,commit_sha,previous_commit_sha,"
            "overall_percent,previous_percent,delta_percent,severity,run_url,"
            "created_epoch FROM coverage_alerts WHERE acknowledged=0 "
            "ORDER BY created_epoch DESC,alert_id DESC")
        result["pending_regressions"] = [{
            "alert_id": row["alert_id"],
            "backend": row["backend"],
            "commit_sha": row.get("commit_sha"),
            "previous_commit_sha": row.get("previous_commit_sha"),
            "overall_percent": float(row["overall_percent"]),
            "previous_percent": float(row["previous_percent"]),
            "delta_percent": float(row["delta_percent"]),
            "severity": row["severity"],
            "run_url": row.get("run_url"),
            "created_epoch": int(row["created_epoch"]),
        } for row in alerts]
    except Exception as exc:
        if (_is_missing_table(exc, "coverage_snapshots")
                or _is_missing_table(exc, "coverage_alerts")):
            result["table_missing"] = True
            return result
        raise
    return result


class CoverageAlertBridge:
    """把未确认的覆盖率回归同步进告警状态机与语音队列。"""

    def __init__(self, db: Any, alerts: Any = None, voice: Any = None, *,
                 cooldown_s: int = 300, speak_on_clear: bool = False,
                 watermark_key: str = "coverage_watch") -> None:
        if cooldown_s < 0:
            raise ValueError("cooldown_s must be >= 0")
        self.db = db
        self.alerts = alerts
        self.voice = voice
        self.cooldown_s = cooldown_s
        self.speak_on_clear = speak_on_clear
        self.watermark_key = watermark_key
        self._state_table_checked = False
        self._seen_kinds: set[str] = set()

    def _state_kind(self, suffix: str) -> str:
        return f"{self.watermark_key}:{suffix}"

    async def _ensure_state_table(self) -> None:
        if self._state_table_checked:
            return
        if not await _table_exists(self.db, "coverage_watch_state"):
            await self.db.execute(
                "CREATE TABLE IF NOT EXISTS coverage_watch_state "
                "(kind TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._state_table_checked = True

    async def _read_state(self, kind: str, default: Any) -> Any:
        row = await self.db.fetch_one(
            "SELECT value FROM coverage_watch_state WHERE kind=?", (kind,))
        return default if row is None else _decode_state(row["value"], default)

    async def _write_state(self, kind: str, value: Any) -> None:
        await self.db.execute(
            "INSERT INTO coverage_watch_state (kind,value) VALUES (?,?) "
            "ON CONFLICT(kind) DO UPDATE SET value=excluded.value",
            (kind, json.dumps(value, ensure_ascii=False, separators=(",", ":"))))

    @staticmethod
    def _kind(row: dict[str, Any]) -> str:
        return f"coverage:{row['alert_id']}:{row['backend']}"

    @staticmethod
    def _level(severity: str) -> str:
        if severity == "severe":
            return "critical"
        if severity == "regression":
            return "warning"
        raise ValueError(f"unsupported coverage alert severity: {severity!r}")

    async def poll(self) -> dict[str, int]:
        counts = {"raised": 0, "cleared": 0, "spoken": 0, "watermark": 0}

        async with self.db.transaction(immediate=(self.db.dialect == "sqlite")):
            await self.db.lock_key(self.watermark_key)     # 选举：只有一个 worker 发
            await self._ensure_state_table()

            watermark_kind = self._state_kind("watermark_epoch")
            cooldown_kind = self._state_kind("cooldowns")
            raised_kind = self._state_kind("raised_kinds")

            watermark = int(await self._read_state(watermark_kind, 0) or 0)
            cooldowns = await self._read_state(cooldown_kind, {})
            old_raised = set(await self._read_state(raised_kind, []))

            if not await _table_exists(self.db, "coverage_alerts"):
                counts["watermark"] = watermark
                return counts

            active_rows = await self.db.fetch_all(
                "SELECT alert_id,backend,commit_sha,previous_commit_sha,"
                "overall_percent,previous_percent,delta_percent,drop_threshold,"
                "severity,label,run_url,created_epoch "
                "FROM coverage_alerts WHERE acknowledged=0 "
                "ORDER BY created_epoch ASC,alert_id ASC")

            newer_rows = await self.db.fetch_all(
                "SELECT alert_id FROM coverage_alerts "
                "WHERE acknowledged=0 AND created_epoch>? "
                "ORDER BY created_epoch ASC,alert_id ASC", (watermark,))
            newer_ids = {str(row["alert_id"]) for row in newer_rows}

            current_kinds: set[str] = set()
            max_seen = watermark
            now = int(await self.db.db_now_epoch())

            for raw in active_rows:
                row = dict(raw)
                kind = self._kind(row)
                current_kinds.add(kind)
                created_epoch = int(row["created_epoch"])
                max_seen = max(max_seen, created_epoch)
                is_new = kind not in old_raised and kind not in self._seen_kinds

                if self.alerts is not None:
                    detail = dict(row)
                    detail["evidence_ref"] = {
                        "alert_id": row["alert_id"],
                        "commit_sha": row.get("commit_sha"),
                        "run_url": row.get("run_url"),
                    }
                    await self.alerts.raise_alert(
                        kind, self._level(str(row["severity"])), detail)

                is_recent = (str(row["alert_id"]) in newer_ids
                             or created_epoch == watermark)
                backend = str(row["backend"])
                last_spoken = int(cooldowns.get(backend, 0) or 0)
                if (is_new and is_recent and self.voice is not None
                        and now - last_spoken >= self.cooldown_s):
                    text, priority = coverage_voice_line(row)
                    await self.voice.enqueue(text, priority, "coverage_regression")
                    cooldowns[backend] = now
                    counts["spoken"] += 1

                if is_new:
                    counts["raised"] += 1
                self._seen_kinds.add(kind)

            stale_kinds = old_raised - current_kinds
            for kind in sorted(stale_kinds):
                if self.alerts is not None:
                    await self.alerts.clear_alert(kind)
                counts["cleared"] += 1
                if self.speak_on_clear and self.voice is not None:
                    await self.voice.enqueue(
                        "信息。覆盖率回归告警已解除。", 2,
                        "coverage_regression_cleared")

            await self._write_state(watermark_kind, max_seen)
            await self._write_state(cooldown_kind, cooldowns)
            await self._write_state(raised_kind, sorted(current_kinds))
            counts["watermark"] = max_seen

        return counts

    async def status(self) -> dict[str, Any]:
        return await coverage_status(self.db)
