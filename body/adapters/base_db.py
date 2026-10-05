"""共享 DB 适配器基类。
铁律：占位符只转换 SQL 参数标记，不拼接参数值。
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from typing import Any


class _BaseDb:
    def __init__(self, dialect: str) -> None:
        self.dialect = dialect
        self._current_connection: ContextVar[Any | None] = ContextVar(
            f"{type(self).__name__}_{id(self)}_connection", default=None,
        )
        self._closed = False

    def bind(self, position: int) -> str:
        """返回 1-based 的方言占位符。"""
        if position < 1:
            raise ValueError("bind position must be >= 1")
        return "?" if self.dialect == "sqlite" else f"${position}"

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError(f"{type(self).__name__} is closed")

    def _set_transaction_connection(self, connection: Any) -> Token[Any | None]:
        return self._current_connection.set(connection)

    def _reset_transaction_connection(self, token: Token[Any | None]) -> None:
        self._current_connection.reset(token)

    def _require_transaction(self) -> Any:
        connection = self._current_connection.get()
        if connection is None:
            raise RuntimeError("operation requires an active db.transaction()")
        return connection

    @staticmethod
    def _dollar_quote_start(sql: str, index: int) -> str | None:
        """识别 PostgreSQL $$...$$ 或 $tag$...$tag$ 字符串起始符。"""
        if sql[index] != "$":
            return None
        end = index + 1
        if end < len(sql) and sql[end] == "$":
            return "$$"
        if end >= len(sql) or not (sql[end].isalpha() or sql[end] == "_"):
            return None
        end += 1
        while end < len(sql) and (sql[end].isalnum() or sql[end] == "_"):
            end += 1
        if end < len(sql) and sql[end] == "$":
            return sql[index:end + 1]
        return None

    @classmethod
    def _translate_qmarks(cls, sql: str, param_count: int) -> str:
        """将引号/注释之外的 ? 转成 PostgreSQL 的 $1..$n。

        支持单/双引号、SQL 双引号转义、行注释、嵌套块注释和 dollar-quoted
        字符串。限制：PostgreSQL JSONB 的 ? 运算符也会被视为占位符。
        """
        out: list[str] = []
        i, n = 0, 0
        state = "normal"
        block_depth = 0
        dollar_end: str | None = None

        while i < len(sql):
            if state == "normal":
                if sql.startswith("--", i):
                    out.append("--"); i += 2; state = "line_comment"; continue
                if sql.startswith("/*", i):
                    out.append("/*"); i += 2; state = "block_comment"; block_depth = 1; continue
                if sql[i] == "'":
                    out.append(sql[i]); i += 1; state = "single_quote"; continue
                if sql[i] == '"':
                    out.append(sql[i]); i += 1; state = "double_quote"; continue
                if sql[i] == "$":
                    delimiter = cls._dollar_quote_start(sql, i)
                    if delimiter is not None:
                        out.append(delimiter); i += len(delimiter)
                        state = "dollar_quote"; dollar_end = delimiter; continue
                if sql[i] == "?":
                    n += 1; out.append(f"${n}"); i += 1; continue
                out.append(sql[i]); i += 1; continue

            if state == "single_quote":
                out.append(sql[i])
                if sql[i] == "'" and i + 1 < len(sql) and sql[i + 1] == "'":
                    out.append(sql[i + 1]); i += 2
                elif sql[i] == "'":
                    i += 1; state = "normal"
                else:
                    i += 1
                continue

            if state == "double_quote":
                out.append(sql[i])
                if sql[i] == '"' and i + 1 < len(sql) and sql[i + 1] == '"':
                    out.append(sql[i + 1]); i += 2
                elif sql[i] == '"':
                    i += 1; state = "normal"
                else:
                    i += 1
                continue

            if state == "line_comment":
                out.append(sql[i])
                if sql[i] == "\n":
                    state = "normal"
                i += 1
                continue

            if state == "block_comment":
                if sql.startswith("/*", i):
                    out.append("/*"); block_depth += 1; i += 2
                elif sql.startswith("*/", i):
                    out.append("*/"); block_depth -= 1; i += 2
                    if block_depth == 0:
                        state = "normal"
                else:
                    out.append(sql[i]); i += 1
                continue

            if state == "dollar_quote":
                assert dollar_end is not None
                if sql.startswith(dollar_end, i):
                    out.append(dollar_end); i += len(dollar_end)
                    state = "normal"; dollar_end = None
                else:
                    out.append(sql[i]); i += 1

        if n != param_count:
            raise ValueError(
                f"SQL placeholder count ({n}) does not match parameter count ({param_count})"
            )
        return "".join(out)

    # ── 只读快照（复核用）：SQLite 为一致性读意图，PG 为只读事务 ──
    @asynccontextmanager
    async def read_snapshot(self):
        """yield 一个只读视图供 verify_chain 批量读。

        诚实标注：SQLite 没有 MVCC 快照，这里表达的是"一致性读意图"
        （每语句独立连接 + WAL，读到的是提交态）；PG 应使用只读事务/REPEATABLE READ。
        调用方只允许 fetch_* 读方法，不得在快照里写。
        """
        yield self

    # ── body 契约：告警与卡点日志（witness_probe / producers / diag 都在用）──
    # 纪律：告警在任何状态下都必须能落（链封冻也要能报出来），因此不看 chain_frozen。
    async def record_alert(self, kind: str, payload: dict | None = None,
                           level: str = "warning", *, bypass_freeze: bool = False) -> str:
        import json as _json
        import uuid as _uuid
        from datetime import datetime, timezone
        rid = _uuid.uuid4().hex[:16]
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self.execute(
            "INSERT INTO body_alerts (id, at, kind, level, payload_json, acknowledged) "
            "VALUES (?,?,?,?,?,0)",
            (rid, at, kind, level,
             _json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)))
        return rid

    async def record_blocked(self, kind: str, payload: dict | None = None) -> str:
        import json as _json
        import uuid as _uuid
        from datetime import datetime, timezone
        rid = _uuid.uuid4().hex[:16]
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        await self.execute(
            "INSERT INTO body_blocked_log (id, at, kind, payload_json) VALUES (?,?,?,?)",
            (rid, at, kind,
             _json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)))
        return rid

    async def alerts(self, kind: str | None = None, level: str | None = None):
        rows = await self.fetch_all(
            "SELECT id, at, kind, level, payload_json, acknowledged FROM body_alerts "
            "ORDER BY at ASC, id ASC")
        out = []
        for r in rows:
            if kind and r["kind"] != kind:
                continue
            if level and r["level"] != level:
                continue
            out.append(dict(r))
        return out

    async def blocked(self, kind: str | None = None):
        rows = await self.fetch_all(
            "SELECT id, at, kind, payload_json FROM body_blocked_log "
            "ORDER BY at ASC, id ASC")
        return [dict(r) for r in rows if not kind or r["kind"] == kind]

    @staticmethod
    def _command_rowcount(status: str) -> int:
        """解析 asyncpg 的 INSERT/UPDATE/DELETE command status。"""
        try:
            return int(status.rsplit(" ", 1)[-1])
        except (ValueError, AttributeError):
            return 0
