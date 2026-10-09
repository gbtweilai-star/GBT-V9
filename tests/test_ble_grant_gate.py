"""蓝牙写路径的授权闸门测试（授权=唯一闸门）。

为什么单独守：曾经 `_grant_ok` 只认"自带 verify_action(dict) 的适配器"形态，
而 `core.gui_grant.issue()` 正规签发出来的是**令牌字符串** → 每次都 TypeError
被 except 吞成"未授权"，写路径等于永久焊死（接线参数种类错的隐蔽断线）。
这里把四种形态都钉住：无授权拒 / 正规令牌放行 / 越权令牌拒 / 适配器仍兼容。
"""

import pytest

from core.gui_grant import issue
from senses import ble as sb


def test_no_grant_is_refused():
    ok, why = sb._grant_ok(None, "gatt_write", "AA:BB:CC:DD")
    assert ok is False and "未授权" in why


def test_issued_token_is_accepted():
    """正规签发的令牌必须放行——否则写路径永远打不开。"""
    tok = issue(scope=["ble"], ttl=300, primitives=["ble.write"], apps=["ble"],
                note="单测用短期令牌")
    ok, why = sb._grant_ok(tok, "gatt_write", "AA:BB:CC:DD")
    assert ok is True, why


def test_wrong_scope_token_is_denied_with_reason():
    tok = issue(scope=["fs"], ttl=300, primitives=["fs.write"], apps=["fs"],
                note="越权：不该放行蓝牙写")
    ok, why = sb._grant_ok(tok, "gatt_write", "AA:BB:CC:DD")
    assert ok is False and "grant_scope_denied" in why


def test_adapter_shape_still_supported():
    class Adapter:
        def verify_action(self, d):
            return (d.get("action") == "gatt_write", "")

    ok, _ = sb._grant_ok(Adapter(), "gatt_write", "AA:BB:CC:DD")
    assert ok is True
    ok2, _ = sb._grant_ok(Adapter(), "other", "AA:BB:CC:DD")
    assert ok2 is False


def test_expired_token_is_denied():
    tok = issue(scope=["ble"], ttl=-10, primitives=["ble.write"], apps=["ble"],
                note="过期令牌")
    ok, why = sb._grant_ok(tok, "gatt_write", "AA:BB:CC:DD")
    assert ok is False


def test_load_grant_prefers_explicit_then_env(monkeypatch):
    """面板不传 grant 时，ble_control.load_grant 必须能从环境变量/落盘文件取到
    —— 否则"主人签了授权也照样被拒"，写路径等于没接线。"""
    from core import ble_control as bc

    tok = issue(scope=["ble"], ttl=300, primitives=["ble.write"], apps=["ble"],
                note="load_grant unit")
    monkeypatch.setenv("V9_BLE_GRANT", tok)
    assert bc.load_grant() == tok, "环境变量里的令牌必须被取到"
    assert bc.load_grant("explicit") == "explicit", "显式传入优先"


def test_end_to_end_gate_with_loaded_grant(monkeypatch):
    """签发 → load_grant → 闸门放行：整条链真通（不靠人肉传参）。"""
    from core import ble_control as bc

    tok = issue(scope=["ble"], ttl=300, primitives=["ble.write"], apps=["ble"],
                note="end to end")
    monkeypatch.setenv("V9_BLE_GRANT", tok)
    ok, why = sb._grant_ok(bc.load_grant(), "gatt_write", "AA:BB:CC:DD")
    assert ok is True, why
