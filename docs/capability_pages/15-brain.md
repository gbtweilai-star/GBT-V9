# 🧠 原生大脑

- **页面 id**：`brain`
- **路由**：`/brain`
- **分组**：指挥
- **一句说明**：统一记忆（主脑+触手+用户+系统）+ 生命起源存档 + 元认知 + 热度褪色 + 提醒 + 隐私回收站

## 这个页面用到的接口

- `/api/brain/status`
- `/api/brain/capture`
- `/api/brain/ask`
- `/api/brain/memories`
- `/api/brain/life`
- `/api/brain/metacog`
- `/api/brain/nudges`
- `/api/brain/consolidate`
- `/api/brain/unify`

## 绑定的资源（双向的一半）

- 统一记忆库
- 生命起源存档
- 元认知
- 热度引擎
- 回收站

## 反向入口（从资源侧回到本页）

- 捕捉→/api/brain/capture
- 提问→/api/brain/ask
- 生平→/api/brain/life
- 元认知→/api/brain/metacog
