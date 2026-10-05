# skills/spec.py —— 能力规格声明（可选扩展，不破坏现有能力）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 约定:
#   spec() 声明 inputs/outputs/idempotent/risk。
#   未声明 schema 的能力 → schema_declared=False, 编辑器回退自由 JSON,
#   并诚实标注"未声明 schema，无法做类型校验"。
import re

SECRET_OK = re.compile(r"^\$secret:[A-Z0-9_]+$")
_SECRET_LOOKS = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|"
                           r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
                           r"(password|passwd|api[_-]?key)\s*[:=]\s*\S{6,})",
                           re.IGNORECASE)


def is_declared(skill) -> bool:
    return callable(getattr(skill, "spec", None))


def get_spec(skill) -> dict:
    if not is_declared(skill):
        return {}
    try:
        return skill.spec() or {}
    except Exception:
        return {}


def check_secret_field(value) -> tuple:
    """密钥类字段只接受 $secret:NAME；返回 (ok, reason)"""
    if value is None or value == "":
        return True, ""
    if isinstance(value, str) and SECRET_OK.match(value):
        return True, ""
    if isinstance(value, str) and _SECRET_LOOKS.search(value):
        return False, "疑似明文凭据，必须改用 $secret:NAME 引用"
    return True, ""


_VALID_TYPES = {"string", "integer", "number", "boolean", "enum",
                "object", "array", "any"}


def validate_spec(spec: dict) -> list:
    """校验一份 spec() 声明的形状；返回问题清单（空=合格）。
    编辑器据此决定"真实类型校验"还是"自由 JSON 回退"。"""
    errs = []
    if not isinstance(spec, dict):
        return ["spec 不是 dict"]
    for key in ("inputs", "outputs"):
        v = spec.get(key)
        if not isinstance(v, dict):
            errs.append(f"缺少 {key}（须为 dict）")
            continue
        for name, f in v.items():
            if not isinstance(f, dict):
                errs.append(f"{key}.{name} 声明不是 dict")
                continue
            if f.get("type") not in _VALID_TYPES:
                errs.append(f"{key}.{name}.type 非法: {f.get('type')!r}")
            if f.get("type") == "enum" and not f.get("values"):
                errs.append(f"{key}.{name} 是 enum 但没有 values")
    if spec.get("risk") not in ("low", "high"):
        errs.append(f"risk 必须是 low|high，现在是 {spec.get('risk')!r}")
    if not isinstance(spec.get("idempotent"), bool):
        errs.append("idempotent 必须是 boolean")
    return errs


def registry_spec_report(registry) -> dict:
    """注册表级体检：每个能力的 spec 是否声明且合格。
    缺 spec / spec 不合格的能力会被点名——不允许静默回退自由 JSON。"""
    out = {}
    for name, s in registry.skills.items():
        if not is_declared(s):
            out[name] = {"declared": False, "errors": ["未实现 spec()"]}
            continue
        errs = validate_spec(get_spec(s))
        out[name] = {"declared": True, "errors": errs}
    return out


def scan_flow_secrets(flow: dict) -> list:
    """扫整份 flow 有没有明文凭据（编辑器与服务端双重拦截）"""
    import json
    blob = json.dumps(flow, ensure_ascii=False)
    hits = []
    for m in _SECRET_LOOKS.finditer(blob):
        hits.append(m.group(0)[:40])
    return hits


_TYPE_CHECK = {"string": str, "integer": int, "number": (int, float),
               "boolean": bool, "object": dict, "array": list, "any": object}


def validate_inputs(inputs: dict, spec_inputs: dict):
    """按 spec 声明校验计划节点输入：类型/必填/枚举/默认值。
    返回 (clean, errors)——errors 非空则该节点不得执行（指挥官纪律：
    提示词与能力签名不允许漂移）。未声明的键原样透传（spec 未定义即不设防）。"""
    errors = []
    clean = {}
    for name, rule in (spec_inputs or {}).items():
        v = inputs.get(name)
        if v is None and rule.get("default") is not None:
            v = rule["default"]
        if v is None:
            if rule.get("required"):
                errors.append(f"缺少必填输入 {name}")
            continue
        t = rule.get("type", "any")
        if t == "enum":
            if v not in (rule.get("values") or []):
                errors.append(f"{name}={v!r} 不在枚举 {rule.get('values')}")
        else:
            py = _TYPE_CHECK.get(t, object)
            if t == "integer" and isinstance(v, bool):
                errors.append(f"{name} 应为 integer，实际 boolean")
            elif t != "any" and not isinstance(v, py):
                errors.append(f"{name} 应为 {t}，实际 {type(v).__name__}")
        clean[name] = v
    for k, v in (inputs or {}).items():
        if k not in (spec_inputs or {}):
            clean[k] = v          # 未声明键透传，由能力自行决定是否接受
    return clean, errors
