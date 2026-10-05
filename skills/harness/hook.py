# skills/harness/hook.py —— harness.hook@1 (ECC)
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 蒸馏点: 只取 hook 事件契约与策略执行, 不搬入整套 harness 配置
{
    "inputs": {
        "harness": {"type": "enum", "required": True,
                    "values": ["claude_code", "codex", "cursor"]},
        "event":   {"type": "string", "required": True},   # 如 PreToolUse
        "payload": {"type": "object", "required": True},   # stdin JSON
    },
    "outputs": {"decision": {"type": "enum", "values": ["allow", "deny", "review"]},
                "reason": {"type": "string"}, "audit_id": {"type": "string"}},
    "idempotent": True, "risk": "low",
}
