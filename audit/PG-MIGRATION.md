# 账本迁移方案：SQLite → PostgreSQL（支持真并发写）

> 结论：迁移是**四步加法**，不是推倒重来。SQLite 侧零改动继续可用；
> PG 侧建表幂等、回填幂等（`event_id` 唯一键）、切换只改两个环境变量。
> 每一步都有独立验证点，任何一步失败都能停在原地回滚。

---

## 一、组件地图（都已落盘、已实测）

| 文件 | 职责 | 状态 |
|---|---|---|
| `audit/schema_pg.sql` | PG 建表 DDL：`ledger` / `cross_tasks` / `cross_arbitration`（幂等 `IF NOT EXISTS`） | ✅ |
| `audit/ledger_pg.py` | `PGLedger`：连接池 + `event_id` 幂等 + `SKIP LOCKED` 领取；`PoolMetrics` 池指标；`ServerProbe` 服务端探针 | ✅ 池/探针已接面板 |
| `audit/migrate_sqlite_to_pg.py` | ① 一次性回填（批量 2000 行，`ON CONFLICT DO NOTHING` 可重跑）② `DualLedger` 双写过渡包装 | ✅ 双写已实测 |
| `audit/ledger_factory.py` | 环境变量开关：`make_ledger()` / `backend_info()`（`LEDGER_BACKEND=sqlite|pg`） | ✅ |
| `senses/sqldialect.py` | 传感器表双方言适配（`?` vs `%s`、`REAL` epoch vs `TIMESTAMPTZ`、cursor 上下文兼容） | ✅ |
| `panel/server.py` | `/api/backend`（池快照+告警）· `/api/scale`（用量/斜率/ETA/分区） | ✅ 已验证 |

**并发语义差异（为什么值得迁）**

| 维度 | SQLite（现状） | PostgreSQL（目标） |
|---|---|---|
| 并发写 | 单写锁 + WAL + `busy_timeout`，多线程退避容忍 | 32 连接池真并发，无锁竞争排队 |
| 幂等 | 靠时间戳，天然允许重复行 | `event_id UUID UNIQUE` + `ON CONFLICT DO NOTHING` |
| 复核领取 | 单机事务内 `UPDATE ... WHERE state='pending'` | `FOR UPDATE SKIP LOCKED`，多机无争抢 |
| 扩容 | 文件增长，靠本地分区/水位 | 分区表 + `pg_inherits` 清单，面板可读 ETA |
| 单控制器 | 单机天然单控 | `pg_try_advisory_lock(0x5CA1E)` + `scaler_state` 行 |

---

## 二、迁移四步（每步可停、可验、可回滚）

### 第 0 步 · 准备（不动生产）
```bash
# 只读盘点：确认 SQLite 现状与体量
python - <<'PY'
import sqlite3; c = sqlite3.connect("tentacle_ledger.db")
print(c.execute("SELECT COUNT(*) FROM ledger").fetchone()[0], "行")
print(c.execute("SELECT status, COUNT(*) FROM ledger GROUP BY status").fetchall())
PY
```
建一个最小权限的 PG 角色（示例，凭据只走环境变量，不落盘）：
```sql
CREATE ROLE v9 LOGIN PASSWORD :'pw';          -- 口令由密钥系统注入
CREATE DATABASE v9 OWNER v9;
```
```bash
export DATABASE_URL='postgresql://v9@db.internal:5432/v9'   # 仅当前会话
```

### 第 1 步 · 建表（幂等，不触碰 SQLite）
```bash
psql "$DATABASE_URL" -f audit/schema_pg.sql
# 或让代码自动建：任何一次 PGLedger 实例化都会执行同一份 DDL
python -c "import os; from audit.ledger_pg import PGLedger; PGLedger().close()"
```
**验证点**：`\dt` 出现 `ledger / cross_tasks / cross_arbitration` 三张表。

### 第 2 步 · 双写过渡（SQLite 主 · PG 影，观察期）
把 `DualLedger` 包在主账本外层，同一 `event_id` 两侧复用；影子侧失败**不回滚主写**，
只打印补偿日志（过渡期宁可少写影子，不可丢主账）：
```python
from audit.ledger import Ledger
from audit.ledger_pg import PGLedger
from audit.migrate_sqlite_to_pg import DualLedger

ledger = DualLedger(Ledger("tentacle_ledger.db"), PGLedger())
# 其余代码零改动：log / log_many / counts / coverage 全部透传主库
```
**验证点**：跑一次 `python main.py --root . --workers 4`，然后
- `SELECT COUNT(*) FROM ledger` 在两边同幅增长；
- 面板 `/api/backend` 的池指标（`in_use / idle / util_pct`）无持续抬升、无 `slow_count` 飙升。

### 第 3 步 · 一次性回填（幂等可重跑）
```bash
python audit/migrate_sqlite_to_pg.py --sqlite tentacle_ledger.db --verify
```
- 批量 2000 行 / 事务，`INSERT ... ON CONFLICT (event_id) DO NOTHING`；
- `--verify` 打印 `SQLite=N  PG=M` 一致性判据；
- 中断/重跑安全：已入库的行因 `event_id` 冲突被跳过。
**验证点**：`PG >= N` 且面板覆盖率（`/api/state`）与迁移前一致。

### 第 4 步 · 切换（只改两个环境变量，重启即生效）
```bash
export LEDGER_BACKEND=pg
export DATABASE_URL='postgresql://v9@db.internal:5432/v9'
python main.py --root . --workers 8        # 8 触手真并发写
```
可选调参（均有默认值，不设也能跑）：
```
PG_MINCONN=2      # 池下限
PG_MAXCONN=32     # 池上限（按触手数×并发度取，别超过 PG max_connections 的 70%）
PG_SLOW_MS=200    # 慢查询阈值（面板 be-slowtable）
PG_LONG_TX_S=120  # 长事务告警阈值（面板 locks 告警）
```
**验证点**：
1. `backend_info()` 显示 `pg (host:port/db)`；
2. `/api/backend`：`pool.total > 0`、`conn_pct < 90`、`locks=[]`；
3. `/api/scale`：`enabled=true`，有 `growth_slope / eta_days / partitions`；
4. 连续跑两轮 `main.py`，账本无重复行（同目标同状态由 `event_id` 幂等挡掉）。

---

## 三、回滚方案

| 时点 | 回滚动作 | 数据影响 |
|---|---|---|
| 双写期（步 2）内 | 去掉 `DualLedger` 包装，恢复裸 SQLite 账本 | 主库从未变过，零丢失 |
| 切换后（步 4）想回 SQLite | `unset LEDGER_BACKEND`（或 `=sqlite`）重启 | **PG 期间新写的行不在 SQLite**；如需并回，用 `migrate` 脚本反向跑（`ledger` 表结构一致，字段做一次映射即可） |
| PG 故障 | 面板 `/api/backend` 卡片变红 + `blocked` 日志；主流程按现有语义**显式报错**（`make_ledger()` 失败 → 退出码 1），不静默降级 | 恢复 PG 后重跑即可，幂等保证重复行被吸收 |

> 诚实说明：过渡期双写是"影子 best-effort"（影子失败不回滚主写），
> 因此双写期 PG 可能缺行——由第 3 步回填补齐；这也是把回填放在切换前的原因。

---

## 四、传感器表（说/听 支路）的方言处理

`voice_jobs / transcripts / mic_segments / mic_events` 四张表由 `senses/voice.py`、
`senses/mic.py` 在实例化时自建；两种后端都支持，差异全部收敛在 `senses/sqldialect.py`：

| 项 | SQLite | PostgreSQL |
|---|---|---|
| 占位符 | `?` | `%s` |
| 时间列 | `REAL`（epoch 秒） | `TIMESTAMPTZ`（写入 `to_timestamp(%s)`） |
| 自增主键 | `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGSERIAL` |
| 大小写检索 | `LIKE` | `ILIKE` |
| 可选过滤 | `(? IS NULL OR col >= ?)` | `(%s::float8 IS NULL OR col >= to_timestamp(%s::float8))` |

两端 `search()` 返回**同一形状**（`{"seq","t","text",...}`），上层无感知。
不存在任何动态拼 SQL：过滤条件用 NULL 通配常量 SQL，外部输入全部参数绑定。

---

## 五、验收清单（迁移完成的判据）

```bash
# 1. 建表/幂等：连跑两次不报错
python -c "from audit.ledger_pg import PGLedger; PGLedger(); PGLedger()"

# 2. 双写无损：主/影同幅
python main.py --root . --workers 4            # 配 DualLedger 时

# 3. 回填一致
python audit/migrate_sqlite_to_pg.py --verify  # 期望: ✅一致

# 4. 并发真写：8 触手并发下零 locked/dropped
LEDGER_BACKEND=pg python main.py --root . --workers 8

# 5. 面板三卡
#   /api/backend  → pool.in_use / conn_pct / locks / slow_queries
#   /api/scale    → usage_pct / growth_slope / eta_days / partitions
#   /api/senses   → voice_jobs / mic_segments 行数

# 6. 七条验收（含账本/R2/回放/缓存真读数）
python audit/stack_acceptance.py
```

---

## 六、风险与红线

- **凭据**：`DATABASE_URL` 只从环境变量读取（`ledger_pg.py` 强制 `os.environ["DATABASE_URL"]`），
  仓库里没有、也不允许出现可用凭据字面量。
- **最小权限**：迁移角色只需要三张表的 SELECT/INSERT/UPDATE + 序列；
  分区管理额外需要 `pg_try_advisory_lock` 与建分区权限（仅扩容机）。
- **时区**：PG 端统一 `TIMESTAMPTZ`（写入 `to_timestamp(epoch)`），读出统一 epoch，
  与 SQLite 端 `REAL` epoch 口径一致，避免"看起来差 8 小时"。
- **状态约束收紧**：PG 的 `ledger.status` 带 `CHECK`（四值），迁移前如遇脏状态行，
  回填会显式失败而不是静默吞行——先修数据再迁。
- **不做静默降级**：选 `pg` 而 PG 不可达时，主流程直接报错退出（实测输出
  `connection refused ... Is the server running`），绝不偷偷回退 SQLite 造成双源漂移。
