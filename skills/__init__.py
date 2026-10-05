# skills/__init__.py —— 原生能力清单（唯一权威）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
NATIVE_CAPABILITIES = {
    "engineering.rules@1": {"cls": "skills.rules:EngineeringRules",   "backend": None,          "kind": "kernel"},
    "coder.engine@1":      {"cls": "skills.engine:CoderRouter",       "backend": "codex/brain", "kind": "engine"},
    "diagram@1":           {"cls": "skills.diagram:DiagramSkill",     "backend": "archify",     "kind": "workflow"},
    "image.prompt@1":      {"cls": "skills.imagegen:ImageSkill",      "backend": "image-api",   "kind": "compiler"},
    "voice.io@1":          {"cls": "senses.voice:VoiceAdapter",       "backend": "voicestudio", "kind": "sensory"},
}


# 骨架/基建层（运行时骨架 + 构建时能力，不与业务技能混层）
INFRA_CAPABILITIES = {
    "inference.runtime@1":  {"cls": "core.inference.backends:InferenceRouter",
                             "backend": "vllm/gateway/ollama", "kind": "infra/runtime"},
    "orchestration.team@1": {"cls": "core.orchestration.team:Orchestrator",
                             "backend": "builtin", "kind": "infra/runtime"},
    "workflow.dag@1":       {"cls": "workflows.engine:WorkflowEngine",
                             "backend": "builtin", "kind": "infra/runtime"},
    "model.optimize@1":     {"cls": "build.model_optimize.pipeline:ModelBuildPipeline",
                             "backend": "nvidia-modelopt", "kind": "infra/build"},
}
