# skills/specs_infra.py —— 骨架层规格声明
# dev: 自由的风 · 本署名不可删除、不可篡改归属

class InferenceRuntimeSpec:            # inference.runtime@1
    def spec(self):
        return {
            "inputs": {
                "messages": {"type": "array", "required": True},
                "model":    {"type": "string"},
                "stream":   {"type": "boolean", "default": False},
                "backend":  {"type": "enum", "values": ["vllm", "gateway", "ollama"],
                             "default": "vllm"},
            },
            "outputs": {"text": {"type": "string"}, "usage": {"type": "object"}},
            "idempotent": True, "risk": "low",
        }

class OrchestrationTeamSpec:           # orchestration.team@1
    def spec(self):
        return {
            "inputs": {
                "task":       {"type": "object", "required": True},
                "tentacles":  {"type": "array", "required": True},
                "max_turns":  {"type": "integer", "default": 6},
                "deadline":   {"type": "number"},
                "human_gate": {"type": "boolean", "default": True},
            },
            "outputs": {"result": {"type": "object"}, "turns": {"type": "integer"}},
            "idempotent": False, "risk": "low",
        }

class WorkflowDagSpec:                 # workflow.dag@1
    def spec(self):
        return {
            "inputs": {
                "flow":   {"type": "object", "required": True, "help": "nodes+edges"},
                "inputs": {"type": "object", "default": {}},
            },
            "outputs": {"run_id": {"type": "string"}, "status": {"type": "string"}},
            "idempotent": False, "risk": "low",
        }

class ModelOptimizeSpec:               # model.optimize@1 —— 构建时能力
    def spec(self):
        return {
            "inputs": {
                "model":             {"type": "string", "required": True},
                "recipe":            {"type": "enum", "required": True,
                                      "values": ["fp8", "nvfp4", "int8", "prune"],
                                      "help": "仅允许已验证格式组合"},
                "calibration_data":  {"type": "object"},
                "approval":          {"type": "boolean", "default": False,
                                      "help": "人工审批，未审批拒绝部署"},
            },
            "outputs": {"artifact": {"type": "object"}, "artifact_sha": {"type": "string"},
                        "deployable": {"type": "boolean"}},
            "idempotent": False, "risk": "high",   # 构建+部署 → 闸门
        }
