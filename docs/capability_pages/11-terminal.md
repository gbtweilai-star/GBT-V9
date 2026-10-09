# 🖥 AI 终端

- **页面 id**：`terminal`
- **路由**：`/terminal`
- **分组**：对话
- **一句说明**：终端形态的指挥入口：白名单命令派发（help/ask/brain/remember/agents/workflows/tools）

## 这个页面用到的接口

- `/api/terminal/run`
- `/api/terminal/history`
- `/api/terminal/help`

## 绑定的资源（双向的一半）

- 原生大脑
- 指挥读数链
- 工作流闸门
- 只读工具
- 命令历史

## 反向入口（从资源侧回到本页）

- 执行→/api/terminal/run
- 历史→/api/terminal/history
