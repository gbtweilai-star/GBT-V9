# GBT小土豆V9 · 目录约定（摆放整齐的规矩）

> 2026-10-08 主人要求："把文件全部整理好摆放整齐。"
> 这份文档就是"整齐"的判据：**新增东西时照这张表放**，别往根目录堆。

## 根目录只放"入口与配置"（不许再堆散件）
| 该放的 | 例子 | 为什么在根 |
|---|---|---|
| 入口 | `main.py`、`conftest.py` | 跑起来的第一站 |
| 配置 | `pyproject.toml`、`pytest.ini`、`requirements*.txt`、`config.yaml`、`docker-compose*.yml`、`install.sh`、`.env*.example`、`.gitignore` | 工具链按约定在根找它们 |
| 门面 | `README.md`、`LANDING.md`、`GBT小土豆V9-总控台.cmd` | 人和脚本一眼能找到 |
| 活跃数据（**路径被写死**） | `tentacle_ledger.db`（+`-shm`/`-wal`）、`witness_integration_status.json` | 代码里的默认值是**相对根的** `tentacle_ledger.db`（`audit/ledger*.py`、`audit/stack_acceptance.py` 等）——挪动须先改默认值并重启，**别手贱** |

## 目录分工
| 目录 | 放什么 | 纪律 |
|---|---|---|
| `core/` | 能力位与引擎（每个模块过钩子 + 进账本） | 一能力一文件；新能力要注册进 `capability_map`/`page_registry` |
| `panel/` | 页面与路由（一页一文件） | 新页要注册进页面表（导航才出现） |
| `senses/` | 感官（话筒/视觉/蓝牙/语音链） | 只读优先；写要走 Grant |
| `body/` | 身体服务（适配器/证据租约/网关护栏） | 出行一律过 `net_guard` |
| `skills/` | 技能与 UI 规范（`.btn` 家族、`ui_design`） | 页面不许自带 `button{}` |
| `templates/` | Blender/生成模板（占位符替换，不做 f-string 大括号地狱） | 模板改动要配一次真渲染验证 |
| `scripts/` | **独立小工具**（零 import 的散件：mesh/printer/scan_*/devour/brain/report…） | 不是包、不被 import；需要复用就上提进 `core/` |
| `tools/` | 外部工具与桥接 | — |
| `tests/` | 测试（时间相关必须冻结时间；网络相关必须打桩） | 新增能力至少一条守门测试 |
| `docs/` | 文档（草案/交付/映射/预检） | **根只留 README/LANDING**，其余进这里 |
| `data/` | 运行库与工件索引（`gbt_v9.sqlite3` 等） | 只读打开时用 `mode=ro` |
| `state/` | 运行产物（产物图/动画/日志/台账 jsonl/缓存） | **中间产物用完即退**（`core.retire`） |
| `_archive/` | 旧物/遗留模块（`build/`、`dashboard/` 等零引用件） | 归档而不是删；要复活先查引用 |
| `desktop/` | 桌面壳与 Octop 便携底座 | 改 `main.js` 要同步打包快照并重打包 asar |
| `native-capabilities/` `integrations/` `workflows/` `migrations/` `parity/` `security/` `scan/` `sched/` `media/` `alert/` `audio/` `audit/` `actuator/` `contract/` `common/` `parity/` | 各自领域模块 | 归属清晰，不往别处丢 |

## 两条铁律（比这张表更重要）
1. **新件落地就清旧件**（`core.retire`：进**回收站**、可还原）——别让重启把旧的当新的；
   开机自动扫一遍（面板启动时跑 sweep）。
2. **摆放整齐的判据是"能被下一只手找到"**：新增文件前先问一句"它该归上表哪一格？"，
   答不上来就先别建——**根目录不接散件**。

## 现状（2026-10-08 整理后）
- 根：**20 个文件**（原 35）+ 35 个结果目录；清掉 5 项缓存/空目录（进回收站）；
- 4 份杂项文档 → `docs/`；9 个零引用小工具 → `scripts/`；`build/`、`dashboard/` → `_archive/`；
- 摆放位置：**桌面** `C:\Users\ADMIN\Desktop\GBT小土豆V9`（与 V8 并排，一眼能找到）；
  主目录里的 `gbt-potato-v9` 与 `GBT小土豆V9` 都是 **Junction** 指过来 —— 桌面壳/开机自启/脚本照旧不断；
- 台账/绑定无损：`data/gbt_v9.sqlite3` 32 表、`octop_binding` 82,000 行。
