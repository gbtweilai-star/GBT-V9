class LedgerAdapter:
    """
    ledger:       你现有 audit/ledger.py 的 实例（PGLedger / SQLite ledger）
    connect_reader: () -> 新连接的 async/sync 工厂（**不复用写事务**）
    fetch:         async (conn, sql, params) -> list[dict]
    """

    def __init__(self, *, ledger, parity_dsn: str, connect_reader, fetch,
                 guard: IsolationGuard):
        self.ledger = ledger
        self.backend = getattr(ledger, "dialect", None) or (
            "sqlite" if guard.is_sqlite else "postgres")
        self.guard = guard
        self._connect = connect_reader
        self._fetch = fetch

    async def read_back_rows(self, *, table: str, probe_token: str) -> list[dict]:
        if not table.replace("_", "").isalnum():
            raise ValueError("非法表名")                       # 防注入
        # 新连接 + 参数化 + 只读本次 token 的行
        conn = await self._connect()
        try:
            return await self._fetch(
                conn,
                f"SELECT * FROM {table} WHERE probe_token = %s" # sqlite 用 ?
                .replace("%s", "?" if self.backend == "sqlite" else "%s"),
                (probe_token,))
        finally:
            await _close(conn)

    async def read_gaps(self, *, trace_id: str) -> list[dict]:
        conn = await self._connect()
        try:
            ph = "?" if self.backend == "sqlite" else "%s"
            return await self._fetch(
                conn,
                f"SELECT trace_id, start_frame, end_frame, reason, created_at "
                f"FROM parity_audit_gaps WHERE trace_id = {ph} ORDER BY start_frame",
                (trace_id,))
        finally:
            await _close(conn)


async def _close(conn) -> None:
    c = getattr(conn, "close", None) or getattr(conn, "release", None)
    if c:
        r = c()
        if hasattr(r, "__await__"):
            await r


PARITY_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS parity_audit_gaps (
  trace_id     TEXT NOT NULL,
  start_frame  INTEGER NOT NULL,
  end_frame    INTEGER NOT NULL,
  reason       TEXT,
  created_at   TEXT NOT NULL,
  PRIMARY KEY (trace_id, start_frame)
);
"""

class ArtifactAdapter:
    def __init__(self, *, guard: IsolationGuard, r2, token_root: Path):
        self.guard = guard
        self.r2 = r2                     # 你的 R2 客户端（boto3/S3 兼容均可）
        self.token_root = token_root     # <temp_root>/parity-artifacts

    async def assert_ephemeral_ref(self, ref: str, token: str) -> None:
        r = str(ref)
        if r.startswith("file://"):
            self.guard.assert_temp_path(Path(r[7:]))                 # 本地必须在 temp 根内
        elif r.startswith(("r2://", "s3://")):
            hdr, _, key = r.partition("://")
            bucket, _, obj = key.partition("/")
            if bucket != self.guard.r2_bucket:
                raise IsolationViolation(f"非测试 bucket: {bucket}")
            self.guard.assert_r2_key(obj, token)                     # 必须 parity/<token>/
        else:
            raise IsolationViolation(f"未知 artifact ref 形式: {r}")

    async def read_back(self, ref: str) -> bytes:
        r = str(ref)
        if r.startswith("file://"):
            path = self.guard.assert_temp_path(Path(r[7:]))
            return path.read_bytes()                                 # 真回读
        hdr, _, key = r.partition("://")
        bucket, _, obj = key.partition("/")
        resp = await _maybe_await(self.r2.get_object(Bucket=bucket, Key=obj))
        body = resp["Body"]
        return body.read() if hasattr(body, "read") else bytes(body)

    async def cleanup_prefix(self, token: str) -> None:
        """幂等分页删 parity/<token>/ ；重复调用无副作用。"""
        prefix = f"parity/{token}/"
        cont = None
        while True:
            kw = {"Bucket": self.guard.r2_bucket, "Prefix": prefix, "MaxKeys": 1000}
            if cont:
                kw["ContinuationToken"] = cont
            page = await _maybe_await(self.r2.list_objects_v2(**kw))
            for obj in page.get("Contents", []):
                await _maybe_await(self.r2.delete_object(
                    Bucket=self.guard.r2_bucket, Key=obj["Key"]))
            if not page.get("IsTruncated"):
                return
            cont = page.get("NextContinuationToken")
        # 本地：token 目录整删（不存在也不报错）
        d = self.token_root / token
        if d.exists():
            import shutil; shutil.rmtree(d, ignore_errors=True)


async def _maybe_await(x):
    return await x if hasattr(x, "__await__") else x

def make_app_factory(*, app_module_path: str, ledger, r2, guard, temp_root: Path):
    """
    每次 probe 用测试配置建 app，并用 dependency_overrides 注入 parity 账本/R2。
    禁止导入会在模块级建生产连接的单例 app。
    """
    import importlib

    def _factory(app_name: str, token: str) -> object:
        module = importlib.import_module(app_module_path)
        create_app = getattr(module, "create_app", None)
        if create_app is None:
            raise ParityConfigError(
                f"{app_module_path} 需要暴露 create_app(settings) 工厂；"
                "不要导入模块级 app 单例（会连生产）")

        settings = {
            "database_url": _require("PARITY_DATABASE_URL"),
            "r2_bucket": _require("PARITY_R2_BUCKET"),
            "tmp_root": str(temp_root),
            "parity_token": token,
            "read_only": False,
        }
        app = create_app(settings)

        # 依赖注入：把生产依赖替换成 parity 实例
        dep_ledger = getattr(module, "get_ledger", None)
        dep_r2 = getattr(module, "get_r2", None)
        overrides = app.dependency_overrides
        if dep_ledger:
            overrides[dep_ledger] = lambda: ledger
        if dep_r2:
            overrides[dep_r2] = lambda: r2
        return app

    return _factory

async def build_parity_context(*, ledger, r2, registry, connect_reader, fetch,
                               operations: dict, app_module_path: str):
    parity_dsn = _require("PARITY_DATABASE_URL")
    hosts = set(filter(None, _require("PARITY_ALLOWED_DB_HOSTS").split(",")))
    r2_bucket = _require("PARITY_R2_BUCKET")

    temp_root = Path(os.environ.get("PARITY_TEMP_ROOT")
                     or tempfile.mkdtemp(prefix="parity-")).resolve()
    temp_root.mkdir(parents=True, exist_ok=True)
    evidence_dir = Path(os.environ.get("PARITY_EVIDENCE_DIR")
                        or temp_root / "evidence").resolve()
    token_root = temp_root / "parity-artifacts"

    guard = IsolationGuard(parity_dsn=parity_dsn, allowed_hosts=hosts,
                           temp_root=temp_root, r2_bucket=r2_bucket)   # ← 失败即终止
    led = LedgerAdapter(ledger=ledger, parity_dsn=parity_dsn,
                        connect_reader=connect_reader, fetch=fetch, guard=guard)
    art = ArtifactAdapter(guard=guard, r2=r2, token_root=token_root)
    factory = make_app_factory(app_module_path=app_module_path, ledger=ledger,
                               r2=r2, guard=guard, temp_root=temp_root)

    async def cleanup(kind: str, token: str, observed: dict) -> None:
        await art.cleanup_prefix(token)          # 幂等，只删本 token 前缀

    return SimpleNamespace(evidence_dir=str(evidence_dir), ledger=led, artifacts=art,
                           registry=registry, isolation=guard, operations=operations,
                           app_factory=factory, cleanup=cleanup)
