# parity/target_schema.py —— target 三元的唯一校验/解析规则
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 规则只此一份, bootstrap 与 align_names 都调它;
#       断言不得重复声明 target/native_skill/path; 旧字段只在 v1 允许迁移

KINDS = {"skill", "subsystem", "api"}

# kind → 必须有的"直接验收"断言
DIRECT_ASSERTION = {
    "skill":     "native_probe",
    "subsystem": "subsystem_probe",
    "api":       "api_response",
}
SUPPORT_ASSERTIONS = {"ledger_row", "artifact", "audit_gap"}


class ManifestError(RuntimeError):
    pass


def validate_target(target) -> None:
    if not isinstance(target, dict) or target.get("kind") not in KINDS:
        raise ManifestError("target.kind 必须是 skill/subsystem/api")
    if set(target) - {"kind", "name", "version"}:
        raise ManifestError(f"target 含未知字段: {sorted(set(target) - {'kind','name','version'})}")
    name = target.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ManifestError("target.name 必填且非空")

    if target["kind"] == "skill":
        if "version" in target and (not isinstance(target["version"], str)
                                    or not target["version"].strip()):
            raise ManifestError("skill target.version 必须是非空字符串")
    elif "version" in target:
        raise ManifestError("version 仅允许用于 skill target")   # subsystem/api 无版本


def validate_assertion(target: dict, assertion: dict) -> None:
    kind = assertion.get("kind")
    if kind in DIRECT_ASSERTION.values():
        expected = DIRECT_ASSERTION[target["kind"]]
        if kind != expected:
            raise ManifestError(
                f"{target['kind']} target ({target['name']}) 必须使用 {expected}，"
                f"实际 {kind}")
        leaked = {"target", "native_skill", "path", "app"} & set(assertion)
        if leaked:
            raise ManifestError(f"断言不得重复声明 {sorted(leaked)}（应在 target/API_TARGETS 里）")
    elif kind in SUPPORT_ASSERTIONS:
        if not isinstance(assertion.get("operation"), str) or not assertion["operation"]:
            raise ManifestError(f"{kind} 断言必须指定 operation")
    else:
        raise ManifestError(f"未知断言类型: {kind}")


def normalize_capability(cap: dict, schema_version: int) -> dict:
    """旧字段迁移：只在 v1 且无冲突时允许 native_skill → target。"""
    if "native_skill" in cap:
        if schema_version != 1 or "target" in cap:
            raise ManifestError(f"{cap.get('id')}: 不允许混用 native_skill 与 target")
        cap["target"] = {"kind": "skill", "name": cap.pop("native_skill")}
    if "target" not in cap:
        raise ManifestError(f"{cap.get('id')}: 缺少 target")
    return cap
