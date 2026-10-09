# ◍ 智能体对话

- **页面 id**：`agents`
- **路由**：`/agents`
- **分组**：对话
- **一句说明**：Octop 智能体名册 + 工程对话（点名带专长）+ 多智能体协作工作流图

## 这个页面用到的接口

- `/api/agents/status`
- `/api/agents/roster`
- `/api/agents/workflow`
- `/api/agents/ask`
- `/api/agents/history`

## 绑定的资源（双向的一半）

- Octop 名册
- V9 驱动链
- 对话记录

## 反向入口（从资源侧回到本页）

- 名册→/api/agents/roster
- 对话→/api/agents/ask
