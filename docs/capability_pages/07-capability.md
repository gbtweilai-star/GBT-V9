# ▦ 总能力/连接

- **页面 id**：`capability`
- **路由**：`/capability`
- **分组**：能力
- **一句说明**：总能力图表 + 连接状态 + 精准用量 + 生产就绪度根因台账 + 固化回滚

## 这个页面用到的接口

- `/api/capability/chart`
- `/api/capability/usage`
- `/api/capability/detail`
- `/api/capability/solid`
- `/api/compute/route`
- `/api/compute/vram`
- `/api/production`

## 绑定的资源（双向的一半）

- 云插件槽
- 数据库槽
- Octop 能力
- 算力路由
- 固化档案

## 反向入口（从资源侧回到本页）

- 条形→/api/capability/detail
- 用量→/api/capability/usage
- 台账→/api/production
