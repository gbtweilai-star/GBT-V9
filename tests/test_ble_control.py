# tests/test_ble_control.py —— 蓝牙操控：多源合并 / 授权闸门 / 审计追加 / AI 决策兜底
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏来源（真实项目，MIT）：Hypijump31/bluetooth-mcp-server（23★，Python）
#   —— 取其「Windows 多源枚举 + 把扫描做成 AI 可调用工具面」的架构，代码全部自写。
#
# 覆盖：
#   ① 多源合并（注册表 / PnP / 射频）按 MAC 归并，逐条标注来源；某源失败不影响其他源
#   ② 权限：写操作没有 Grant 一律拒绝；有 Grant 才继续（本测试用假 Grant 验证调用路径）
#   ③ 数据校验：非十六进制 / 空数据一律拒发（不把坏数据丢给设备）
#   ④ 审计：追加式 JSONL，只增不改；坏行跳过并计数；概览数字真实
#   ⑤ 决策：无统一密钥时用本地规则并**如实标注** decided_by=rules（不冒充模型）
#   ⑥ 算力路由：ble.decide 必须映射到真实云插件槽 + 真实库槽
import json

import pytest

from core import ble_audit as ba
from core import ble_control as bc
from core import compute_router as cr
from senses import ble as sb


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path, monkeypatch):
    """审计文件指到临时目录：不污染仓库 state/。测试里不自己拼路径，全走模块 API。"""
    monkeypatch.setattr(ba, "STATE_DIR", str(tmp_path / "state"))
    yield


# ═══════════ ① 多源合并 ═══════════
def test_norm_mac_and_vendor_are_honest():
    assert sb.norm_mac("5bff520e1cba") == "5B:FF:52:0E:1C:BA"
    assert sb.norm_mac("5B:FF:52:0E:1C:BA") == "5B:FF:52:0E:1C:BA"
    assert sb.norm_mac("不是MAC") == "不是MAC"          # 不规范就原样返回，不硬造
    assert sb.vendor_of("7C:DF:A1:11:22:33") == "Espressif（ESP32）"
    assert sb.vendor_of("FF:FF:FF:FF:FF:FF") == "未知"   # 表里没有 → 未知，不猜


def test_scan_merges_sources_and_reports_errors(monkeypatch):
    monkeypatch.setattr(sb, "scan_registry", lambda **k: {
        "ok": True, "reason": "", "devices": [
            {"mac": "AA:BB:CC:DD:EE:01", "name": "灯泡", "kind": "paired",
             "status": "paired", "service_uuid": None, "instance": None,
             "source": "registry", "vendor": sb.vendor_of("AA:BB:CC:DD:EE:01")}]})
    monkeypatch.setattr(sb, "scan_pnp", lambda **k: {
        "ok": True, "reason": "", "devices": [
            {"mac": "AA:BB:CC:DD:EE:01", "name": None, "kind": "ble", "status": "OK",
             "service_uuid": "0x180F", "instance": "BTHLEDEVICE", "source": "pnp",
             "vendor": "未知"},
            {"mac": "AA:BB:CC:DD:EE:02", "name": "手环", "kind": "ble", "status": "OK",
             "service_uuid": None, "instance": "BTHLEDEVICE2", "source": "pnp",
             "vendor": "未知"}]})
    monkeypatch.setattr(sb, "scan_ble", lambda *a, **k: {
        "ok": False, "devices": [], "reason": "无适配器"})
    res = sb.scan(duration=0.1)
    assert res["ok"] and res["count"] == 2
    one = [d for d in res["devices"] if d["mac"] == "AA:BB:CC:DD:EE:01"][0]
    assert set(one["sources"]) == {"registry", "pnp"}      # 同 MAC 归并
    assert one["name"] == "灯泡"                            # 后到的不覆盖已有名字
    assert one["kind"] == "ble"                             # 有 ble 就报 ble
    # 失败源如实列出原因，不静默吞掉
    assert res["errors"] and res["errors"][0]["source"] == "bleak"
    assert res["sources"] == ["registry", "pnp"]


def test_scan_pnp_parses_real_shape(monkeypatch):
    payload = json.dumps([
        {"Status": "OK", "FriendlyName": "RZ608 Bluetooth(R) Adapter",
         "InstanceId": "USB\\VID_0E8D&PID_0608&MI_00\\7&330F77BF&0&0000"},
        {"Status": "OK", "FriendlyName": "iPhone",
         "InstanceId": "BTHENUM\\DEV_1C71252DFC8B\\9&21FBA506&0&BLUETOOTHDEVICE_1C71252DFC8B"},
        {"Status": "OK", "FriendlyName": "蓝牙 LE 通用属性服务",
         "InstanceId": "BTHLEDEVICE\\{00001805-0000-1000-8000-00805F9B34FB}_54423D4B165A\\A&1&0&001D"}])
    monkeypatch.setattr(sb, "_ps", lambda script, timeout: (0, payload, ""))
    res = sb.scan_pnp()
    assert res["ok"] and len(res["devices"]) == 3
    kinds = {d["kind"] for d in res["devices"]}
    assert kinds == {"adapter", "paired", "ble"}
    iphone = [d for d in res["devices"] if d["name"] == "iPhone"][0]
    assert iphone["mac"] == "1C:71:25:2D:FC:8B" and iphone["vendor"] == "Apple"
    ble_dev = [d for d in res["devices"] if d["kind"] == "ble"][0]
    assert ble_dev["mac"] == "54:42:3D:4B:16:5A" and ble_dev["service_uuid"].startswith("00001805")
    assert sb.adapters()["count"] == 1


def test_scan_pnp_failure_is_reported_not_empty(monkeypatch):
    monkeypatch.setattr(sb, "_ps", lambda script, timeout: (1, "", "拒绝访问"))
    res = sb.scan_pnp()
    assert res["ok"] is False and res["devices"] == [] and "rc=1" in res["reason"]


# ═══════════ ②③ 授权闸门与数据校验 ═══════════
def test_write_without_grant_is_refused():
    res = sb.gatt_write("AA:BB:CC:DD:EE:FF", "uuid-1", "01", grant=None)
    assert res["ok"] is False and "未授权" in res["reason"]


def test_write_rejects_bad_hex_even_with_grant():
    class G:
        def verify_action(self, action):
            return {"ok": True, "reason": ""}
    res = sb.gatt_write("AA:BB:CC:DD:EE:FF", "uuid-1", "ZZZZ", grant=G())
    assert res["ok"] is False and "十六进制" in res["reason"]
    res2 = sb.gatt_write("AA:BB:CC:DD:EE:FF", "uuid-1", "", grant=G())
    assert res2["ok"] is False and "空数据" in res2["reason"]


def test_read_also_goes_through_grant_gate():
    res = sb.gatt_read("AA:BB:CC:DD:EE:FF", "uuid-1", grant=None)
    assert res["ok"] is False and "未授权" in res["reason"]


def test_apply_plan_write_requires_target_and_grant():
    no_target = bc.apply_plan({"action": "write", "target": None})
    assert no_target["ok"] is False and "缺少目标设备" in no_target["reason"]
    dry = bc.apply_plan({"action": "write", "target": "AA:BB:CC:DD:EE:FF",
                         "uuid": "u", "data_hex": "01"}, dry_run=True)
    assert dry["ok"] is True and dry.get("dry_run") is True and "演练" in dry["reason"]


def test_apply_plan_unknown_action_is_refused_and_audited():
    res = bc.apply_plan({"action": "删除一切"})
    assert res["ok"] is False and "未知动作" in res["reason"]
    got = bc.history(5)
    assert got["ok"] and got["rows"][0]["action"] == "删除一切" and got["rows"][0]["ok"] is False


# ═══════════ ④ 审计：追加式 ═══════════
def test_audit_is_append_only_and_summary_is_real():
    ba.append({"action": "scan", "ok": True, "decided_by": "rules"})
    ba.append({"action": "write", "target": "AA:BB:CC:DD:EE:FF", "ok": False,
               "reason": "未授权", "decided_by": "rules"})
    ba.append({"action": "scan", "ok": True, "decided_by": "api"})
    got = ba.read_all()
    assert got["ok"] and len(got["rows"]) == 3
    s = ba.summary()
    assert (s["total"], s["success"], s["failed"]) == (3, 2, 1)
    assert len(ba.recent(1)["rows"]) == 1                  # 条数在 Python 侧切片
    ba.append({"action": "status", "ok": True})            # 追加不改历史
    assert ba.recent(99)["count"] == 4


def test_audit_skips_bad_lines_and_counts_them(monkeypatch):
    """坏行（比如半截写入）要被跳过并计数——通过模块 API 制造，不自己碰路径。"""
    real_dumps = json.dumps
    monkeypatch.setattr(
        ba.json, "dumps",
        lambda obj, **kw: "{坏行" if obj.get("action") == "broken" else real_dumps(obj, **kw))
    ba.append({"action": "scan", "ok": True})
    ba.append({"action": "broken", "ok": True})
    ba.append({"action": "read", "ok": False})
    got = ba.read_all()
    assert got["ok"] and len(got["rows"]) == 2 and got["bad_lines"] == 1


# ═══════════ ⑤ 决策兜底（如实标注） ═══════════
def test_rule_plan_matches_intents_and_labels_itself():
    assert bc.rule_plan("扫一下周边蓝牙")["action"] == "scan"
    assert bc.rule_plan("蓝牙适配器能用吗")["action"] == "status"
    w = bc.rule_plan("给 AA:BB:CC:DD:EE:FF 开灯")
    assert w["action"] == "write" and w["target"] == "AA:BB:CC:DD:EE:FF"
    assert "rules" in w["decided_by"]                 # 绝不冒充模型
    miss = bc.rule_plan("今天天气怎么样")
    assert miss["ok"] is False and "没匹配上" in miss["reason"]


def test_decide_without_unified_key_uses_rules(monkeypatch):
    class K:
        loaded = False
    monkeypatch.setattr("core.tentacle_fleet.UnifiedKey", lambda *a, **k: K())
    plan = bc.decide("扫一下周边的蓝牙设备")
    assert plan["action"] == "scan" and "rules" in plan["decided_by"]


# ═══════════ ⑥ 算力路由 ═══════════
def test_ble_decide_workload_is_wired_to_real_slots():
    ids = {w.id for w in cr.WORKLOADS}
    assert "ble.decide" in ids
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    w = [x for x in cr.WORKLOADS if x.id == "ble.decide"][0]
    assert w.plugin in set(PLUGIN_IDS) and w.db in set(SLOT_IDS)
    assert cr.audit()["ok"] is True                        # 全部算力活依然零问题
