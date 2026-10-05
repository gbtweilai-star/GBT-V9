# skills/native_dispatch.py
class AgentDispatch:
    name, version = "agent.dispatch", "1.0.0"

    def spec(self):
        return {"name": self.name, "version": self.version,
                "inputs": {"type": "object", "properties": {
                    "goal": {"type": "string"},
                    "parent_task_id": {"type": ["string", "null"]},
                    "budget": {"type": "number"},        # 预算上限，超了拒跑
                    "require_approval": {"type": "boolean"}}},
                "outputs": {"type": "object", "properties": {
                    "task_id": {"type": "string"}, "children": {"type": "array"}}},
                "side_effects": ["ledger.write"], "permissions": ["dispatch"]}

    async def run(self, ctx, inputs):
        # 1) 校验 goal/budget  2) 拆分子任务写 agent_tasks
        # 3) 心跳排队唤醒触手, 不发轮询  4) 子任务完成回写父任务
        ...
