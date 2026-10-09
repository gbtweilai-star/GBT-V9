# 🎬 创作工坊

- **页面 id**：`studio`
- **路由**：`/studio`
- **分组**：指挥
- **一句说明**：一条链出片：台湾腔旁白 → 本地配乐 → 字幕/口型轴 → ffmpeg 成片（全本地零付费）

## 这个页面用到的接口

- `/api/studio`
- `/api/studio/make`
- `/api/studio/file/`
- `/api/lipsync`

## 绑定的资源（双向的一半）

- 创作链 core.studio
- edge-tts 台湾腔
- 本地配乐算法
- ffmpeg 合成
- 动作件 anim_v

## 反向入口（从资源侧回到本页）

- 创作链→core/studio.py
- 口型轴→/api/lipsync
- 成品→/api/studio/file/
