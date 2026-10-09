# ▥ 媒体监控

- **页面 id**：`media`
- **路由**：`/media`
- **分组**：设备
- **一句说明**：生成队列：深度/等待/失败率/显存/死信/事件流

## 这个页面用到的接口

- `/api/media/queue/stats`
- `/api/media/vram`
- `/api/media/dead`
- `/api/media/terminal-events`

## 绑定的资源（双向的一半）

- 媒体队列
- 显存预算（本地 0）
- 云主管道

## 反向入口（从资源侧回到本页）

- 队列→/api/media/queue/stats
- 死信→/api/media/dead
