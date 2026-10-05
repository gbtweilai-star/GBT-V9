# workflows/validate_spec.py —— 规格级校验（并入 engine.validate）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from skills.spec import get_spec, is_declared, check_secret_field

_TYPE_OK = {
    ("string", "string"), ("number", "number"), ("integer", "integer"),
    ("boolean", "boolean"), ("object", "object"), ("array", "array"),
}


def _err(code, node_id=None, edge_id=None, path=None, msg=""):
    return {"code": code, "node_id": node_id, "edge_id": edge_id,
            "path": path, "message": msg}


def check_node_args(node, registry):
    """节点参数校验：必填/类型/枚举/secret 明文"""
    errs, warns = [], []
    skill = registry.get(node["skill"])
    if not is_declared(skill):
        warns.append(_err("schema_undeclared", node["id"],
                          msg="未声明 schema，无法做类型校验"))
        return errs, warns
    spec = get_spec(skill)
    inputs, args = spec.get("inputs", {}), (node.get("inputs") or {})
    for name, f in inputs.items():
        val = args.get(name)
        if f.get("required") and val in (None, ""):
            errs.append(_err("required_missing", node["id"], path=name,
                             msg=f"{name} 为必填"))
            continue
        if val is None:
            continue
        t = f.get("type")
        if t == "enum" and val not in f.get("values", []):
            errs.append(_err("enum_invalid", node["id"], path=name,
                             msg=f"{name} 取值须在 {f.get('values')}"))
        if f.get("secret"):
            ok, why = check_secret_field(val)
            if not ok:
                errs.append(_err("plaintext_secret", node["id"], path=name, msg=why))
    if spec.get("risk") == "high" and not node.get("gate"):
        errs.append(_err("high_risk_gate_required", node["id"],
                         msg="高风险节点必须配置 gate 人工闸门"))
    return errs, warns


def check_edge_types(edge, nodes, registry):
    """仅当两端都声明 schema 才比类型；否则返回'未校验'警告"""
    src, dst = nodes[edge["from"]], nodes[edge["to"]]
    ss, ds = registry.get(src["skill"]), registry.get(dst["skill"])
    if not (is_declared(ss) and is_declared(ds)):
        return [], [_err("edge_unchecked", edge_id=edge["id"],
                         msg="一端未声明 schema，连线类型未校验")]
    o = get_spec(ss).get("outputs", {}).get(edge.get("from_port"), {})
    i = get_spec(ds).get("inputs", {}).get(edge.get("to_port"), {})
    ot, it = o.get("type", "any"), i.get("type", "any")
    if ot == "any" or it == "any":
        return [], [_err("edge_unchecked", edge_id=edge["id"], msg="any 端口不校验")]
    if ot != it:
        return [_err("port_type_mismatch", edge_id=edge["id"],
                     msg=f"输出 {ot} → 输入 {it} 类型不匹配")], []
    return [], []
