# 帧证据契约首跑预检

## 常见首跑失败与修法
1. 迁移没跑 → `_sanity_check` 报 `Required table missing after migrations`。
   确保 adapter_factory 调的是真的 `migrations.runner.apply_pending(db)`，且
   两个方言都建了 artifact_objects / frame_leases / frame_verification_evidence。
2. 测试 SQL 用 `?` 而 PG 要 `$n` → 靠 `_translate_qmarks` 转；注意 PG 的 JSONB `?`
   键存在运算符会被误当占位符，改用 `jsonb_exists()`。
3. asyncpg 拒绝 `?sslmode=disable` → DSN 不带 query 参数（本脚本已如此）。
4. SQLite `:memory:` 各连接看到不同库 → 每测试用真实临时文件（`tmp_path`）。
5. 嵌套 `db.transaction()` 报错 → 参考实现故意不支持嵌套，别套事务。
6. 事务外调 `lock_*` → 抛 `RuntimeError`，锁只在 `async with db.transaction(...)` 内用。
7. pytest-asyncio 缺配置 → `pyproject.toml` 加 `asyncio_mode="auto"` +
   `asyncio_default_fixture_loop_scope="function"`。
8. `put_verified` 拒绝 ref → 要求 `ref==sha256(content)`；任意 ref 的测试直接
   `store.objects[ref]=content` 只塞字节。
9. PG 跨测试污染 → 唯一 ref + 前缀清理（agent 工厂/本脚本用一次性库隔离）。
10. 文件库 WAL 失败 → 本地文件系统必须支持 `PRAGMA journal_mode=WAL`，别静默忽略。
