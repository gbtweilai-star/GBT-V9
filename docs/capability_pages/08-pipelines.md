# ⧉ 流水线部署

- **页面 id**：`pipelines`
- **路由**：`/pipelines`
- **分组**：能力
- **一句说明**：四条全自动流水线（短视频/电影/音乐/编程）48 步的分类部署与变更日志

## 这个页面用到的接口

- `/api/pipelines/catalog`
- `/api/pipelines/status`
- `/api/pipelines/journal`

## 绑定的资源（双向的一半）

- 云插件槽
- 库槽
- 触手区间
- 替代实现执行器

## 反向入口（从资源侧回到本页）

- 步骤→/api/pipelines/step
- 变更→/api/pipelines/journal
