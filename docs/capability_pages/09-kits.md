# ◫ 三套工具包

- **页面 id**：`kits`
- **路由**：`/kits`
- **分组**：能力
- **一句说明**：剪映式 / Qwen-Image / ComfyUI 式三套包全走云插件，配置白名单校验

## 这个页面用到的接口

- `/api/kits/catalog`
- `/api/kits/status`
- `/api/kits/journal`
- `/api/kits/validate`

## 绑定的资源（双向的一半）

- 云插件槽
- 库槽
- 本机免费通道
- 替代实现执行器

## 反向入口（从资源侧回到本页）

- 配置→/api/kits/config
- 校验→/api/kits/validate
