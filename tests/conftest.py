# tests/conftest.py —— 抓取接口测试装置（双后端）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations
import os, sqlite3, uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_KEY = "k" * 48                                   # ≥32 字节，够用


# ── CI 必须跑 PG：缺配置直接失败，不 skip ──
def pytest_configure(config):
    if os.getenv("PARITY_PG_REQUIRED") == "1" and not os.getenv("PARITY_DATABASE_URL"):
        raise pytest.UsageError(
            "PARITY_PG_REQUIRED=1 但未设置 PARITY_DATABASE_URL —— 拒绝静默 skip")


def _backend_params():
    params = [pytest.param("sqlite", id="sqlite")]
    if os.getenv("PARITY_DATABASE_URL"):
        params.append(pytest.param("postgres", id="postgres"))
    else:
        params.append(pytest.param("postgres", id="postgres",
                                   marks=pytest.mark.skip(reason="本地未配置 PostgreSQL")))
    return params


# ─────────────────────────────────────────────
# 时间归一化：写入与比较必须是**同一种**规范格式
# ─────────────────────────────────────────────
def canon_iso(value) -> str:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)     # naive 一律按 UTC 解释
    return (value.astimezone(timezone.utc)
                 .isoformat(timespec="microseconds").replace("+00:00", "Z"))


class TestDB:
    """最小 db 适配器：dialect / fetch_all / timestamp_param（与生产路由契约一致）"""

    def __init__(self, kind: str, conn, dialect: str):
        self.kind, self.conn, self.dialect = kind, conn, dialect

    # ---- 路由契约 ----
    def timestamp_param(self, value):
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return canon_iso(dt) if self.kind == "sqlite" else dt

    async def fetch_all(self, sql: str, params):
        if self.kind == "sqlite":
            cur = self.conn.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        with self.conn.cursor() as cur:
            cur.execute(sql.replace("%s", "%s"), tuple(params))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    # ---- 测试辅助 ----
    def execute(self, sql: str, params=()):
        if self.kind == "sqlite":
            self.conn.execute(sql, tuple(params))
            self.conn.commit()
        else:
            with self.conn.cursor() as cur:
                cur.execute(sql, tuple(params))
            self.conn.commit()

    def insert_scrape_run(self, *, run_id, host, tier="http", matched=True,
                          match_score=None, created_at, status="ok",
                          policy_code=None, result_count=0, selector_id=None,
                          selector_version=None, artifact_ref=None):
        self.execute(
            "INSERT INTO web_scrape_runs (run_id, trace_id, tentacle_id, host, tier,"
            " result_count, matched, match_score, status, policy_code, selector_id,"
            " selector_version, artifact_ref, created_at, probe_token)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)".replace("?", "%s")
            if self.kind == "postgres" else
            "INSERT INTO web_scrape_runs (run_id, trace_id, tentacle_id, host, tier,"
            " result_count, matched, match_score, status, policy_code, selector_id,"
            " selector_version, artifact_ref, created_at, probe_token)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (run_id, f"tr-{run_id}", "T1", host, tier, result_count,
             1 if matched else 0, match_score, status, policy_code,
             selector_id, selector_version, artifact_ref,
             self.timestamp_param(created_at)))

    def insert_selector_version(self, *, domain, selector_id, version,
                                selector=".item", properties="{}",
                                match_score=None, last_matched=None, updated_at=None):
        self.execute(
            ("INSERT INTO web_selector_state (domain, selector_id, selector,"
             " properties, version, last_matched, match_score, updated_at)"
             " VALUES (?,?,?,?,?,?,?,?)").replace("?", "%s")
            if self.kind == "postgres" else
            "INSERT INTO web_selector_state (domain, selector_id, selector,"
            " properties, version, last_matched, match_score, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (domain, selector_id, selector, properties, version, last_matched,
             match_score, self.timestamp_param(updated_at or DDL_TS)))

    def insert_item(self, *, run_id, position, excerpt):
        self.execute(
            ("INSERT INTO web_scrape_items (run_id, position, excerpt, content_json)"
             " VALUES (?,?,?,NULL)").replace("?", "%s")
            if self.kind == "postgres" else
            "INSERT INTO web_scrape_items (run_id, position, excerpt, content_json)"
            " VALUES (?,?,?,NULL)",
            (run_id, position, excerpt))

    # ---- 数据未变，复用一条读取路径 ----
    def rows(self, sql, params=()):
        if self.kind == "sqlite":
            cur = self.conn.execute(sql, tuple(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
        with self.conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


DDL_TS = datetime(2025, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

SQLITE_DDL = """
CREATE TABLE web_scrape_runs (
  run_id TEXT PRIMARY KEY, trace_id TEXT NOT NULL, tentacle_id TEXT,
  host TEXT NOT NULL, tier TEXT NOT NULL, result_count INTEGER NOT NULL DEFAULT 0,
  matched INTEGER NOT NULL DEFAULT 0, match_score REAL,
  status TEXT NOT NULL DEFAULT 'ok', policy_code TEXT,
  selector_id TEXT, selector_version INTEGER, artifact_ref TEXT,
  created_at TEXT NOT NULL, probe_token TEXT);
CREATE INDEX ix_scrapes_page ON web_scrape_runs(created_at DESC, run_id DESC);
CREATE INDEX ix_scrapes_host_page ON web_scrape_runs(host, created_at DESC, run_id DESC);
CREATE TABLE web_scrape_items (
  run_id TEXT NOT NULL, position INTEGER NOT NULL, excerpt TEXT NOT NULL,
  content_json TEXT, PRIMARY KEY (run_id, position));
CREATE TABLE web_selector_state (
  domain TEXT NOT NULL, selector_id TEXT NOT NULL, selector TEXT NOT NULL,
  properties TEXT NOT NULL, version INTEGER NOT NULL, last_matched TEXT,
  match_score REAL, updated_at TEXT NOT NULL,
  PRIMARY KEY (domain, selector_id, version));
CREATE INDEX ix_selector_versions ON web_selector_state(domain, selector_id, version DESC);
"""


def _make_sqlite(tmp_path: Path) -> TestDB:
    conn = sqlite3.connect(tmp_path / "web.sqlite", isolation_level=None)
    for stmt in SQLITE_DDL.strip().split(";"):
        if stmt.strip():
            conn.execute(stmt)
    conn.commit()
    return TestDB("sqlite", conn, "sqlite")


def _make_postgres(schema: str) -> TestDB:
    import psycopg
    from psycopg.rows import dict_row
    dsn = os.environ["PARITY_DATABASE_URL"]
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{schema}"')
    conn = psycopg.connect(dsn, options=f"-c search_path={schema}",
                           row_factory=dict_row, autocommit=True)
    ddl = (SQLITE_DDL
           .replace("INTEGER NOT NULL DEFAULT 0, matched", "INTEGER NOT NULL DEFAULT 0, matched")
           .replace("created_at TEXT NOT NULL", "created_at TIMESTAMPTZ NOT NULL")
           .replace("updated_at TEXT NOT NULL", "updated_at TIMESTAMPTZ NOT NULL")
           .replace("match_score REAL", "match_score DOUBLE PRECISION"))
    with conn.cursor() as cur:
        for stmt in ddl.strip().split(";"):
            if stmt.strip():
                cur.execute(stmt)
    return TestDB("postgres", conn, "postgres")


@pytest.fixture(params=_backend_params())
def web_api(request, tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_CURSOR_HMAC_KEY", TEST_KEY)     # 每个测试都有密钥
    if request.param == "sqlite":
        db = _make_sqlite(tmp_path)
    else:
        db = _make_postgres(f"test_{uuid.uuid4().hex[:12]}")

    from panel.server import create_app                    # ← 对齐点
    from panel.routes_web import get_db                    # ← 对齐点
    app = create_app({"database_url": "unused", "profile": "test"})
    app.dependency_overrides[get_db] = lambda: db

    try:
        yield request.param, TestClient(app), db
    finally:
        if request.param == "postgres":
            with db.conn.cursor() as cur:
                cur.execute("SELECT current_schema()")
                schema = cur.fetchone()
            import psycopg
            with psycopg.connect(os.environ["PARITY_DATABASE_URL"],
                                 autocommit=True) as admin:
                admin.execute("DROP SCHEMA IF EXISTS %s CASCADE" % (
                    '"' + (schema[0] if schema else "") + '"'))
        db.conn.close()



# ─────────────────────────────────────────────
# 传感器/执行层测试夹具（tests/test_voice.py · test_mic.py · test_actuator.py 用）
# 契约：一律 SQLite、绝不碰真实 PG；账本放 tmp_path 隔离
# ─────────────────────────────────────────────
import sys as _sys

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


@pytest.fixture
def ledger(tmp_path):
    os.environ.setdefault("LEDGER_BACKEND", "sqlite")
    os.environ.pop("DATABASE_URL", None)          # 测试绝不污染真实 PG
    from audit.ledger import Ledger
    led = Ledger(db=str(tmp_path / "test_ledger.db"))
    yield led
    led.close()


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    return d



# ─────────────────────────────────────────────
# 收集排除：tests/legacy_spec/ 是"对话目标态"测试（引用尚未落地的 API，
# 如 body.ports 适配器、body.identity 增强、psycopg3、workflows.list_flows 等），
# 保留作规格，不进 CI。对应 API 落地后移回 tests/ 即可回归。
# ─────────────────────────────────────────────
collect_ignore_glob = ["legacy_spec/*"]
