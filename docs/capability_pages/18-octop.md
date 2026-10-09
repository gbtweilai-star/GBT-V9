# ◈ Octop 能力桥

- **页面 id**：`octop`
- **路由**：`/octop`
- **分组**：能力
- **一句说明**：339 项 Octop/V9 能力 × 100 触手 1:1 双向绑定（含对称性检查）

## 这个页面用到的接口

- `/api/octop/catalog`
- `/api/octop/state`
- `/api/octop/links`

## 绑定的资源（双向的一半）

- Octop 能力
- 触手
- octop_binding 表

## 反向入口（从资源侧回到本页）

- 能力行→/api/octop/links
- 绑定→/api/octop/bind
