# GBT小土豆V9 · 触手工程（吞噬能 → R2 归档 → 回放 → 面板 → 缓存回收）

## 这是什么 · 一张图看懂

**GBT 小土豆 V9** = **一个主脑 + 一支可扩到亿万根的触手编队**，用**模块式部署**把"看得见、点得动、
跑得通、验得过"四件事绑成闭环；每个能力都有**自己的验收器**，没闭环的能力**禁止交付**。

```
                        ┌──────────────── 主脑（指挥） ────────────────┐
                        │  接手协议(护栏挂接手这一刻) · 交付闸(每能力单跑) │
                        └───────┬───────────────────────┬──────────────┘
              ┌─────────────────┘                       └───────────────┐
       ┌──────▼──────┐                                            ┌───────▼────────┐
       │  眼（实时流）│  抓帧线程→最新帧槽(帧龄~14ms)               │ 手（触手）      │
       │  mss 60fps  │ ───────────────┐                            │ 装备坞·随身库   │
       └─────────────┘                ▼                            │ 职业·账号·钥匙  │
                        ┌────────────────────────┐                  └───────┬────────┘
                        │ 脑：取最新帧即回(0ms)   │                          │
                        └───────────┬────────────┘                          │
                                    ▼                                        ▼
                     ┌──────────────────────────────┐          ┌──────────────────────┐
                     │ 万能插（插上并驱动）          │          │ 接手包：谁接·凭什么接 │
                     │  process/file/api/web/model  │          │ 接不了→带原因退        │
                     └───────────┬──────────────────┘          └──────────┬───────────┘
                                 ▼                                        ▼
              ┌───────────────────────────────┐            ┌─────────────────────────┐
              │ 牢房：模型关在里面跑            │            │ 编队：1 亿地址可寻址      │
              │ 四查(文件/进程/出网/stdout)     │            │ 实体按需·钥匙 HMAC 派生   │
              │ 结果只从 outbox 出·封印防篡改   │            │ 交叉扫描：命中须 2 根复核 │
              └───────────────┬───────────────┘            └─────────────────────────┘
                              ▼
                    ┌────────────────────┐
                    │ 触手把结果"吐"出来   │→ 随身库(一丢即走) → 主脑批量慢看
                    └────────────────────┘
```

**它解决的核心问题**：一个人（不懂代码的设计师）要指挥 AI 干长程活时，**不能每步都靠人**、
**不能模型一拒绝就卡死**、**不能嘴上说"做好了"却没证据**。于是：

| 痛点 | 机制 | 实测读数 |
|---|---|---|
| 模型拒答就停工 | 原样指令 · 沙盒里火力全开 · 换人 · 结果只从触手吐 | 1b 空答→自动换 3b 拿到产出 |
| 说不清"做没做" | 每步落账 + 邮件正文留痕 + 封印 sha256 | 邮箱正文可读回·篡改即拒吐 |
| AI 说的不能信 | **交付闸**：每能力一条独立验收器，缺=没闭环=禁止交付 | 逐能力单跑，红即不准交 |
| 看屏幕慢到被打爆 | 实时视觉流（独立线程+最新帧槽） | 640×360 **59.9fps** · 帧龄 14ms · 真响应 0ms |
| 触手数量不够 | 逻辑 1 亿地址 + 实体按需 + 钥匙派生 | spawn 10 万仅 15.3s · 盘占 8MB |
| 报警被"不影响"糊过去 | 每日重启排查：任何报警必须带根因/处置/证据 | 缺一项即退出码 1 |



> 根目录名可改（原方案叫 `gbt-tentacle/`，当前工程根为 `gbt-potato-v9/`）。
> 本文件 = **一次落盘说明**：目录树 · 依赖 · 启动顺序 · 验收读数 · 自检命令。

## 1. 工程目录树（现状，全部可运行）

```text
gbt-potato-v9/
├── README.md                     # 本文件（目录树 + 依赖 + 启动顺序）
├── requirements.txt              # pip 依赖清单
├── .env.example                  # 环境变量模板（复制为 .env；不含任何真实密钥）
├── docker-compose.yml            # CLIProxyAPI 统一网关
├── config.yaml                   # CLIProxyAPI v8 配置（密钥走环境变量）
│
├── contract/                     # 契约层
│   ├── tentacle_contract.ts      # TS 契约定义（能力/权限/超时/钩子/吞噬策略）
│   └── slot_board.py             # D1~D100 槽位板（认领/心跳/超时回收，SQLite）
│
├── core/                         # 主脑与总线
│   ├── brain.py                  # 主脑：唯一 LLM 出口
│   ├── isolated_bus.py           # 共享网关 · 隔离使用（key/配额/记忆）
│   ├── mesh.py                   # D1..D100 全互联（O(1) 直达 + 投递落账）
│   └── pulse.py                  # 万能插脉冲（结构化插座优先）
│
├── senses/                       # 感知与执行（本次重点）
│   ├── devour.py                 # 吞噬能：采集 + 每帧哈希 + index.jsonl + 断档审计 + 修复
│   ├── r2.py                     # 统一 R2 层：真 boto3 / 离线模拟 / 出站 host 校验
│   ├── tiered_store.py           # 分层归档：水位触发 → FFV1 封装 → R2 → 驱逐本地帧
│   ├── printer.py                # 打印复原：无损视频 / 帧包 / 联络表
│   ├── playback.py               # 回放端：三级命中 + 哈希校验 + 拼接（+ preview/export API）
│   ├── cache_reaper.py           # 缓存回收：LRU + pin 保护 + _preview/_cache 双预算
│   ├── voice.py                  # 说/听：TTS 队列(去重+缓存) · ASR 转写 · 配音
│   ├── mic.py                    # 实时麦克风：VAD 切段 → 有界队列 → 转写 → 关键词触发
│   └── sqldialect.py             # 传感器 SQL 双方言（sqlite ? / pg %s；epoch / TIMESTAMPTZ）
│
├── scan/                         # 扫描与对账
│   ├── scanner.py                # 顶层遍历 + 权威全集 + full_sweep
│   ├── scan_rules.py             # 密钥/依赖/危险 API 规则
│   ├── cross_scan.py             # 交叉互扫
│   └── report.py                 # 覆盖率对账报告
│
├── skills/                       # 能力插件（主脑按需调用）
│   └── native_codex.py           # Codex 编程工具（V9 的工具之一；沙箱只读/工作区写，绝不放宽）
│
├── audit/                        # 账本与验收
│   ├── ledger.py                 # 扫描/归档/回放账本（SQLite，WAL + 退避）
│   ├── ledger_factory.py         # 账本开关：LEDGER_BACKEND=sqlite|pg（环境变量切）
│   ├── ledger_pg.py              # PG 账本：连接池 + 池指标 + 服务端探针（真并发写）
│   ├── schema_pg.sql             # PG 建表（ledger / cross_tasks / cross_arbitration，幂等）
│   ├── migrate_sqlite_to_pg.py   # 一次性回填（幂等）+ DualLedger 双写过渡
│   ├── PG-MIGRATION.md           # 迁移方案：四步加法 · 双写/回填/切换/回滚
│   ├── scale_daemon.py           # 三层扩容（partition/growth/daemon，advisory lock 单控）
│   └── stack_acceptance.py       # 七条判据全检（真读数；拿不到写「未读到 + 原因」）
│
├── panel/                        # 可视化面板（:8765）
│   ├── server.py                 # 状态看板 + 回放台（预览/导出/缓存管理）
│   └── exports/                  # 导出产物（自动创建）
│
├── tools/
│   ├── entry.py                  # sys.path 固定
│   ├── selfcheck.py              # 落盘自检：目录/语法/导入（--with-acceptance 连验收）
│   ├── trace_chains.py           # 原流水线统一视图（总装入口改为 main.py 后迁到这里）
│   └── accept_octop_offline.py   # Octop 侧 53 项能力验收（与本事并行）
│
├── main.py                       # 总装入口：多触手并发扫描 + 交叉互扫 + 感官/Codex 接线
│
├── state/                        # 运行态（自动创建；r2-sim/ 为离线模拟桶）
├── devoured/t1-eye/              # 吞噬落帧根（自动创建）
│   ├── f00000000.png …           # 热层帧
│   ├── index.jsonl               # 每帧 {seq,sha256,ts,bytes,state}
│   ├── segments.jsonl            # 段索引（sealed→uploading→archived）
│   ├── _segments/                # 本地保留的段（KEEP_LAST_SEGMENTS）
│   ├── _cache/                   # R2 拉回缓存（PULL_CACHE_MB 预算，可回收）
│   └── _preview/                 # 预览转码缓存（PREVIEW_CACHE_MB 预算，可回收）
└── tentacle_ledger.db            # 账本（自动创建）
```

## 2. 依赖清单

```text
pip（见 requirements.txt）：fastapi · uvicorn · pydantic · mss · pillow · numpy ·
                           pyautogui · boto3 · botocore · openai
系统级：ffmpeg（FFV1 封装/拼接/转码预览）· docker（CLIProxyAPI 网关）· ollama（本机 Qwen，可选）
验证： ffmpeg -version && docker --version && ollama --version
```
说明：没有 ffmpeg 时不会全废 —— 封段自动回退为**帧包 zip（无损）**，预览转码回退为直给原件；
没有 boto3 真 R2 时可用 `R2_SIM_DIR=state/r2-sim` 走**离线模拟桶**，全链路依旧可跑可验。

## 3. 启动顺序

```bash
# 0) 依赖
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# 系统级：ffmpeg / docker / ollama（见上）

# 1) 网关（一套上游凭据，多把隔离钥匙）
cp .env.example .env        # 填 OPENAI_API_KEY / CLI_PROXY_MGMT_KEY / 上游登录
docker compose up -d        # → http://127.0.0.1:8317

# 2) 本机 Qwen 大脑（可选但推荐）
ollama serve &
ollama pull qwen2.5:27b     # 显存 <24G 换 14b/7b

# 3) 面板（另开终端；启动即自动迁移 + 拉起缓存回收循环）
set -a && source .env && set +a          # Windows: 逐条 set VAR=...
uvicorn panel.server:app --port 8765     # → http://127.0.0.1:8765

# 4) 七条验收（真读数，不拿绿顶）
python audit/stack_acceptance.py --with-local

# 5) 总装运行（多触手并发 + 交叉互扫；账本写入线程安全）
python main.py --root /你要扫描的项目路径 --workers 8 --k 1
python main.py --root . --frames 60 --say "开始吞噬"        # 并发吞噬 + 语音播报
python main.py --root . --code "修掉 parse.py 空输入崩溃"    # Codex 工具做一次编程任务
LEDGER_BACKEND=pg python main.py --root . --workers 8       # 切 PG 账本（需 DATABASE_URL）
```

> 感官闭环：看（吞噬能）→ 听（ASR/麦克风）→ 说（TTS）→ 控（脉冲/触手）→ 想（主脑）。
> 语音与麦克风都是**可选支路**：VoiceStudio 不可达时只在 voice_jobs 记一行 failed，主流程照跑。
> 账本迁移与调参看 `audit/PG-MIGRATION.md`（PG_MINCONN / PG_MAXCONN / PG_SLOW_MS / PG_LONG_TX_S）。

## 4. 七条判据（当前实测读数）

| # | 判据 | 状态 | 读数 |
|---|---|---|---|
| ① | 统一网关可达 | ⏳ 待你填 key | `HTTP 401 @ <你的网关>/models`（.env 里有 key 即通过） |
| ② | 本机 Ollama 车道 | ⏳ 未启动 | `连接被拒 @ http://127.0.0.1:11434/api/tags`（`ollama serve` 后通过） |
| ③ | 账本可读写 | ✅ | 真写一行 → 回读 → 删除；现存行数照实报 |
| ④ | 吞噬零丢帧 | ✅ | 36 帧 · 跨度 36 · 每帧 sha256 覆盖 35/36（1 帧为演练故意丢） |
| ⑤ | R2 归档可用 | ✅ | 模拟模式 · 2 对象 · 元数据 17812B（填真 R2 四项变量即切真桶） |
| ⑥ | 回放三级命中 | ✅ | 本地命中 + 从 R2 拉回并哈希校验，取段成功 |
| ⑦ | 缓存回收可用 | ✅ | 双预算读数 + 真跑一次回收（超限清最旧，pin 保护） |

## 5. 一条流水线全景

```text
契约层   能力/权限/钩子/槽位（contract/）
   ↓
核心层   主脑 · 隔离总线 · mesh · 脉冲（core/）
   ↓
感知层   吞噬(采集+哈希+断档) → 分层(水位→FFV1→R2) → 回放(三级命中+拼接) → 打印(视频/帧包/联络表)
   ↓
扫描层   遍历 → 规则 → 交叉互扫 → 对账（scan/）
   ↓
审计层   账本 + 七条验收（audit/）
   ↓
面板层   看板 + 回放台（预览/导出/缓存管理）（panel/）
```

## 6. 落盘自检

```bash
python tools/selfcheck.py                  # 目录 26 件 / 语法 / 17 个关键导入
python tools/selfcheck.py --with-acceptance  # 连七条验收一起跑
```

## 7. R2 三种模式（自动选择）

| 模式 | 触发条件 | 行为 |
|---|---|---|
| sim | 设了 `R2_SIM_DIR` | 本地目录模拟 S3：上传/回读校验/回放全真跑（离线演示与 CI 用） |
| r2 | 设了 `CLOUDFLARE_ACCOUNT_ID` 等 4 项 | 真 R2（S3 兼容）；发请求前校验 host（仅 https，拒绝环回/私有/保留） |
| off | 都没设 | 显式抛错并降级提示，**不假装成功** |

## 8. 本轮增补（总装 · 迁移 · 感官 · 工具）

| 增补 | 落点 | 实测 |
|---|---|---|
| 多触手并发扫描 | `main.py`：目标分片给 N 根触手，`safe_log` 指数退避，随后 `cross_sweep` 交叉互扫 | 4 触手 8 目标覆盖率 1.0；PG 8 触手模式已接线 |
| 账本线程安全 | SQLite 每线程连接 + WAL + 退避；PG 连接池 + `event_id` 幂等 | 契约测试 32 passed（7 条 PG 用例待 PG 环境） |
| SQLite→PG 迁移 | `audit/PG-MIGRATION.md` + `migrate_sqlite_to_pg.py`（回填/双写） | DualLedger 双写实测同幅；`LEDGER_BACKEND=pg` 切换报错清晰不静默 |
| 传感器表双方言 | `senses/sqldialect.py` + voice/mic 分支 | SQLite 下 voice_jobs/transcripts 建表·写·查全通 |
| 听得见说得出 | `senses/voice.py`（TTS 队列/ASR/配音）+ `senses/mic.py`（VAD 切段+关键词） | `--say` enqueue ✓；VoiceStudio 不在时记 failed 不阻塞 |
| Codex 编程工具 | `skills/native_codex.py`（沙箱 read-only/workspace-write，无绕过旗标） | 实测创建并运行 `hello.py`，输出 `V9 CLI 就绪`，rc=0 |
| 面板三卡 | `/api/backend`（池+告警）· `/api/scale`（用量/斜率/ETA/分区）· `/api/senses`（五路感官） | 重启后面板 5 端点全 200，voice_jobs 读数为真 |