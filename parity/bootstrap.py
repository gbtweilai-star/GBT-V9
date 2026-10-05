# parity/bootstrap.py —— 校验 + 组装（纯标准库，不 import 项目代码）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 本文件只准用标准库; guard 通过后才 import parity.runtime;
#       offline 碰到 full-only 能力 → 报配置错, 绝不静默 skip

from __future__ import annotations
import hashlib, json, os
from pathlib import Path
from urllib.parse import unquote, urlsplit

class ParityConfigError(RuntimeError): pass

PROFILES = {"offline", "full"}
KINDS = {"ledger_row", "artifact", "api_response", "audit_gap", "native_probe"}
EQUIVALENCE = {"full", "partial", "none", "unverified"}

# ★代码审查保护的操作白名单：manifest 只能"请求"，不能"授权"
CODE_OPERATION_ALLOWLIST = {
    "scan.cross_review",
    "devour.synthetic_capture",
    "devour.archive_probe",
    # 按实现增补
}


def load_manifest(path: str | Path, profile: str | None = None) -> dict:
    """纯文件校验；不导入应用/账本/R2 任何项目模块。"""
    profile = profile or os.environ.get("PARITY_PROFILE", "")
    if profile not in PROFILES:
        raise ParityConfigError("PARITY_PROFILE 必须是 offline 或 full")

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        raise ParityConfigError(f"manifest 无法读取或解析：{e}") from e

    if doc.get("schema_version") != 1 or not isinstance(doc.get("capabilities"), list):
        raise ParityConfigError("manifest schema_version/capabilities 无效")

    caps = doc["capabilities"]
    ids = [c.get("id") for c in caps]
    if any(not isinstance(x, str) or not x for x in ids) or len(ids) != len(set(ids)):
        raise ParityConfigError("能力 ID 必须非空且唯一")

    known, used_ops = set(ids), set()
    for cap in caps:
        if cap.get("equivalence", "unverified") not in EQUIVALENCE:
            raise ParityConfigError(f"{cap['id']}: equivalence 无效")

        # ★不按 profile 静默过滤：能力不支持当前 profile 就直接报配置错
        profiles = cap.get("profiles")
        if not isinstance(profiles, list) or profile not in profiles:
            raise ParityConfigError(
                f"{cap['id']} 不支持 profile={profile} —— 请用对应清单，不得静默跳过")

        parent = cap.get("parent_id")
        if parent is not None and parent not in known:
            raise ParityConfigError(f"{cap['id']}: parent_id 不存在: {parent}")

        assertions = cap.get("acceptance")
        if not isinstance(assertions, list) or not assertions:
            raise ParityConfigError(f"{cap['id']}: acceptance 必须非空")

        for a in assertions:
            kind = a.get("kind")
            if kind not in KINDS:
                raise ParityConfigError(f"{cap['id']}: 未知断言类型 {kind!r}")
            if kind in {"ledger_row", "artifact", "audit_gap"}:
                op = a.get("operation")
                if not isinstance(op, str) or not op:
                    raise ParityConfigError(f"{cap['id']}: {kind} 缺 operation")
                used_ops.add(op)

    declared = doc.get("allowed_operations")
    if not isinstance(declared, list) or len(declared) != len(set(declared)):
        raise ParityConfigError("manifest.allowed_operations 必须是唯一名称列表")
    if set(declared) != used_ops:
        raise ParityConfigError("allowed_operations 必须与断言实际引用的操作完全一致")
    if not used_ops <= CODE_OPERATION_ALLOWLIST:
        raise ParityConfigError(
            f"manifest 请求了代码白名单之外的操作: "
            f"{sorted(used_ops - CODE_OPERATION_ALLOWLIST)}")

    canonical = json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()
    doc["_profile"] = profile
    doc["_manifest_hash"] = hashlib.sha256(canonical).hexdigest()
    return doc


def build_context(manifest: dict, *, evidence_dir, temp_root):
    """先验环境与隔离，再延迟导入项目运行时模块。"""
    profile = os.environ.get("PARITY_PROFILE", "")
    if profile not in PROFILES or manifest.get("_profile") != profile:
        raise ParityConfigError("PARITY_PROFILE 与已校验 manifest 不一致")

    # ★父进程消毒失败的兜底：子进程仍见到 DATABASE_URL 就拒
    if "DATABASE_URL" in os.environ:
        raise ParityConfigError("子进程环境仍包含 DATABASE_URL —— 拒绝启动")

    dsn = os.environ.get("PARITY_DATABASE_URL", "").strip()
    if not dsn:
        raise ParityConfigError("缺少 PARITY_DATABASE_URL")

    root = Path(temp_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    parts = urlsplit(dsn)

    if profile == "offline":
        if not parts.scheme.startswith("sqlite"):
            raise ParityConfigError("offline 仅允许 SQLite")
        db_path = Path(unquote(parts.path)).resolve()
        if str(db_path) == ":memory:" or root not in db_path.parents:
            raise ParityConfigError("offline SQLite 文件必须位于 PARITY_TEMP_ROOT 内")
        allowed_hosts, bucket = set(), None
    else:
        if parts.scheme.split("+")[0] not in {"postgres", "postgresql"}:
            raise ParityConfigError("full 仅允许 PostgreSQL")
        if not unquote(parts.path.lstrip("/")).startswith("parity_"):
            raise ParityConfigError("full PostgreSQL 库名必须以 parity_ 开头")

        allowed_hosts = {h.strip().lower() for h in
                         os.environ.get("PARITY_ALLOWED_DB_HOSTS", "").split(",")
                         if h.strip()}
        if not allowed_hosts or (parts.hostname or "").lower() not in allowed_hosts:
            raise ParityConfigError("数据库 host 不在 PARITY_ALLOWED_DB_HOSTS allowlist")

        for name in ("PARITY_R2_ENDPOINT", "PARITY_R2_BUCKET",
                     "PARITY_R2_ACCESS_KEY_ID", "PARITY_R2_SECRET_ACCESS_KEY"):
            if not os.environ.get(name, "").strip():
                raise ParityConfigError(f"full profile 缺少 {name}")

        bucket = os.environ["PARITY_R2_BUCKET"].strip()
        prod_bucket = os.environ.get("R2_BUCKET", "").strip()
        if prod_bucket and bucket == prod_bucket:           # ★对称防生产
            raise ParityConfigError("PARITY_R2_BUCKET 与生产 R2_BUCKET 同名 —— 拒绝运行")

    # ── 到这里为止只碰了 env。现在建守卫 ──
    from parity.isolation import IsolationGuard
    guard = IsolationGuard(parity_dsn=dsn, allowed_hosts=allowed_hosts,
                           temp_root=root, r2_bucket=bucket)
    guard.assert_safe("bootstrap", "bootstrap")

    # ── ★guard 通过之后，才允许 import 项目代码 ──
    from parity import runtime
    from parity.adapters import LedgerAdapter, ArtifactAdapter

    ledger = runtime.make_parity_ledger(profile=profile, dsn=dsn)
    registry = runtime.load_skill_registry()
    trusted_ops = runtime.load_operation_registry()

    requested = set(manifest["allowed_operations"])
    missing = requested - set(trusted_ops)
    if missing:
        raise ParityConfigError(f"白名单操作未注册: {sorted(missing)}")
    operations = {n: trusted_ops[n] for n in requested}
    if not all(callable(fn) for fn in operations.values()):
        raise ParityConfigError("operation registry 含不可调用项")

    r2 = runtime.make_parity_r2_client() if profile == "full" else None
    artifacts = ArtifactAdapter(guard=guard, r2=r2, token_root=root / "artifacts")
    ledger_adapter = LedgerAdapter(ledger=ledger, parity_dsn=dsn,
                                   connect_reader=runtime.new_parity_reader,
                                   fetch=runtime.fetch_rows, guard=guard)
    app_factory = runtime.make_test_app_factory(
        ledger=ledger, r2=r2, profile=profile, temp_root=root)

    async def cleanup(kind: str, token: str, observed: dict) -> None:
        await runtime.cleanup_probe_rows(ledger, token)   # 幂等：只删本 token
        await artifacts.cleanup_prefix(token)             # 幂等：本地或 R2 前缀

    return runtime.make_context(
        evidence_dir=Path(evidence_dir), ledger=ledger_adapter,
        artifacts=artifacts, registry=registry, isolation=guard,
        operations=operations, app_factory=app_factory, cleanup=cleanup)
