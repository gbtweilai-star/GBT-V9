# ☁ 云插件中枢

- **页面 id**：`cloud`
- **路由**：`/cloud`
- **分组**：能力
- **一句说明**：100 个云插件 10×10：开关 / 插入双向绑定 / 内部互绑 / 出网隔离

## 这个页面用到的接口

- `/api/cloud/registry`
- `/api/cloud/state`
- `/api/cloud/links`
- `/api/cloud/speed`

## 绑定的资源（双向的一半）

- 云插件槽
- 触手
- 出网池
- cloud_binding/cloud_share

## 反向入口（从资源侧回到本页）

- 插件格→/api/cloud/links
- 速率→/api/cloud/speed
