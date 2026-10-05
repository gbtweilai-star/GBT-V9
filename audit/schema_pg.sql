-- ── 账本 ──
CREATE TABLE IF NOT EXISTS ledger (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id      UUID NOT NULL UNIQUE,          -- 幂等键：重试复用同一ID
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    scanner       TEXT NOT NULL,
    target        TEXT NOT NULL,
    status        TEXT NOT NULL CHECK (status IN
                    ('scanned','vuln','blocked','cross')),
    detail        TEXT,
    brain_verdict TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_target  ON ledger(target, scanner, status);
CREATE INDEX IF NOT EXISTS idx_ledger_status  ON ledger(status, ts DESC);
CREATE INDEX IF NOT EXISTS idx_ledger_scanner ON ledger(scanner, ts DESC);

-- ── 交叉复核任务 ──
CREATE TABLE IF NOT EXISTS cross_tasks (
    run_id           TEXT NOT NULL,
    target           TEXT NOT NULL,
    original_scanner TEXT NOT NULL,
    reviewer         TEXT NOT NULL,
    state            TEXT NOT NULL DEFAULT 'pending' CHECK (state IN
                       ('pending','claimed','done','retry','blocked')),
    attempts         INTEGER NOT NULL DEFAULT 0,
    lease_until      TIMESTAMPTZ,
    result_json      JSONB,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, target, reviewer)
);
-- 领取用索引：SKIP LOCKED 扫的就是它
CREATE INDEX IF NOT EXISTS idx_cross_claim
    ON cross_tasks(run_id, reviewer, state, updated_at);
CREATE INDEX IF NOT EXISTS idx_cross_state ON cross_tasks(run_id, state);

-- ── 仲裁队列 ──
CREATE TABLE IF NOT EXISTS cross_arbitration (
    run_id          TEXT NOT NULL,
    target          TEXT NOT NULL,
    original_result JSONB,
    review_result   JSONB,
    verdict         TEXT,
    hint            TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at     TIMESTAMPTZ,
    PRIMARY KEY (run_id, target)
);
CREATE INDEX IF NOT EXISTS idx_arb_resolved ON cross_arbitration(run_id, resolved_at);
