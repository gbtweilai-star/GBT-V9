# ⛭ 数据中枢

- **页面 id**：`hub`
- **路由**：`/hub`
- **分组**：指挥
- **一句说明**：唯一数据源：页面登记/能力图/部件读数/布局/盲区 + 主动汇报/长任务/镜像/信息素

## 这个页面用到的接口

- `/api/hub/snapshot`
- `/api/hub/widgets`
- `/api/hub/scan`
- `/api/hub/layout`
- `/api/sched/tick`
- `/api/proactive/feed`
- `/api/longrun/status`
- `/api/mirror/status`
- `/api/stigmergy/status`

## 绑定的资源（双向的一半）

- 页面登记
- 能力图
- 部件读数
- 主动汇报
- 长任务
- 镜像排练
- 信息素场地

## 反向入口（从资源侧回到本页）

- 部件读数→/api/hub/widgets
- 盲区→/api/hub/scan
- 推进心跳→/api/sched/tick
- 主动扫一遍→/api/proactive/scan
