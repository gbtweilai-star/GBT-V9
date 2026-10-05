# migrations/m2026_witness_tools_v10.py
"""见证快照/语音 outbox/只读工具审计：witness_snapshot、witness_voice_outbox、
body_read_snapshots、read_tool_audit，并给 body_witness_status 补身份维度列。

dev: 自由的风 · 本署名不可删除、不可篡改归属

为什么是 v10：v8 已在既有库记录为 applied，迁移 append-only；
身份维度列（identity_status / evidence_level / content_status / vote_eligible /
identity_error_code）用**字面 ALTER** 补齐 —— 不在 v8 里改表定义，避免"新库重复加列"。
"""
from __future__ import annotations

TABLES: tuple[str, ...] = (
    # ── 身份维度列（witness_voice.reconcile 与见证卡片都要读）──
    "ALTER TABLE body_witness_status ADD COLUMN identity_status TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN evidence_level TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN content_status TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN vote_eligible INTEGER",
    "ALTER TABLE body_witness_status ADD COLUMN identity_error_code TEXT",

    # ── 见证快照（单行；数字只从这里取，工具与卡片共用）──
    """CREATE TABLE IF NOT EXISTS witness_snapshot (
        id           INTEGER PRIMARY KEY,
        revision     INTEGER NOT NULL,
        valid_count  INTEGER NOT NULL,
        required     INTEGER NOT NULL,
        status       TEXT NOT NULL,
        states_json  TEXT NOT NULL,
        observed_at  TEXT NOT NULL
    )""",

    # ── 见证语音 outbox（跳变才开口；失败留文本不假报已播）──
    """CREATE TABLE IF NOT EXISTS witness_voice_outbox (
        event_id         TEXT PRIMARY KEY,
        revision         INTEGER,
        transition_id    TEXT UNIQUE,
        kind             TEXT NOT NULL,
        priority         INTEGER NOT NULL,
        witness_ids_json TEXT,
        reason_code      TEXT,
        valid_count      INTEGER,
        required         INTEGER,
        text             TEXT NOT NULL,
        state            TEXT NOT NULL DEFAULT 'pending',
        created_at       TEXT NOT NULL,
        updated_at       TEXT,
        error            TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_wvo_state_pri ON witness_voice_outbox (state, priority, created_at)",

    # ── 只读工具快照（每域一行；工具不重算，只转述）──
    """CREATE TABLE IF NOT EXISTS body_read_snapshots (
        domain          TEXT PRIMARY KEY,
        revision        INTEGER NOT NULL DEFAULT 1,
        observed_at     TEXT NOT NULL,
        sample_period_s REAL NOT NULL DEFAULT 60,
        payload_json    TEXT NOT NULL,
        evidence_json   TEXT NOT NULL DEFAULT '[]'
    )""",

    # ── 工具调用审计（每次调用必落一行）──
    """CREATE TABLE IF NOT EXISTS read_tool_audit (
        call_id        TEXT PRIMARY KEY,
        session_id     TEXT,
        tool           TEXT NOT NULL,
        domain         TEXT,
        params_digest  TEXT,
        revision       INTEGER,
        observed_at    TEXT,
        stale          INTEGER,
        facts_digest   TEXT,
        evidence_json  TEXT,
        spoken_text    TEXT,
        ok             INTEGER,
        error_code     TEXT,
        duration_ms    INTEGER,
        at             TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_rta_tool_at ON read_tool_audit (tool, at DESC)",
)

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "witness_snapshot": {"id", "revision", "valid_count", "required", "status",
                         "states_json", "observed_at"},
    "witness_voice_outbox": {"event_id", "transition_id", "kind", "priority",
                             "text", "state", "created_at", "updated_at"},
    "body_read_snapshots": {"domain", "revision", "observed_at", "sample_period_s",
                            "payload_json", "evidence_json"},
    "read_tool_audit": {"call_id", "session_id", "tool", "domain", "ok",
                        "error_code", "duration_ms", "at"},
    "body_witness_status": {"identity_status", "evidence_level", "content_status",
                            "vote_eligible", "identity_error_code"},
}
