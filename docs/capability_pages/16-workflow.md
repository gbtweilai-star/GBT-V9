# ⛓ 工作流

- **页面 id**：`workflow`
- **路由**：`/workflow`
- **分组**：指挥
- **一句说明**：工作流独立页：多智能体协作编排 + 市场调研前置闸门 + 逐段验收标准（不盲推）

## 这个页面用到的接口

- `/api/workflows/status`
- `/api/workflows/catalog`
- `/api/workflows/graph`
- `/api/workflows/acceptance`
- `/api/workflows/research`
- `/api/workflows/research/submit`
- `/api/workflows/advance`

## 绑定的资源（双向的一半）

- 工作流注册表
- 市场调研闸门
- 触手班分工
- 验收标准

## 反向入口（从资源侧回到本页）

- 编排图→/api/workflows/graph
- 调研→/api/workflows/research
- 验收→/api/workflows/acceptance
