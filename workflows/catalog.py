# workflows/catalog.py —— 由能力注册表生成可拖拽节点目录
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from skills.spec import get_spec, is_declared

CATEGORY = [
    ("engineering.", "工程规则"), ("coder.", "编程引擎"), ("diagram", "图表"),
    ("image.", "图像"), ("voice.", "语音"), ("rules", "工程规则"),
    ("inference.", "骨架·推理"), ("orchestration.", "骨架·编排"),
    ("workflow.", "骨架·工作流"), ("model.optimize", "骨架·构建"),
]

# 内置控制节点（无 schema，但结构固定）
CONTROL_NODES = [
    {"id": "branch", "label": "分支", "kind": "branch", "color": "#ffbd59",
     "schema_declared": True,
     "inputs": {"when": {"type": "any", "required": True,
                         "help": "条件（可用 $ref:节点.output）"}},
     "outputs": {"ok": {"type": "boolean"}}, "risk": "low", "idempotent": True},
    {"id": "map", "label": "遍历", "kind": "map", "color": "#ffbd59",
     "schema_declared": True,
     "inputs": {"over": {"type": "array", "required": True},
                "skill": {"type": "string", "required": True},
                "item": {"type": "any"}},
     "outputs": {"output": {"type": "array"}}, "risk": "low", "idempotent": False},
    {"id": "gate", "label": "人工闸门", "kind": "gate", "color": "#ff5d73",
     "schema_declared": True, "inputs": {}, "outputs": {"ok": {"type": "boolean"}},
     "risk": "high", "idempotent": True},
]


def _category(name: str) -> str:
    for prefix, cat in CATEGORY:
        if name.startswith(prefix):
            return cat
    return "其他"


def build_catalog(registry) -> dict:
    nodes = []
    health = getattr(registry, "_health", {}) or {}
    for name, s in registry.skills.items():
        spec = get_spec(s)
        declared = is_declared(s)
        h = health.get(name)
        nodes.append({
            "id": name, "label": name, "kind": "skill",
            "version": getattr(s, "version", "?"),
            "category": _category(name),
            "schema_declared": declared,
            "inputs": spec.get("inputs", {}),
            "outputs": spec.get("outputs", {}),
            "risk": spec.get("risk", "low"),
            "idempotent": spec.get("idempotent", None),
            "available": (bool(h.ok) if h is not None else None),
            "unavailable_reason": (h.reason if h is not None and not h.ok else ""),
        })
    return {"nodes": nodes + CONTROL_NODES}


def spec_of(registry, skill_name: str) -> dict:
    if skill_name in {c["id"] for c in CONTROL_NODES}:
        return next(c for c in CONTROL_NODES if c["id"] == skill_name)
    s = registry.get(skill_name)
    if not s:
        return {}
    return {"id": skill_name, "label": skill_name, "kind": "skill",
            "schema_declared": is_declared(s), "inputs": get_spec(s).get("inputs", {}),
            "outputs": get_spec(s).get("outputs", {}),
            "risk": get_spec(s).get("risk", "low")}
