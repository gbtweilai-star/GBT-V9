# security/audit_pipeline/audit.py —— security.audit@1
{
    "inputs": {
        "root":            {"type": "string", "required": True},
        "scope":           {"type": "object"},
        "sandbox_profile": {"type": "enum", "values": ["restricted", "none"],
                            "default": "restricted"},
    },
    "outputs": {"findings": {"type": "array"}, "coverage": {"type": "object"},
                "reports": {"type": "object"}, "status": {"type": "string"}},
    "idempotent": False, "risk": "high",   # 沙箱内执行目标代码 → 闸门
}
