# migrations/m2026_witness_runtime_v11.py —— 见证运行期列（身份缓存/凭据域指纹/代际）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么是 v11：v10 已在既有库记录为 applied，迁移 append-only；
# body.witness_alerts.apply_identity 需要写这些列，但 v8 建表时没有 —— 用字面 ALTER 补齐。
# 纪律：account_domain_fp 只存 HMAC 指纹（不存账户 ID 原文）；配置代际变化必须能让
#       "配置类告警"重新开单，故 config_generation 单独一列。
from __future__ import annotations

TABLES: tuple[str, ...] = (
    "ALTER TABLE body_witness_status ADD COLUMN identity_observed_at TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN identity_expires_at TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN account_domain_fp TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN config_generation TEXT",
    "ALTER TABLE body_witness_status ADD COLUMN last_seen_at TEXT",
    "CREATE INDEX IF NOT EXISTS ix_bws_domain_fp ON body_witness_status (account_domain_fp)",
)

REQUIRED_COLUMNS: dict[str, set[str]] = {
    "body_witness_status": {"identity_observed_at", "identity_expires_at",
                            "account_domain_fp", "config_generation", "last_seen_at"},
}
