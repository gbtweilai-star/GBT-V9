# core/workspaces/isolate.py —— workspace.isolate@1 (Orca)
# 只做 worktree 生命周期, 不创建/分派任务
{
    "inputs": {
        "action":    {"type": "enum", "required": True,
                      "values": ["create", "status", "diff", "cleanup"]},
        "repo_root": {"type": "string", "required": True},
        "task_id":   {"type": "string", "required": True},
    },
    "outputs": {"workspace_id": {"type": "string"}, "path": {"type": "string"},
                "state": {"type": "string"}, "diff": {"type": "object"}},
    "idempotent": False, "risk": "high",   # 创建/清理工作区 → 闸门
}
