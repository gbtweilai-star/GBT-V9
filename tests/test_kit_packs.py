# tests/test_kit_packs.py —— 三套免费工具 → 云插件部署（剪映 / Qwen-Image / ComfyUI 式）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖（主人要求：全部部署到云插件、配置精细不能混乱、跑通闭环）：
#   ① 每步只落在真实存在的云插件槽/库槽；插件族必须在目录内
#   ② 配置严格 schema：未知键/类型错/重复步骤/白名单外的族 一律打回（"不混乱"的硬保证）
#   ③ 部署登记 + 固化 + 回滚：登记条数 = 步骤数；冻结的配置可回滚
#   ④ 变更日志按 scope 分仓：两套扫描互不干扰（否则会互相把对方判成"消失"）
#   ⑤ 待接（目录里确实没有的能力）必须写明，不许算成"已部署"
import json

import pytest

from core import deploy_ledger as J
from core import kit_packs as K


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("core.solidify.BASE", tmp_path / "solid")


# ═══════════ ① 映射完整性 ═══════════
def test_all_steps_map_to_real_cloud_slots():
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    ps, ds = set(PLUGIN_IDS), set(SLOT_IDS)
    a = K.audit()
    assert a["ok"] and a["问题"] == [], a["问题"]
    assert a["包"] == 3 and a["步骤总数"] == 20
    for _, s in [(p, s) for p in K.PACKS for s in p.步骤]:
        assert s.插件 in ps, s.插件
        assert s.库槽 in ds, s.库槽
        assert s.插件.split(":")[0] in K.ALLOWED_PLUGIN_FAMILIES


def test_pack_shapes_and_honest_gaps():
    counts = {p.id: len(p.步骤) for p in K.PACKS}
    assert counts == {"capcut": 8, "qwen_image": 6, "comfyui": 6}
    gates = {s.id for _, s in [(p, s) for p in K.PACKS for s in p.步骤] if s.门禁 != "无"}
    assert gates == {"cp8", "qi6"}                      # 剪映发布闸门 / 出图合规闸门
    gaps = K.audit()["缺口"]
    ids = {g["哪里"] for g in gaps}
    assert ids == {"capcut/cp4", "comfyui/cf4"}          # 音乐生成缺失 / 视频节点缺失
    for g in gaps:
        assert g["缺口"]
    # 待接的不许算成"原生已接"，但要用替代实现接上（不许留"没接"）
    rows = K.deploy_rows()
    assert sum(1 for r in rows.values() if r["状态"] == "已接（替代实现）") == 2
    assert sum(1 for r in rows.values() if r["原生模型"] == "目录无（登记待接）") == 2
    assert len(rows) == 20


def test_environment_facts_are_declared():
    """本机事实要写在包里（AMD/无 NVIDIA/CPU torch/剪映未装/ComfyUI 在），免后人搞混。"""
    joined = " ".join(" ".join(p.环境事实) for p in K.PACKS)
    assert "AMD" in joined and "CUDA" in joined
    assert "剪映" in joined or "CapCut" in joined
    assert "ComfyUI" in joined


# ═══════════ ② 配置严格 schema ═══════════
def test_config_is_built_from_whitelist_and_validates():
    got = K.build_config("capcut")
    assert got["ok"]
    cfg = got["config"]
    assert set(cfg) <= set(K.CONFIG_SCHEMA)
    assert cfg["cloud_only"] is True and cfg["guard_step"] == "cp8"
    assert K.validate_config(cfg)["ok"] is True
    for pid in ("qwen_image", "comfyui"):
        c = K.build_config(pid)["config"]
        assert K.validate_config(c)["ok"] is True


def test_config_rejects_unknown_key_type_and_bad_family():
    cfg = K.build_config("capcut")["config"]
    bad1 = {**cfg, "乱塞的键": 1}
    r1 = K.validate_config(bad1)
    assert r1["ok"] is False and any("未知配置键" in e for e in r1["errors"])
    bad2 = {**cfg, "version": "一"}
    assert K.validate_config(bad2)["ok"] is False
    bad3 = {**cfg, "steps": [{"id": "x", "名称": "n", "插件": "魔法:不存在#1", "库槽": "core-rdb:ledger#1"}]}
    r3 = K.validate_config(bad3)
    assert r3["ok"] is False and any("插件族不在目录内" in e for e in r3["errors"])
    dup = {**cfg, "steps": [cfg["steps"][0], cfg["steps"][0]]}
    assert K.validate_config(dup)["ok"] is False
    extra = {**cfg, "steps": [{**cfg["steps"][0], "多余": 1}]}
    assert K.validate_config(extra)["ok"] is False


def test_configure_all_refuses_to_write_bad_config(monkeypatch):
    ok = K.configure_all()
    assert ok["ok"] and len(ok["配置"]) == 3 and ok["不合格"] == []
    # 人为破坏 schema → 必须拒绝落盘（宁可不写，也不写一份乱的）
    monkeypatch.setitem(K.CONFIG_SCHEMA, "version", (str, None, None))
    bad = K.configure_all()
    assert bad["ok"] is False and bad["不合格"]
    monkeypatch.setitem(K.CONFIG_SCHEMA, "version", (int, None, None))


# ═══════════ ③ 部署登记 / 固化 / 回滚 ═══════════
def test_deploy_all_registers_and_solidifies():
    d = K.deploy_all()
    assert d["ok"] and d["登记条数"] == 20 and d["步骤总数"] == 20
    assert d["固化"]["ok"] and d["固化"]["name"] == "kit_packs"
    kinds = J.summary()["by_kind"]
    assert kinds["deploy"] == 20 and kinds["solidify"] == 1
    # 部署是幂等的：再跑一次不新增"新增"记录，只多 deploy 记录
    K.deploy_all()
    assert J.summary()["by_kind"]["deploy"] == 40
    # 固化可回滚
    K.register(note="第二版")
    from core import solidify as S
    rb = S.rollback("kit_packs")
    assert rb["ok"] and rb["verified"] is True


# ═══════════ ④ 变更日志：按 scope 分仓 ═══════════
def test_scan_records_and_scopes_are_isolated():
    from core import pipelines as P
    P.deploy_all()
    P.scan()
    k1 = K.scan()
    assert k1["items"] == 20 and len(k1["added"]) == 20
    # 两套扫描共享一个快照文件，但按 scope 分仓 → 互不干扰
    assert J.summary()["scopes"] == ["kit_packs×cloud_plugins",
                                     "pipelines×cloud_plugins×tentacles"]
    p2 = P.scan()
    k2 = K.scan()
    for got in (p2, k2):
        assert got["added"] == [] and got["changed"] == [] and got["removed"] == []


def test_scan_detects_modify_and_remove_with_where():
    K.scan()                                            # 首次清点
    step = [s for p in K.PACKS if p.id == "capcut" for s in p.步骤 if s.id == "cp1"][0]
    old = step.细节
    step.细节 = "改过的细节"
    try:
        got = K.scan()
    finally:
        step.细节 = old
    assert "kit:capcut/cp1" in got["changed"], got["changed"][:3]
    rows = J.recent(200)["rows"]
    mods = [r for r in rows if r["kind"] == "modify" and r["where"] == "kit:capcut/cp1"]
    assert mods and mods[0]["before"]["细节"] == old
    assert mods[0]["after"]["细节"] == "改过的细节"
    # 删一步 → 记成"消失"（ok=False + 原因）
    pack = [p for p in K.PACKS if p.id == "comfyui"][0]
    saved = pack.步骤
    pack.步骤 = saved[:-1]
    try:
        gone = K.scan()
    finally:
        pack.步骤 = saved
    assert any("cf6" in x for x in gone["removed"]), gone["removed"][:3]
    recs = [r for r in J.recent(300)["rows"]
            if r["kind"] == "modify" and "cf6" in r["where"] and not r["ok"]]
    assert recs and "消失" in recs[0]["reason"]


def test_status_reports_real_numbers():
    K.deploy_all()
    st = K.status()
    assert len(st["包"]) == 3
    assert st["配置校验"]["ok"] is True and st["配置校验"]["包数"] == 3
    assert st["审计"]["步骤总数"] == 20 and st["审计"]["问题"] == []
    assert st["固化"]["kit_packs"]
    assert st["变更日志"]["total"] >= 20
