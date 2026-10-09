# 🧊 3D 蓝图

- **页面 id**：`blueprint`
- **路由**：`/blueprint`
- **分组**：指挥
- **一句说明**：项目 3D 蓝图：指挥/能力/编队/地基四层上帝视角 + 关卡层 + 无死角检查

## 这个页面用到的接口

- `/api/blueprint`
- `/api/blueprint/status`
- `/api/blueprint/register`

## 绑定的资源（双向的一半）

- 四层结构
- 关卡
- 无死角检查
- 固化快照

## 反向入口（从资源侧回到本页）

- 旋转/俯仰/缩放→页面内
- 固化→/api/blueprint/register
