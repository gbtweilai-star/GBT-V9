# skills/native_parallel.py —— 对应 Orca 的"扇出并行"
class CodeParallel:
    name, version = "code.parallel", "1.0.0"
    # inputs: {prompt, repo_ref, lanes: int<=N, base_branch}
    # run(): 每 lane 一个隔离工作区(Git worktree 或快照目录) → 并行跑触手
    #        收 diff 摘要 + 交叉互扫结果 → 返回候选池，等审批合并
    # 纪律: 工作区只隔离文件, 不等于安全沙箱 → 权限仍走 risk_gate
