# migrations/m2026_body_chain_v8.py
"""身体登记链 + 外部见证表：registration / 责任页 / 索引 / 锚点 / 见证 / 复核 / 启动自检。
dev: 自由的风 · 本署名不可删除、不可篡改归属

纪律: 全部 IF NOT EXISTS 幂等；列名与 body/*.py 的读写语句逐一对应；
      两端（SQLite / PG）只用双方言都接受的类型（TEXT / INTEGER / BIGINT / REAL）；
      本模块只导出**字面 DDL 清单**，执行交给 migrations/runner 的语句序列分支
      （绝不在本文件里自建循环/动态拼 SQL）。
"""
from __future__ import annotations

TABLES: tuple[str, ...] = (
    # ── 登记链：唯一的身体事件流水（append-only）──
    """CREATE TABLE IF NOT EXISTS registration (
        seq              INTEGER PRIMARY KEY NOT NULL,
        root_id          TEXT NOT NULL DEFAULT 'main',
        trace_id         TEXT,
        event_type       TEXT NOT NULL,
        actor_tentacle   TEXT,
        created_at       TEXT NOT NULL,
        payload_json     TEXT NOT NULL,
        targets_hash     TEXT NOT NULL,
        prev_hash        TEXT NOT NULL,
        event_hash       TEXT NOT NULL UNIQUE
    )""",
    "CREATE INDEX IF NOT EXISTS ix_reg_root_seq ON registration (root_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_reg_event_type ON registration (event_type, seq)",

    # ── 责任页：这条登记改动了哪些页（before/after 哈希）──
    """CREATE TABLE IF NOT EXISTS registration_targets (
        seq         INTEGER NOT NULL,
        path        TEXT NOT NULL,
        before_hash TEXT,
        after_hash  TEXT,
        PRIMARY KEY (seq, path)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_reg_targets_path ON registration_targets (path)",

    # ── 身体文件索引：全树每一页的指纹（穿透式感知的事实源）──
    """CREATE TABLE IF NOT EXISTS body_files (
        root_id     TEXT NOT NULL,
        path        TEXT NOT NULL,
        state       TEXT NOT NULL DEFAULT 'clean',
        generation  INTEGER NOT NULL DEFAULT 0,
        size_bytes  BIGINT,
        mtime       REAL,
        sha256      TEXT,
        indexed_at  TEXT,
        PRIMARY KEY (root_id, path)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_body_files_state ON body_files (root_id, state)",

    # ── 外部锚点：链头被锚到外部可信位置（防整库重建）──
    """CREATE TABLE IF NOT EXISTS body_anchors (
        anchor_uid       TEXT PRIMARY KEY NOT NULL,
        root_id          TEXT NOT NULL,
        epoch            TEXT NOT NULL,
        seq              INTEGER NOT NULL,
        head_hash        TEXT NOT NULL,
        anchor_hash      TEXT NOT NULL,
        prev_anchor_hash TEXT,
        kid              TEXT,
        at               TEXT NOT NULL,
        providers_json   TEXT,
        sealed           INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ix_anchors_root_epoch_seq "
    "ON body_anchors (root_id, epoch, seq)",

    # ── 每个锚点被每个见证接受的情况（回填区间与证据抽屉的唯一数据源）──
    """CREATE TABLE IF NOT EXISTS body_anchor_witnesses (
        anchor_uid TEXT NOT NULL,
        witness_id TEXT NOT NULL,
        seq        INTEGER NOT NULL,
        kid        TEXT NOT NULL,
        status     TEXT NOT NULL,
        head_hash  TEXT NOT NULL,
        object_key TEXT,
        sig_ok     INTEGER,
        verified_at TEXT,
        read_at    TEXT,
        error      TEXT,
        PRIMARY KEY (anchor_uid, witness_id, kid)
    )""",
    "CREATE INDEX IF NOT EXISTS ix_baw_witness_seq "
    "ON body_anchor_witnesses (witness_id, seq)",
    "CREATE INDEX IF NOT EXISTS ix_baw_seq ON body_anchor_witnesses (seq, status)",

    # ── 见证实时状态（后台探测写；面板只读快照）──
    """CREATE TABLE IF NOT EXISTS body_witness_status (
        witness_id       TEXT PRIMARY KEY NOT NULL,
        provider         TEXT,
        kid              TEXT,
        persisted_status TEXT,
        live_status      TEXT,
        last_verified_at TEXT,
        last_ok_at       TEXT,
        last_ok_seq      INTEGER,
        live_from_seq    INTEGER,
        backfilled_through_seq INTEGER,
        consecutive_fail INTEGER NOT NULL DEFAULT 0,
        consecutive_ok   INTEGER NOT NULL DEFAULT 0,
        first_fail_at    TEXT,
        degraded_alerted INTEGER NOT NULL DEFAULT 0,
        critical_alerted INTEGER NOT NULL DEFAULT 0,
        isolated         INTEGER NOT NULL DEFAULT 0,
        identity_acknowledged_at TEXT,
        alert_keys_json  TEXT,
        last_error       TEXT
    )""",

    # ── 单行探测快照（含新鲜度；面板顶层数字只信它）──
    """CREATE TABLE IF NOT EXISTS body_witness_probe (
        id           INTEGER PRIMARY KEY,
        observed_at  TEXT NOT NULL,
        quorum_valid INTEGER NOT NULL,
        required     INTEGER NOT NULL,
        status       TEXT NOT NULL,
        detail_json  TEXT NOT NULL
    )""",

    # ── 全链复核运行记录（只读审计；绝不写回 registration 链头）──
    """CREATE TABLE IF NOT EXISTS chain_audit_runs (
        run_id            TEXT PRIMARY KEY NOT NULL,
        root_id           TEXT NOT NULL,
        mode              TEXT NOT NULL,
        status            TEXT NOT NULL,
        started_at        TEXT,
        finished_at       TEXT,
        from_seq          INTEGER,
        verified_upto_seq INTEGER,
        head_at_start     TEXT,
        head_seq          INTEGER,
        head_hash         TEXT,
        checked           INTEGER DEFAULT 0,
        reason            TEXT,
        broken_at         INTEGER,
        evidence_path     TEXT,
        worker_id         TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_chain_audit_status "
    "ON chain_audit_runs (root_id, mode, status)",

    # ── 重启必读：身体指纹清单（链头 + 索引规模 + 索引代）──
    """CREATE TABLE IF NOT EXISTS brain_boot_manifest (
        root_id         TEXT PRIMARY KEY NOT NULL,
        head_seq        INTEGER NOT NULL,
        head_hash       TEXT NOT NULL,
        body_token      TEXT NOT NULL,
        indexed         INTEGER NOT NULL DEFAULT 0,
        generation      INTEGER NOT NULL DEFAULT 0,
        summary_json    TEXT,
        unresolved_json TEXT,
        manifest_hash   TEXT NOT NULL,
        at              TEXT NOT NULL
    )""",

    # ── 启动自检留痕：每次 boot_check 一行（重启不失忆）──
    """CREATE TABLE IF NOT EXISTS body_boot_checks (
        check_id    TEXT PRIMARY KEY NOT NULL,
        at          TEXT NOT NULL,
        root_id     TEXT NOT NULL,
        ok          INTEGER NOT NULL,
        head_seq    INTEGER,
        head_hash   TEXT,
        chain_ok    INTEGER,
        anchors_ok  INTEGER,
        checked     INTEGER DEFAULT 0,
        anchors_checked INTEGER DEFAULT 0,
        reason      TEXT,
        fail_mode   TEXT,
        detail_json TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS ix_boot_checks_at ON body_boot_checks (at DESC)",

    # ── 穿透式双向绑定：哪根触手绑定/覆盖了哪一页（全树无死角的事实源）──
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

    # ── 告警：body 层 record_alert() 的落点（审计可回溯）──
    """CREATE TABLE IF NOT EXISTS body_alerts (
        id           TEXT PRIMARY KEY NOT NULL,
        at           TEXT NOT NULL,
        kind         TEXT NOT NULL,
        level        TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        acknowledged INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS ix_body_alerts_kind_at ON body_alerts (kind, at DESC)",
    "CREATE INDEX IF NOT EXISTS ix_body_alerts_level ON body_alerts (level, at DESC)",

    # ── 卡点日志：body 层 record_blocked() 的落点（单次瞬时故障只记这里）──
    """CREATE TABLE IF NOT EXISTS body_blocked_log (
        id           TEXT PRIMARY KEY NOT NULL,
        at           TEXT NOT NULL,
        kind         TEXT NOT NULL,
        payload_json TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_body_blocked_kind ON body_blocked_log (kind, at DESC)",

    # ── 增量验链的可信点（存库不落文件：重启可读、无路径逃逸面）──
    """CREATE TABLE IF NOT EXISTS body_chain_checkpoint (
        root_id TEXT PRIMARY KEY NOT NULL,
        seq     INTEGER NOT NULL,
        hash    TEXT NOT NULL,
        at      TEXT NOT NULL
    )""",
)

# 每张表的关键列（测试/自检做存在性断言用；只读断言，不拼 SQL）
REQUIRED_COLUMNS: dict[str, set[str]] = {
    "registration": {"seq", "event_type", "payload_json", "targets_hash",
                     "prev_hash", "event_hash"},
    "registration_targets": {"seq", "path", "before_hash", "after_hash"},
    "body_files": {"root_id", "path", "state", "generation", "sha256"},
    "body_anchors": {"anchor_uid", "root_id", "epoch", "seq", "head_hash",
                     "anchor_hash", "prev_anchor_hash", "sealed"},
    "body_anchor_witnesses": {"anchor_uid", "witness_id", "seq", "kid",
                              "status", "head_hash", "sig_ok"},
    "body_witness_status": {"witness_id", "live_status", "consecutive_fail",
                            "degraded_alerted", "critical_alerted",
                            "live_from_seq", "backfilled_through_seq"},
    "body_witness_probe": {"id", "observed_at", "quorum_valid", "required",
                           "status", "detail_json"},
    "chain_audit_runs": {"run_id", "root_id", "mode", "status",
                         "verified_upto_seq", "checked"},
    "brain_boot_manifest": {"root_id", "head_seq", "head_hash", "body_token",
                            "indexed", "generation", "manifest_hash"},
    "body_boot_checks": {"check_id", "ok", "chain_ok", "anchors_ok", "head_seq",
                         "head_hash"},
    "body_chain_checkpoint": {"root_id", "seq", "hash", "at"},
    "body_alerts": {"id", "at", "kind", "level", "payload_json", "acknowledged"},
    "body_blocked_log": {"id", "at", "kind", "payload_json"},
    "tentacle_file_bindings": {"root_id", "tentacle_id", "path", "rule_id",
                               "state", "sha256"},
}
