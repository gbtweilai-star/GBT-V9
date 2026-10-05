"""帧证据库表 DDL。
dev: 自由的风 · 本署名不可删除、不可篡改归属

表与索引语句使用 SQLite 与 PostgreSQL 共同接受的 SQL。
"""
from __future__ import annotations

FRAME_EVIDENCE_VERSION = 1
FRAME_EVIDENCE_NAME = "frame_evidence_v1"

TABLES: list[tuple[str, str]] = [
    (
        "artifact_objects",
        """
        CREATE TABLE IF NOT EXISTS artifact_objects (
            ref TEXT PRIMARY KEY NOT NULL,
            state TEXT NOT NULL
                CHECK (state IN ('staged', 'ready', 'deleting', 'deleted')),
            bytes BIGINT NOT NULL DEFAULT 0,
            created_epoch BIGINT NOT NULL,
            last_access_epoch BIGINT NOT NULL,
            staged_by TEXT,
            staged_until_epoch BIGINT NOT NULL DEFAULT 0
        )
        """,
    ),
    (
        "frame_leases",
        """
        CREATE TABLE IF NOT EXISTS frame_leases (
            lease_id TEXT PRIMARY KEY NOT NULL,
            ref TEXT NOT NULL,
            holder TEXT NOT NULL,
            purpose TEXT NOT NULL,
            acquired_epoch BIGINT NOT NULL,
            expires_epoch BIGINT NOT NULL,
            renew_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL
                CHECK (status IN ('active', 'released', 'expired'))
        )
        """,
    ),
    (
        "frame_verification_evidence",
        """
        CREATE TABLE IF NOT EXISTS frame_verification_evidence (
            id TEXT PRIMARY KEY NOT NULL,
            ref TEXT,
            baseline_ref TEXT,
            result_ref TEXT,
            raw_available INTEGER NOT NULL DEFAULT 1
                CHECK (raw_available IN (0, 1)),
            verification_times TEXT NOT NULL DEFAULT '[]',
            color_metrics_delta TEXT,
            effect_metrics TEXT,
            delta_metrics TEXT,
            metrics TEXT,
            verdict_and_spec TEXT,
            created_epoch BIGINT NOT NULL,
            calibration_key TEXT,
            calibration_fingerprint TEXT,
            fingerprint TEXT
        )
        """,
    ),
    (
        "coverage_snapshots",
        """
        CREATE TABLE IF NOT EXISTS coverage_snapshots (
            snapshot_id TEXT PRIMARY KEY NOT NULL,
            backend TEXT NOT NULL,
            generated_epoch BIGINT NOT NULL,
            overall_percent REAL NOT NULL,
            modules_json TEXT NOT NULL,
            files_json TEXT,
            lines_total BIGINT,
            lines_covered BIGINT,
            threshold REAL,
            passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
            commit_sha TEXT,
            run_url TEXT,
            label TEXT
        )
        """,
    ),
    (
        "coverage_alerts",
        """
        CREATE TABLE IF NOT EXISTS coverage_alerts (
            alert_id TEXT PRIMARY KEY NOT NULL,
            backend TEXT NOT NULL,
            commit_sha TEXT,
            previous_commit_sha TEXT,
            overall_percent REAL NOT NULL,
            previous_percent REAL NOT NULL,
            delta_percent REAL NOT NULL,
            drop_threshold REAL NOT NULL,
            severity TEXT NOT NULL CHECK (severity IN ('regression', 'severe')),
            label TEXT,
            run_url TEXT,
            created_epoch BIGINT NOT NULL,
            acknowledged INTEGER NOT NULL DEFAULT 0 CHECK (acknowledged IN (0, 1))
        )
        """,
    ),
    (
        "devour_calibrations",
        """
        CREATE TABLE IF NOT EXISTS devour_calibrations (
          project_id          TEXT NOT NULL,
          op_kind             TEXT NOT NULL,
          algorithm_version   TEXT NOT NULL,
          encode_fingerprint  TEXT NOT NULL,
          noise_threshold     REAL NOT NULL,
          effect_floor        REAL NOT NULL,
          outside_max         REAL NOT NULL,
          local_ratio         REAL NOT NULL,
          sample_count        INTEGER NOT NULL,
          delta_e_p50         REAL NOT NULL,
          delta_e_p99         REAL NOT NULL,
          noise_fraction_p99  REAL NOT NULL,
          policy_version      TEXT NOT NULL,
          created_at BIGINT NOT NULL,
          updated_at BIGINT NOT NULL,
          expires_at BIGINT NOT NULL,
          PRIMARY KEY (project_id, op_kind, algorithm_version, encode_fingerprint)
        )
        """,
    ),
    (
        "voice_state",
        """
        CREATE TABLE IF NOT EXISTS voice_state (
            kind TEXT PRIMARY KEY NOT NULL,
            value TEXT NOT NULL,
            updated_epoch BIGINT NOT NULL
        )
        """,
    ),
]

INDEXES: list[tuple[str, str]] = [
    # 水位候选：state='ready' ORDER BY last_access_epoch ASC
    ("idx_artifact_objects_state_last_access",
     "CREATE INDEX IF NOT EXISTS idx_artifact_objects_state_last_access "
     "ON artifact_objects (state, last_access_epoch)"),
    # staging 回收：state='staged' AND staged_until_epoch < now
    ("idx_artifact_objects_state_staged_until",
     "CREATE INDEX IF NOT EXISTS idx_artifact_objects_state_staged_until "
     "ON artifact_objects (state, staged_until_epoch)"),
    # pin 检查：ref + status='active' + expires_epoch
    ("idx_frame_leases_ref_status_expires",
     "CREATE INDEX IF NOT EXISTS idx_frame_leases_ref_status_expires "
     "ON frame_leases (ref, status, expires_epoch)"),
    # 过期租约清理：status + expires_epoch
    ("idx_frame_leases_status_expires",
     "CREATE INDEX IF NOT EXISTS idx_frame_leases_status_expires "
     "ON frame_leases (status, expires_epoch)"),
    ("idx_frame_verification_evidence_ref",
     "CREATE INDEX IF NOT EXISTS idx_frame_verification_evidence_ref "
     "ON frame_verification_evidence (ref)"),
    ("idx_frame_verification_evidence_baseline_ref",
     "CREATE INDEX IF NOT EXISTS idx_frame_verification_evidence_baseline_ref "
     "ON frame_verification_evidence (baseline_ref)"),
    ("idx_frame_verification_evidence_result_ref",
     "CREATE INDEX IF NOT EXISTS idx_frame_verification_evidence_result_ref "
     "ON frame_verification_evidence (result_ref)"),
    ("idx_coverage_snapshots_backend_epoch",
     "CREATE INDEX IF NOT EXISTS idx_coverage_snapshots_backend_epoch "
     "ON coverage_snapshots (backend, generated_epoch DESC)"),
    ("idx_coverage_alerts_backend_epoch",
     "CREATE INDEX IF NOT EXISTS idx_coverage_alerts_backend_epoch "
     "ON coverage_alerts (backend, created_epoch DESC)"),
    ("idx_coverage_alerts_epoch",
     "CREATE INDEX IF NOT EXISTS idx_coverage_alerts_epoch "
     "ON coverage_alerts (created_epoch DESC)"),
    ("ix_devour_cal_expiry",
     "CREATE INDEX IF NOT EXISTS ix_devour_cal_expiry "
     "ON devour_calibrations(expires_at)"),
    ("ix_devour_cal_updated",
     "CREATE INDEX IF NOT EXISTS ix_devour_cal_updated "
     "ON devour_calibrations(updated_at DESC)"),
]

FRAME_EVIDENCE_STATEMENTS: tuple[str, ...] = (
    tuple(sql for _, sql in TABLES) + tuple(sql for _, sql in INDEXES)
)

# 按 (version, name, 语句) 排序；顺序即执行顺序。
MIGRATIONS: list[tuple[int, str, tuple[str, ...]]] = [
    (FRAME_EVIDENCE_VERSION, FRAME_EVIDENCE_NAME, FRAME_EVIDENCE_STATEMENTS),
]
