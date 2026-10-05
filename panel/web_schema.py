# panel/web_schema.py —— 抓取三表 DDL（按方言生成，测试与生产共用）
_SQLITE = """
CREATE TABLE IF NOT EXISTS web_scrape_runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, tentacle_id TEXT,
  host TEXT NOT NULL, tier TEXT NOT NULL, result_count INTEGER NOT NULL DEFAULT 0,
  matched INTEGER NOT NULL DEFAULT 0, match_score REAL,
  status TEXT NOT NULL DEFAULT 'ok', policy_code TEXT,
  selector_id TEXT, selector_version INTEGER, artifact_ref TEXT,
  created_at TEXT NOT NULL, probe_token TEXT);
CREATE INDEX IF NOT EXISTS ix_scrapes_page ON web_scrape_runs(created_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS ix_scrapes_host_page ON web_scrape_runs(host, created_at DESC, run_id DESC);
CREATE INDEX IF NOT EXISTS ix_scrapes_token ON web_scrape_runs(probe_token);
CREATE TABLE IF NOT EXISTS web_scrape_items (
  run_id TEXT NOT NULL, position INTEGER NOT NULL, excerpt TEXT NOT NULL,
  content_json TEXT, PRIMARY KEY (run_id, position));
CREATE TABLE IF NOT EXISTS web_selector_state (
  domain TEXT NOT NULL, selector_id TEXT NOT NULL, selector TEXT NOT NULL,
  properties TEXT NOT NULL, version INTEGER NOT NULL, last_matched TEXT,
  match_score REAL, updated_at TEXT NOT NULL,
  PRIMARY KEY (domain, selector_id, version));
CREATE INDEX IF NOT EXISTS ix_selector_versions
  ON web_selector_state(domain, selector_id, version DESC);
"""

_PG_SUBS = [
    ("created_at TEXT NOT NULL",  "created_at TIMESTAMPTZ NOT NULL"),
    ("updated_at TEXT NOT NULL",  "updated_at TIMESTAMPTZ NOT NULL"),
    ("match_score REAL",          "match_score DOUBLE PRECISION"),
]


def web_schema_ddl(dialect: str) -> str:
    """dialect: 'sqlite' | 'postgres'。测试与迁移都调这个，避免两份 DDL 漂移。"""
    if dialect == "sqlite":
        return _SQLITE
    if dialect == "postgres":
        sql = _SQLITE
        for old, new in _PG_SUBS:
            sql = sql.replace(old, new)
        return sql
    raise ValueError(f"未知方言: {dialect}")


def statements(dialect: str) -> list[str]:
    return [s.strip() for s in web_schema_ddl(dialect).strip().split(";") if s.strip()]
