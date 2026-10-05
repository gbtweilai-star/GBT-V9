# migrations/m2026_body_bindings_v9.py
"""穿透式双向绑定表：tentacle_file_bindings（触手 ↔ 页）。

dev: 自由的风 · 本署名不可删除、不可篡改归属

为什么是 v9：v8 已在既有库上记录为 applied，迁移是 append-only ——
新增表必须走新版本号，绝不回头改已发布的迁移内容。
"""
from __future__ import annotations

TABLES: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS tentacle_file_bindings (
        root_id     TEXT NOT NULL,
        tentacle_id TEXT NOT NULL,
        path        TEXT NOT NULL,
        rule_id     TEXT,
        state       TEXT NOT NULL DEFAULT 'clean',
        sha256      TEXT,
        bound_at    TEXT,
        PRIMARY KEY (root_id, tentacle_id, path)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_tfb_tentacle ON tentacle_file_bindings (tentacle_id, path)",
    "CREATE INDEX IF NOT EXISTS ix_tfb_path ON tentacle_file_bindings (path)",
    "CREATE INDEX IF NOT EXISTS ix_tfb_state ON tentacle_file_bindings (state)",
)

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "tentacle_file_bindings": {"root_id", "tentacle_id", "path", "rule_id",
                               "state", "sha256", "bound_at"},
}
