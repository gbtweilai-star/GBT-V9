# tests/test_pipelines.py —— 四条流水线分类部署 + 变更日志（扫描/新增/修改/部署/固化）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 每步的云插件槽/库槽必须是**真实存在**的（对 PLUGIN_IDS / SLOT_IDS 逐个校验）
#   ② 目录里没有的能力必须**如实标注缺口 + 待接登记**，不许假装已部署
#   ③ 部署登记：每步一条 deploy 记录；固化登记表可回滚（sha256 校验）
#   ④ 变更日志：扫描→新增→修改→消失 都要留痕，且"哪里"精确；坏行跳过并计数
#   ⑤ 未知记录类型拒绝写入（不写脏数据）
import json

import pytest

from core import deploy_ledger as J
from core import pipelines as P


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """日志与快照都指到临时目录，别污染仓库 state/。"""
    monkeypatch.setattr(J, "STATE_DIR", str(tmp_path / "state"))


# ═══════════ ① 映射完整性 ═══════════
def test_every_step_maps_to_real_plugin_and_db_slot():
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    ps, ds = set(PLUGIN_IDS), set(SLOT_IDS)
    a = P.audit()
    assert a["ok"] and a["问题"] == [], a["问题"]
    assert a["流水线"] == 4 and a["步骤总数"] == 48
    for _, s in P.steps():
        assert s.插件 in ps, s.插件
        assert s.库槽 in ds, s.库槽
        assert s.细节 and s.本地备用, f"{s.id} 缺细节或本地兜底"


def test_pipeline_step_counts_and_gates():
    counts = {p.id: len(p.步骤) for p in P.PIPELINES}
    assert counts == {"shortvideo": 12, "film": 14, "music": 10, "procode": 12}
    gates = {s.id for _, s in P.steps() if s.门禁 != "无"}
    assert gates == {"sv12", "fm13", "mu10", "pc12"}       # 发布/审片/分发/提交 才要门禁


# ═══════════ ② 缺口如实标注 ═══════════
def test_missing_families_are_declared_with_fallback():
    a = P.audit()
    pend = a["待接登记"]
    assert len(pend) == 7                                   # 视频2 音乐4 混音1
    for x in pend:
        assert x["待接登记"] and x["当前兜底"]
    where = {x["哪里"] for x in pend}
    assert where == {"shortvideo/sv4", "shortvideo/sv8", "film/fm8", "film/fm10",
                     "music/mu4", "music/mu5", "music/mu7"}
    rows = P.deploy_rows()
    # 目录无原生模型 ≠ 没接上：这些步用"替代实现"接上了，同时保留待接登记
    assert sum(1 for r in rows.values() if r["状态"] == "已接（替代实现）") == 7
    assert sum(1 for r in rows.values() if r["原生模型"] == "目录无（登记待接）") == 7
    assert all(r["状态"] in ("已部署到云插件", "已接（替代实现）") for r in rows.values())


def test_resolve_step_and_unknown_step_refused():
    ok = P.resolve_step("shortvideo", "sv1")
    assert ok["ok"] and ok["名称"] == "选题与钩子" and ok["插件"].startswith("text-generation:")
    miss = P.resolve_step("shortvideo", "sv99")
    assert miss["ok"] is False and "没有这一步" in miss["reason"]


# ═══════════ ③ 部署登记与固化 ═══════════
def test_deploy_all_registers_every_step_and_solidifies(monkeypatch, tmp_path):
    monkeypatch.setattr("core.solidify.BASE", tmp_path / "solid")
    r = P.deploy_all()
    assert r["ok"] and r["登记条数"] == 48
    assert r["固化"]["ok"] and r["固化"]["name"] == "pipeline_registry"
    # 触手分配：4 段 × 25 根，且不重叠
    tent = r["触手分配"]
    assert len(tent) == 4
    all_t = [t for v in tent.values() for t in v["触手"]]
    assert len(all_t) == 100 and len(set(all_t)) == 100
    kinds = J.summary()["by_kind"]
    assert kinds["deploy"] == 52                            # 48 步 + 4 段触手分配
    assert kinds["solidify"] == 1
    # 幂等：再部署一次不会重复涨"新增"（deploy 记录会再记，但扫描不会认成新增）
    P.deploy_all()
    assert J.summary()["by_kind"]["deploy"] == 104


# ═══════════ ④ 变更日志：扫描/新增/修改/消失 ═══════════
def test_scan_records_add_modify_and_remove_with_where():
    first = P.scan()
    assert first["items"] == 52 and len(first["added"]) == 52 and first["changed"] == []
    kinds = J.summary()["by_kind"]
    assert kinds["scan"] == 1 and kinds["add"] == 52
    # 人为改一个步骤的名称 → 下一次扫描必须记成"修改"，且带 before/after
    step = [s for _, s in P.steps() if s.id == "sv1"][0]
    old = step.名称
    step.名称 = "选题与钩子（改）"
    try:
        second = P.scan()
    finally:
        step.名称 = old
    # 键是稳定 id（shortvideo/sv1）：改名只应产生一条"修改"，不是"新增+消失"
    assert "shortvideo/sv1" in second["changed"], second["changed"][:3]
    assert "shortvideo/sv1" not in second["added"]
    got = J.recent(200)["rows"]
    mods = [r for r in got if r["kind"] == "modify" and r["where"] == "shortvideo/sv1"]
    assert mods and mods[0]["before"] and mods[0]["after"]
    assert mods[0]["before"]["名称"] == old
    assert mods[0]["after"]["名称"] == "选题与钩子（改）"
    # 删掉一个步骤 → 下一次扫描记成"消失"（ok=False + 原因）
    popped = P.PIPELINES[3].步骤
    P.PIPELINES[3].步骤 = popped[:-1]
    try:
        third = P.scan()
    finally:
        P.PIPELINES[3].步骤 = popped
    assert any("pc12" in r for r in third["removed"]), third["removed"][:3]
    gone = [r for r in J.recent(200)["rows"]
            if r["kind"] == "modify" and "pc12" in r["where"] and not r["ok"]]
    assert gone and "消失" in gone[0]["reason"]


def test_journal_rejects_unknown_kind_and_skips_bad_lines(monkeypatch):
    bad = J.record("oops", "x")
    assert bad["ok"] is False and "未知记录类型" in bad["reason"]
    real = json.dumps
    monkeypatch.setattr(J.json, "dumps", lambda o, **k: "{坏行" if o.get("where") == "broken"
                        else real(o, **k))
    J.record("scan", "broken")
    got = J.read_all()
    assert got["bad_lines"] == 1 and all(r["kind"] in J.KINDS for r in got["rows"])


def test_journal_summary_counts_are_real():
    J.record("scan", "scope-A", detail={"清点项": 3})
    J.record("add", "where-B")
    J.record("modify", "where-C", before={"a": 1}, after={"a": 2})
    J.record("deploy", "where-D", ok=False, reason="待接")
    s = J.summary()
    assert s["total"] == 4 and s["failed"] == 1
    assert s["by_kind"] == {"scan": 1, "add": 1, "modify": 1, "deploy": 1, "solidify": 0}
    assert J.recent(2)["count"] == 4 and len(J.recent(2)["rows"]) == 2
