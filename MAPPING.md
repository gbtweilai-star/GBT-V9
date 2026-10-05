# MAPPING.md — grep 映射 runbook（现场对齐用）
dev: 自由的风 · 本署名不可删除、不可篡改归属

> 用途：在**你的真实工程**里执行下面的 grep，把结果填进「现场名」列；
> 与 V9 代码里的期望名不一致的，就按映射表改 V9 侧（或加适配器）。
> 只需改表里列出的「接线点」，业务规则不用动。

## 1. 契约方法名（ports → 现场实现）

| V9 期望名 | 现场 grep | 现场名 | 处理 |
|---|---|---|---|
| `Db.transaction(immediate=)` | `grep -rn "def transaction" --include=*.py` | | 无则加包装 |
| `Db.lock_artifact(ref)` | `grep -rn "lock_artifact\|FOR UPDATE" --include=*.py` | | PG: SELECT..FOR UPDATE |
| `Db.transition(ref,src,dst,guard=)` | `grep -rn "def transition" --include=*.py` | | 条件 UPDATE |
| `Db.db_now_epoch()` | `grep -rn "db_now_epoch\|now()" --include=*.py` | | 用库时钟，禁本地钟 |
| `EvidenceLeases.hold / acquire_many_atomic` | `grep -rn "put_and_hold\|acquire_many_atomic"` | | 全有或全无 |
| `ArtifactStore.put_verified/get_bytes` | `grep -rn "put_verified\|get_bytes"` | | 内容寻址 |
| `ExclusiveQueue.exclusive` | `grep -rn "def exclusive" --include=*.py` | | 单实例锁 |
| `Timeline.revision/bump_revision/media_at` | `grep -rn "bump_revision\|media_at"` | | |
| `Executor.focus/locate_*/click/drag/time_to_x` | `grep -rn "locate_uia\|drag_playhead\|time_to_x"` | | |
| `Devour.snapshot_at/frame_diff_refs/...` | `grep -rn "snapshot_at\|frame_diff_refs"` | | |
| `Brain.report_blocked` | `grep -rn "report_blocked"` | | |
| `Policy.for_op / Settings.get` | `grep -rn "for_op\|class Settings"` | | |

## 2. 面板接线点

| V9 期望 | 现场 grep | 现场名 | 处理 |
|---|---|---|---|
| `panel.deps.db`（模块级适配器） | `grep -rn "get_db\|DATABASE_URL" panel/` | | 改 deps.py 构造 |
| `leader_jobs.register` | `grep -rn "leader_jobs\|leader" --include=*.py` | | 无则用 asyncio 循环 |
| `director.notify(kind, severity, say=)` | `grep -rn "def notify"` | | 语音播报入口 |
| `upsert_state(db, kind, value)` | `grep -rn "upsert_state\|voice_state"` | | v6 表 voice_state |

## 3. 环境变量（.env）

| 变量 | 用途 | 默认 |
|---|---|---|
| `DATABASE_URL` | PG DSN（不设则 SQLite） | — |
| `LEDGER_BACKEND` | `sqlite` 强制本地库 | 自动 |
| `GBT_DB_PATH` | SQLite 文件路径 | `data/gbt_v9.sqlite3` |
| `GBT_STORE_ROOT` | 本地内容寻址存储根 | `data/artifacts` |
| `LEASE_TTL_S` / `STAGING_TTL_S` | 租约/暂存 TTL | 120 / 900 |
| `FRAME_EVIDENCE_CURSOR_SECRET` | 游标 HMAC 密钥（生产必须改） | dev 占位 |
| `COVERAGE_THRESHOLD` / `COVERAGE_DB_URL` | 覆盖率门禁/写库 | 80 / — |

## 4. 填表模板（复制到现场记录）

```
[现场] 数据库: ______ (sqlite/pg)   DSN来源: ______
[现场] 队列/单实例锁实现: ______     位置: ______
[现场] 时间线(revision/media_at)来源: ______
[现场] UI 执行器: ______ (pywinauto/uia / ADB / DOM)
[现场] 存储: ______ (local/R2)   bucket: ______
[现场] 语音播报入口: ______
[差异清单] V9名 ↔ 现场名 的替换位置：
  1) ______
  2) ______
```
