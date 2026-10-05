# tests/legacy_spec —— 「对话目标态」测试存档（不进 CI）

这里的测试文件来自对话中给出的**目标态设计**：它们引用的 API 在撰写时
尚未在工程树里落地，或模块本身是残缺件。整层被 `tests/conftest.py` 的
`collect_ignore_glob = ["legacy_spec/*"]` 排除，因此 `pytest tests/` 保持真实全绿。

## 各文件缺口速查（API 落地后把文件移回 tests/ 即可回归）

| 文件 | 缺口 |
|---|---|
| test_db_contract.py / test_dbmetrics.py | 需 psycopg3（工程统一用 psycopg2） |
| test_identity_r2.py / test_identity_b2.py | 需 `body.identity.reject_duplicate_identities` |
| test_evidence_leases.py | 需 `body.evict.claim_for_eviction` |
| test_witness_segments.py | 模块级 `NameError: get_db`（残缺件） |
| test_witness_debounce.py | 需 `body.witness_probe.CFG` |
| test_workflow_editor.py | 需 `workflows.store.list_flows`（当前循环导入） |
| test_web_routes.py | 需 `parity_routes_time` 模块 |
| test_infra_capabilities.py | 需 `build.model_optimize` 模块 |
| test_lease_session.py / test_put_and_hold.py / test_frame_* | 需 body.ports 帧证据链适配器（store/lease/executor） |
| test_calibration*.py / test_color_effect_verify.py / test_video_edit_touch.py | 需校准/比对子系统（ΔE 闭环） |
| test_health.py / test_scan_sessions.py / test_sessions_snapshots.py / test_trends*.py / test_throughput.py / test_repair*.py / test_gap_alerts.py / test_landing.py / test_witness_quorum.py / test_run_edit_op_wiring.py / test_delta_kernel.py / test_credential_domain.py | 对应目标态面板/编排 API 未落地 |

## 约定

- 这些文件是**规格**，不是死代码：实现对应子系统时以它们为验收标准。
- 修任何一个 = 把文件移回 `tests/`、跑绿、然后从本表划掉。
- 不允许为了让它们"过"而放松主工程的测试（tests/ 下的真实套件必须保持诚实）。
