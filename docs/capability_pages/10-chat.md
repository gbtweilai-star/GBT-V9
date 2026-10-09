# 💬 对话

- **页面 id**：`chat`
- **路由**：`/chat`
- **分组**：对话
- **一句说明**：APP 独立多功能对话：会话留得住 + 智能体可点名 + 五种模式（日常/记忆/读数/工作流/工具）

## 这个页面用到的接口

- `/api/chat/sessions`
- `/api/chat/new`
- `/api/chat/send`
- `/api/chat/history`
- `/api/chat/mode`
- `/api/chat/rename`
- `/api/chat/drop`
- `/api/chat/status`

## 绑定的资源（双向的一半）

- 会话存储
- Octop 名册
- 原生大脑
- 工作流
- 只读工具

## 反向入口（从资源侧回到本页）

- 会话→/api/chat/sessions
- 发送→/api/chat/send
- 记忆→/api/brain/ask
