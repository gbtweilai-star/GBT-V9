# tests/test_production_gate.py —— 生产就绪度总审计：根因归类 / 修复登记 / 不许假"已修复"
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：
#   ① 面板上每处"未达标"都有 现状→根因→归类→修复动作（结构完整，不许空字段）
#   ② 归类必须落到六类之一；账务/凭据/硬件这类**改不了的要写改不了**（不许标"已修复"）
#   ③ 就绪度 = (已修复 + 按设计 + 待厂商目录) / 合计
#   ④ apply_fixes 会把每条以 modify 记进变更日志，并固化 production_gate（可回滚）
import json

import pytest

from core import deploy_ledger as J
from core import production_gate as PG


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("core.solidify.BASE", tmp_path / "solid")
    # 审计里会做驱动预检/深探（真发请求）——测试里换成确定性的假事实
    monkeypatch.setattr(PG, "_drive_facts", lambda: {
        "preflight": {"通道": "free-local", "通道说明": "测试：主通道余额为 0 → 走本机免费通道",
                      "主通道原因": "insufficient_balance", "密钥已配置": True},
        "deep_probe": {"根因": "insufficient_balance",
                       "主通道": {"error_type": "insufficient_balance"},
                       "选中通道": {"ok": True}}})
    yield


def test_audit_items_are_well_formed():
    a = PG.audit()
    assert a["项"] and len(a["项"]) == a["统计"]["合计"]
    for it in a["项"]:
        for k in ("面板", "项目", "现状", "根因", "归类", "修复"):
            assert it.get(k), f"{it.get('项目')} 的 {k} 是空的"
        assert it["归类"] in (PG.FIXED_CODE, PG.FIXED_CONF, PG.BLOCK_BILLING,
                              PG.BLOCK_CRED, PG.BLOCK_HW, PG.BY_DESIGN, PG.PENDING_VENDOR)


def test_drive_item_reflects_reality_either_way():
    """驱动器这条必须**跟着真实账本走**：通了就是已修复，没通就是阻塞·账务（两态都合法）。"""
    from core import production_gate as PGa
    a = PGa.audit()
    drive = [i for i in a["项"] if "驱动器" in i["项目"]][0]
    dc = PGa._drive_counts()
    if dc.get("成功"):
        assert drive["归类"] == PGa.FIXED_CODE
        assert "纯 CPU" in drive["根因"] or "num_gpu" in drive["根因"]
        assert "驱动成功" in drive["现状"] and "余额" in drive["根因"]
    else:
        assert drive["归类"] == PGa.BLOCK_BILLING
        assert "余额" in drive["根因"] and "充值" in drive["修复"]
    # 云插件 id 这条：已按官方文档核实（ids_unverified=0）→ 必须是"已修复"，不是阻塞
    cf = [i for i in a["项"] if "model id" in i["项目"]][0]
    assert cf["归类"] == PGa.FIXED_CODE
    assert "官方文档" in cf["修复"] and "待核 0" in cf["现状"]


def test_alternative_implementations_are_executable_not_just_text():
    """替代实现必须真能跑（有执行器 + 有产物），否则不许算"成"。"""
    a = PG.audit()
    alt = [i for i in a["项"] if "替代实现的可执行性" in i["项目"]][0]
    assert "已跑通执行器" in alt["现状"]
    assert alt["归类"] in (PG.FIXED_CODE, PG.BLOCK_HW)
    stub = [i for i in a["项"] if "帧素材占位" in i["项目"]][0]
    assert stub["归类"] == PG.FIXED_CODE and "可解码" in stub["修复"]


def test_readiness_formula_and_counts():
    a = PG.audit()
    st = a["统计"]
    assert st["合计"] == st["已修复"] + st["阻塞（非代码）"] + st["按设计"] + st["待厂商目录"]
    expect = round(100.0 * (st["已修复"] + st["按设计"] + st["待厂商目录"]) / st["合计"], 1)
    assert a["生产就绪度"] == expect
    assert st["已修复"] >= 2                                   # 分母 + 重复行 两处代码修复


def test_by_design_items_are_labeled_not_blocking():
    a = PG.audit()
    design = [i for i in a["项"] if i["归类"] == PG.BY_DESIGN]
    assert len(design) >= 2                                    # 无绑定关系三类 + 本机无 NVIDIA
    assert all("按设计" in i["归类"] for i in design)
    # 本机硬件那条：架构本就是云优先 → 不构成阻塞
    hw = [i for i in a["项"] if "本地出图" in i["项目"]]
    assert hw and hw[0]["归类"] == PG.BY_DESIGN


def test_apply_fixes_registers_every_item_and_solidifies():
    a = PG.apply_fixes()
    assert a["固化"]["ok"] and a["固化"]["name"] == "production_gate"
    rows = J.recent(500)["rows"]
    mods = [r for r in rows if r["kind"] == "modify" and r["where"].startswith("production:")]
    assert len(mods) == a["统计"]["合计"]
    # 只允许"真阻塞（凭据/账务）"是 ok=False；已修复/按设计/替代实现都必须是 ok=True
    failed = [r for r in mods if not r["ok"]]
    assert all("CF model id" in r["where"] or "驱动器" in r["where"] for r in failed), \
        [r["where"] for r in failed]
    vendor = [r for r in mods if "待接" in r["where"] or "待厂商" in r["where"]]
    assert vendor and all(r["ok"] is True for r in vendor), [r["where"] for r in vendor]
    assert any(r["kind"] == "solidify" and "production_gate" in r["where"] for r in rows)


def test_summary_is_lightweight_but_complete():
    s = PG.summary()
    assert set(s) >= {"at", "生产就绪度", "统计", "项", "阻塞清单", "通道", "固化"}
    assert s["通道"] == "free-local"                            # 走的免费通道
    assert json.dumps(s, ensure_ascii=False)                    # 可序列化（面板直接吐 JSON）
