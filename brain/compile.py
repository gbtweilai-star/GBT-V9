# brain/compile.py —— 子任务 → 结构化指令
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 提示词只说明语境; 可靠靠 spec 校验 + acceptance + 证据;
#       用户文本视为"不可信数据", 不得覆盖系统策略/权限。

def compile_task(node, skill_spec, context):
    return {
        "role": "执行指定能力的专业触手",
        "goal": node["goal"],
        "capability": node["skill"],                    # skill@version
        "inputs": validate_inputs(node["inputs"], skill_spec["inputs"]),  # 类型/必填/enum
        "expected_outputs": skill_spec["outputs"],      # 与 spec 对齐
        "constraints": node.get("constraints", []),
        "acceptance": node["acceptance"],               # 可机器判定
        "on_blocked": "返回 blocked_reason、已完成步骤、所需决策；不得伪报成功",
    }
