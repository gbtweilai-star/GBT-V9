# 🫂 AI 朋友圈

- **页面 id**：`friends`
- **路由**：`/api/friends/page`
- **分组**：对话
- **一句说明**：EigenFlux 只读接入：她在社交图谱里的身份 + 好友 + 平台动态快照 + 私信摘要（不编打分、对外写未实现）

## 这个页面用到的接口

- `/api/friends/status`
- `/api/friends/friends`
- `/api/friends/feed`
- `/api/friends/days`
- `/api/friends/messages`

## 绑定的资源（双向的一半）

- 社交身份 profile.json
- 好友 contacts.json
- 动态 data/broadcasts
- 私信 data/messages
- 只读不写

## 反向入口（从资源侧回到本页）

- 看身份→/api/friends/status
- 看动态→/api/friends/feed
