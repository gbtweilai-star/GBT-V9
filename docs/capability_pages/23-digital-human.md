# ☺ 数字人

- **页面 id**：`digital-human`
- **路由**：`/digital-human`
- **分组**：设备
- **一句说明**：身体自己说：实时流 + 见证播报 + 工具问询

## 这个页面用到的接口

- `/api/digital-human/stream`
- `/api/digital-human/witness`
- `/api/digital-human/tools`

## 绑定的资源（双向的一半）

- 语音总线
- 情绪源
- 证词播报

## 反向入口（从资源侧回到本页）

- 实时流→/api/digital-human/stream
- 见证→/api/digital-human/witness
