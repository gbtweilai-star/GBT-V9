# ▣ 总控台

- **页面 id**：`console`
- **路由**：`/`
- **分组**：指挥
- **一句说明**：全站入口：身体启动检查、告警、编队与快照一屏看

## 这个页面用到的接口

- `/api/health`
- `/api/state`
- `/api/backend`
- `/api/panel/overview`
- `/api/panel/alerts`
- `/api/fleet/status`
- `/api/fleet/drives`
- `/api/senses`
- `/api/jobs`
- `/api/media/vram`

## 绑定的资源（双向的一半）

- 触手编队
- 身体账本
- 告警状态机
- 只读工具快照

## 反向入口（从资源侧回到本页）

- 编队卡→/api/fleet/status
- 告警卡→/api/panel/alerts
- 快照卡→/api/body/tools
