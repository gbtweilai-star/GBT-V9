# parity/runtime.py —— parity 与项目代码的唯一边界
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 只建独立 parity 账本(不碰生产单例); probe_token 独立标记, 不冒充 trace_id;
#       清理只删本 token 登记过的 PK, 绝不按 trace_id 删

from __future__ import annotations
import json, os, sqlite3
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from parity.bootstrap import CODE_OPERATION_ALLOWLIST, ParityConfigError

# 需要 probe_token 关联列的业务表（幂等 ALTER）
PARITY_TRACKED_TABLES = {
    "cross_scan_results",
    "audit_gaps",
    "media_job_events",
    "devour_segments",
    "workflow_runs",
    "office_ops",
}

# parity 自有的元数据表
PARITY_META_DDL = """
CREATE TABLE IF NOT EXISTS parity_probe_index (
  probe_token TEXT NOT NULL,
  table_name  TEXT NOT NULL,
  pk_json     TEXT NOT NULL,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (probe_token, table_name, pk_json)
);
CREATE INDEX IF NOT EXISTS ix_parity_probe_token
  ON parity_probe_index (probe_token);
"""


def _q(ident: str) -> str:
    if not ident.replace("_", "").isalnum():
        raise ParityConfigError(f"非法表名/列名: {ident}")
    return ident


# ─────────────────────────────────────────────────────────────
# ① 账本：显式构造，绝不复用生产单例
# ─────────────────────────────────────────────────────────────
def make_parity_ledger(*, profile: str, dsn: str):
    if profile == "offline":
        from audit.ledger import SQLiteLedger          # ← 对齐点：你的类名
        path = unquote(urlsplit(dsn).path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        ledger = SQLiteLedger(path)                     # 只传 parity 路径
    else:
        from audit.ledger import PGLedger               # ← 对齐点
        ledger = PGLedger(dsn, pool_size=2, read_only=False)
    ensure_parity_schema(ledger)
    return ledger


def ensure_parity_schema(ledger) -> None:
    """幂等迁移：建 parity 元表 + 给业务表加 nullable probe_token（不建 FK/不改语义）。"""
    for stmt in PARITY_META_DDL.strip().split(";"):
        if stmt.strip():
            ledger.execute(stmt.strip())

    dialect = getattr(ledger, "dialect", "sqlite")
    for table in sorted(PARITY_TRACKED_TABLES):
        try:
            if dialect == "sqlite":
                cols = {r[1] for r in
                        ledger.execute_fetch(f"PRAGMA table_info({_q(table)})")}
                if not cols:
                    continue                                # 该表不存在 → 跳过
                if "probe_token" not in cols:
                    ledger.execute(f"ALTER TABLE {_q(table)} ADD COLUMN probe_token TEXT")
            else:
                ledger.execute(
                    f"ALTER TABLE {_q(table)} ADD COLUMN IF NOT EXISTS probe_token TEXT")
                ledger.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_{table}_probe_token "
                    f"ON {_q(table)} (probe_token)")
        except Exception as e:                              # 表不存在等 → 记录但不中断
            print(f"[parity] 跳过 {table} 的 probe_token 迁移: {e}")


def track_probe_ref(ledger, token: str, table: str, pk: dict) -> None:
    """登记本次 probe 创建的实体主键 —— 清理只依据这张表。"""
    import datetime as _dt
    ledger.execute(
        "INSERT OR REPLACE INTO parity_probe_index"
        "(probe_token, table_name, pk_json, created_at) VALUES (?,?,?,?)"
        if getattr(ledger, "dialect", "sqlite") == "sqlite" else
        "INSERT INTO parity_probe_index"
        "(probe_token, table_name, pk_json, created_at) VALUES (%s,%s,%s,%s)"
        " ON CONFLICT (probe_token, table_name, pk_json) DO NOTHING",
        (token, table, json.dumps(pk, sort_keys=True),
         _dt.datetime.utcnow().isoformat(timespec="seconds")))


# ─────────────────────────────────────────────────────────────
# ② 读回：新连接（绝不复用写事务）
# ─────────────────────────────────────────────────────────────
def new_parity_reader():
    """返回**新**连接；sync 或 async 都行（probe 侧已收敛）。"""
    dsn = os.environ["PARITY_DATABASE_URL"]
    parts = urlsplit(dsn)
    if parts.scheme.startswith("sqlite"):
        conn = sqlite3.connect(unquote(parts.path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    import psycopg                                    # ← 对齐点：你的驱动
    from psycopg.rows import dict_row
    return psycopg.connect(dsn, row_factory=dict_row, autocommit=True)


async def fetch_rows(conn, sql: str, params: tuple) -> list[dict]:
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    if rows and hasattr(rows[0], "keys"):
        return [dict(r) for r in rows]
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


# ─────────────────────────────────────────────────────────────
# ③ 注册表与操作白名单
# ─────────────────────────────────────────────────────────────
def load_skill_registry():
    from skills.native import SkillRegistry, register_native_skills  # ← 对齐点
    reg = SkillRegistry()
    register_native_skills(reg)
    return reg


def load_operation_registry() -> dict:
    from parity.operations import OPERATIONS
    unknown = set(OPERATIONS) - CODE_OPERATION_ALLOWLIST
    if unknown:
        raise ParityConfigError(f"operations 注册了白名单外的操作: {sorted(unknown)}")
    return dict(OPERATIONS)


# ─────────────────────────────────────────────────────────────
# ④ R2 测试客户端
# ─────────────────────────────────────────────────────────────
def make_parity_r2_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=os.environ["PARITY_R2_ENDPOINT"],
        aws_access_key_id=os.environ["PARITY_R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["PARITY_R2_SECRET_ACCESS_KEY"],
        region_name="auto",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"},
                      retries={"max_attempts": 3, "mode": "standard"}),
    )


# ─────────────────────────────────────────────────────────────
# ⑤ ASGI 测试应用工厂
# ─────────────────────────────────────────────────────────────
def make_test_app_factory(*, ledger, r2, profile: str, temp_root: Path):
    def _factory(app_name: str, token: str):
        import importlib
        module = importlib.import_module("panel.server")
        create_app = getattr(module, "create_app", None)
        if create_app is None:
            raise ParityConfigError(
                "panel/server.py 需暴露 create_app(settings)；"
                "不得导入模块级 app 单例（会连生产）")

        settings = {
            "database_url": os.environ["PARITY_DATABASE_URL"],
            "r2": r2, "profile": profile,
            "tmp_root": str(temp_root), "parity_token": token,
        }
        app = create_app(settings)
        for name, dep in (("get_ledger", ledger), ("get_r2", r2)):
            fn = getattr(module, name, None)
            if fn is not None:
                app.dependency_overrides[fn] = (lambda d: (lambda: d))(dep)
        return app
    return _factory


# ─────────────────────────────────────────────────────────────
# ⑥ 清理：只删本 token 登记过的 PK（幂等）
# ─────────────────────────────────────────────────────────────
async def cleanup_probe_rows(ledger, token: str) -> None:
    ph = "?" if getattr(ledger, "dialect", "sqlite") == "sqlite" else "%s"
    rows = ledger.execute_fetch(
        f"SELECT table_name, pk_json FROM parity_probe_index WHERE probe_token = {ph}",
        (token,))

    for table, pk_json in rows:
        pk = json.loads(pk_json)
        where = " AND ".join(f"{_q(k)} = {ph}" for k in pk)
        ledger.execute(
            f"DELETE FROM {_q(table)} WHERE probe_token = {ph} AND {where}",
            (token, *pk.values()))        # ★必须同时核 probe_token，防误删真实数据

    # 自有的 gap 表与索引表
    if "audit_gaps" in {t for t, _ in rows}:
        ledger.execute(f"DELETE FROM audit_gaps WHERE probe_token = {ph}", (token,))
    ledger.execute(f"DELETE FROM parity_probe_index WHERE probe_token = {ph}", (token,))


# ─────────────────────────────────────────────────────────────
# ⑦ 组装 ctx
# ─────────────────────────────────────────────────────────────
def make_context(*, evidence_dir, ledger, artifacts, registry,
                 isolation, operations, app_factory, cleanup):
    return SimpleNamespace(
        evidence_dir=str(evidence_dir), ledger=ledger, artifacts=artifacts,
        registry=registry, isolation=isolation, operations=operations,
        app_factory=app_factory, cleanup=cleanup)
