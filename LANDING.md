# LANDING.md — 帧证据链落盘总纲 (v3)

> v3 新增：覆盖率统计与面板（`coverage_report.py` / `coverage_snapshots` v3 迁移 /
> `panel/routes/coverage.py` / `coverage.js`）、CI 覆盖率门禁（PR 告警、夜间卡阈值）、
> acceptance.sh 覆盖率开关。
> 图例：✅ 代码完整(待跑绿) · 🟨 草案 · 🟩 测试 · 🔌 CI/脚本 · ⬜ 现有/待接

## 1. 目录树

​```text
gbt-potato-v9/
├── body/
│   ├── ports/                     # ✅ 契约边界(Protocol, 仅 typing)
│   │   ├── __init__.py · types.py · db.py · leases.py · store.py
│   │   ├── queue.py · timeline.py · executor.py · devour.py
│   │   └── brain.py · policy.py
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── base_db.py             # ✅ ? -> $n 引号安全转换 + 事务连接绑定(ContextVar)
│   │   ├── sqlite_db.py           # ✅ SqliteDb(BEGIN IMMEDIATE, WAL 强校验)
│   │   ├── postgres_db.py         # ✅ PostgresDb(asyncpg, FOR UPDATE / advisory)
│   │   ├── local_store.py         # ✅ 内容寻址本地存储(暂存+原子重命名)
│   │   └── r2_store.py            # ✅ 本地 + S3/R2 存储
│   ├── evidence_leases.py         # ✅ LeaseSession + put_and_hold(staged→ready+lease)
│   ├── evict.py                   # ✅ Evictor + StagingReclaimer(与租约共用同一行锁)
│   ├── evidence_row.py            # ✅ write_evidence/read_evidence/attach_verification_time
│   ├── delta_kernel.py            # 🟨 ΔE 内核 + heatmap_png(与 effect_metrics 同源)
│   ├── devour_frames.py           # 🟨 snapshot_at/frame_diff_refs/color_metrics_delta
│   ├── devour_wrappers.py         # 🟨 verification_times/verdict_and_spec/evidence_row
│   ├── fingerprint.py             # 🟨 encode_fingerprint
│   ├── calibration.py             # 🟨 load/save + 状态判定
│   └── history.py                 # 🟨 body_snapshot_history
├── migrations/
│   ├── ddl.py                     # ✅ 三表+coverage_snapshots + 9 索引
│   ├── runner.py                  # ✅ apply_pending(SQL|callable) + boot_check(期望版本=3)
│   ├── m2026_operation_v2.py      # ✅ 加 operation 列+回填 'legacy'+索引(幂等)
│   └── m2026_coverage_v3.py       # ✅ 建 coverage_snapshots + 索引(幂等)   ← v3 新增
├── actuator/
│   ├── editor_touch.py · editor_actions.py · editor_verify.py   # 🟨
├── panel/
│   ├── routes/
│   │   ├── frame_evidence.py      # ✅ 列表(游标+HMAC)/详情/取帧(租约)/diff 热图
│   │   ├── coverage.py            # ✅ /api/coverage(总览+最近+趋势+详情)   ← v3 新增
│   │   └── calibrations.py        # 🟨
│   └── static/
│       ├── frame_evidence.js      # ✅ 列表/详情/对比 + 导航栈 + 深链 + 409 徽标
│       ├── coverage.js            # ✅ 覆盖率卡片(整体/模块/趋势/下钻)      ← v3 新增
│       ├── nav_stack.js · compare_overlay.js · calibration.js   # 🟨
├── skills/
│   └── video_edit.py              # 🟨 run + run_edit_op
├── tests/
│   ├── adapter_factory.py         # 🟩 工厂(已接 Evictor/Reclaimer + alert)
│   └── contracts/
│       ├── test_frame_evidence_contract.py  # 🟩 回滚/全有或全无/竞态唯一胜者/staging 顺序
│       ├── test_staging_reclaimer.py        # 🟩 续期/过期回收/删失败回滚
│       ├── test_evictor_watermark.py        # 🟩 水位驱逐/pinned 阻塞告警
│       └── test_lease_eviction.py           # 🟩 lease-vs-eviction / 批量全有或全无
├── scripts/
│   ├── acceptance.sh              # 🔌 本地验收(--no-coverage / COVERAGE_THRESHOLD / COVERAGE_DB_URL)
│   ├── acceptance_preflight.py    # 🔌 迁移/建表预检
│   └── coverage_report.py         # 🔌 覆盖率汇总+门禁+可选写库            ← v3 新增
├── .github/workflows/
│   ├── contracts.yml              # 🔌 可复用：SQLite 矩阵 + PG16；coverage 输入/密钥；覆盖率报告步骤
│   └── contracts-nightly.yml      # 🔌 每日 03:00；夜间 threshold=80 + enforce；issue 含失败用例/coverage/日志
├── pyproject.toml / pytest.ini    # 🔌 asyncio 配置 + coverage source/addopts
├── MAPPING.md                     # 🟨 grep 映射 runbook
├── PREFLIGHT.md                   # ✅ 首跑 10 个坑与修法
└── LANDING.md                     # 本文
​```

## 2. 数据表

| 表 | 关键列 | 用途 |
|---|---|---|
| `artifact_objects` | ref(PK) · state · bytes · created_epoch · last_access_epoch · staged_by · staged_until_epoch | 帧工件状态与水位计量(含 staged) |
| `frame_leases` | lease_id(PK) · ref · holder · purpose · acquired_epoch · expires_epoch · renew_count · status | 证据帧租约，pin 住不许删 |
| `frame_verification_evidence` | id(PK) · ref · baseline_ref · result_ref · raw_available · verification_times · color_metrics_delta · effect_metrics · delta_metrics · metrics · verdict_and_spec · created_epoch · calibration_key · calibration_fingerprint · fingerprint · operation | 验证证据与结论(JSON 列存文本) |
| `coverage_snapshots` | snapshot_id(PK) · backend · generated_epoch · overall_percent · modules_json · files_json · lines_total · lines_covered · threshold · passed · commit_sha · run_url | 覆盖率快照（按 backend 分开） |

索引：artifact_objects(state,last_access_epoch)/(state,staged_until_epoch)；
frame_leases(ref,status,expires_epoch)/(status,expires_epoch)；
frame_verification_evidence(ref)/(baseline_ref)/(result_ref)/(operation,created_epoch DESC,id DESC)；
coverage_snapshots(backend, generated_epoch DESC)。

## 3. 迁移

- v1 `ddl.py`：建四表 + 索引（新库直接含 operation 与 coverage_snapshots）。`FRAME_EVIDENCE_VERSION = 3`。
- v2 `m2026_operation_v2.py`：老库加 `operation`（列存在检查）→ 回填 `NULL/'' → 'legacy'` → 建索引。
- v3 `m2026_coverage_v3.py`：建 `coverage_snapshots` 表 + 索引（表存在检查 + 必需列校验，幂等）。
- `runner.py`：每迁移一事务 + `db.lock_key('migrations')`；支持 SQL 序列或 `async def apply(db)`；`boot_check` 期望 `max(version)`。

## 4. 契约测试（四类边界，双后端）

| 套件 | 锁定 |
|---|---|
| test_frame_evidence_contract.py | 事务回滚 / 全有或全无 / 竞态唯一胜者 / staging 顺序 |
| test_staging_reclaimer.py | 续期 → 过期才回收 → 删失败回 staged 延后重试 |
| test_evictor_watermark.py | 老优先驱逐到水位 / staged 只计量不回收 / 全 pin 报 blocked_pinned |
| test_lease_eviction.py | 同对象行只有一个赢家 / 不留 deleting 幽灵 / 批量全有或全无 / 不相互死锁 |

## 5. 覆盖率

- 采集：`pytest --cov=body --cov=migrations --cov=panel --cov=skills --cov-report=json:coverage.json`
  （`--cov` 必须与 `[tool.coverage.run] source` 对齐，漏写会让该包**不出现在报告**里）。
- 汇总门禁：`python scripts/coverage_report.py --coverage coverage.json --backend <sqlite|postgres> --out coverage_summary.json --threshold <N> [--db DSN]`
  非零退出 = 低于阈值；`--db` 给时才写 `coverage_snapshots`。
- 展示：`GET /api/coverage`（各后端最新 + 最近列表）、`/api/coverage/series`（趋势）、`/api/coverage/{snapshot_id}`（模块/文件级 + 缺行）；表不存在时返回 `table_missing: true` 不 500。
- **SQLite 与 PG 各一条 backend 标签快照，分开展示，不合并成单一数字。**

## 6. CI 与门禁

- `contracts.yml`（可复用）：`workflow_call` 输入 `coverage_threshold`(默认 `"0"`) + `enforce_coverage`(默认 `false`)，密钥 `coverage_db_url`(可选)。
  - 两个 job 都跑 `--cov*`；新增 `Coverage report` 步骤（`PIPESTATUS` 取退出码）：`enforce_coverage==true` 时低于阈值 `::error::`+失败，否则 `::warning::` 继续。
  - 产物含 `pytest.log` / `report.xml` / `coverage.json` / `coverage_summary.json` / `coverage-report.log`。
- `contracts-nightly.yml`：`with: {coverage_threshold: "80", enforce_coverage: true}` 调可复用工作流；失败时 issue 附**失败用例名 + Coverage 摘要 + 日志尾部**；成功自动关 issue。
- **门禁策略**：PR/push 只告警（默认 threshold 0 / enforce false）；夜间阈值 80% 且强制（测试失败或覆盖率不达标都失败）。

## 7. 环境变量

​```ini
FRAME_EVIDENCE_CURSOR_SECRET=        # 面板必设：稳定随机，否则重启后旧游标 400
TEST_DATABASE_URL=                   # 一次性库，禁止带 ?sslmode=（asyncpg 不认）
RUN_PG_CONTRACTS=1
COVERAGE_THRESHOLD=80                # 本地/CI 覆盖率阈值
COVERAGE_DB_URL=                     # 可选 PG DSN，用于写 coverage_snapshots(CI 里是 secret)
ALERT_WEBHOOK_URL=                   # 可选：Slack/Discord 兼容 JSON
NIGHTLY_ALERT_ASSIGNEE=              # 可选变量：开 issue 时 @ 并指派
# 运行期策略
LEASE_TTL_S=60 · STAGING_TTL_S=900 · STAGING_RETRY_DELAY_S=60
EVICT_GRACE_S=60 · HIGH_WATER=… · EVICT_BATCH=500
