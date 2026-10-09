# 🎙 语音操控

- **页面 id**：`voice`
- **路由**：`/voice`
- **分组**：对话
- **一句说明**：数字人交互式语音操控中心：说一句 → 分意图（给依据）→ 执行 → 台湾腔回话 → 留痕

## 这个页面用到的接口

- `/api/voice/command`
- `/api/voice/hear`
- `/api/voice/center/status`
- `/api/avatar/state`

## 绑定的资源（双向的一半）

- 原生大脑
- 动手入口
- 台湾腔通道
- 本机免费听写

## 反向入口（从资源侧回到本页）

- 执行→/api/voice/command
- 收音→/api/voice/hear
