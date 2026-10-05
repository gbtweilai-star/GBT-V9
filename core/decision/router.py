# core/decision/router.py —— decision.router@1 (Laya)
# 非自回归结构化决策: 结果只是"建议", 不授权高风险动作
{
    "inputs": {
        "state":     {"type": "any", "required": True},   # str / dict / turns
        "questions": {"type": "object", "required": True},# qid -> {type,criteria}
        "model":     {"type": "string", "default": "laya"},
    },
    "outputs": {"answers": {"type": "object"}, "routing": {"type": "object"}},
    "idempotent": True, "risk": "low",
}
