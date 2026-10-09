# tests/test_tentacle_fleet.py —— 触手编队：统一密钥 + 指挥闸门（≥100 根）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 锁死本轮指令的两条硬约束：
#   ① 100 根触手与主脑共用【同一把】密钥（只暴露指纹；源码零凭据字面量）
#   ② 每根触手每次驱动都必须带【指挥官签发的一次性工单】，否则拒绝
import asyncio
import os
import sqlite3

import pytest

from core.tentacle_fleet import (COMMANDER_NAME, KEY_ENV_NAMES, OrderError,
                                 OrderGate, TentacleFleet, UnifiedKey,
                                 key_fingerprint)


# ── 假 LLM（真接口形状，不联网）──
class _Msg:
    def __init__(self, content): self.content = content


class _Choice:
    def __init__(self, content): self.message = _Msg(content)


class _Usage:
    total_tokens = 37


class _Resp:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class FakeCompletions:
    def __init__(self, outer): self.outer = outer

    def create(self, **kw):
        self.outer.calls.append(kw)
        if self.outer.raise_with:
            raise self.outer.raise_with
        return _Resp('{"ok": true, "step": "done"}')


class FakeChat:
    def __init__(self, outer):
        self.completions = FakeCompletions(outer)


class FakeClient:
    """假客户端：记录"谁被驱动、用的哪把钥匙"，便于断言统一密钥与审计。"""

    def __init__(self, tentacle, calls=None, raise_with=None, key=None):
        self.tentacle = tentacle
        self.calls = calls if calls is not None else []
        self.raise_with = raise_with
        self.key = key
        self.chat = FakeChat(self)


@pytest.fixture
def key_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "tk-unified-" + "a" * 20)   # 假钥匙，仅测试进程
    monkeypatch.setenv("GBT_ORDER_SECRET", "order-secret-for-test")
    return "tk-unified-" + "a" * 20


@pytest.fixture
def fleet(key_env):
    calls = []
    f = TentacleFleet(ledger=None,
                      client_factory=lambda t: FakeClient(t, calls))
    f._calls = calls
    return f


# ═══ ① 统一密钥：100 根全部同一把 ═══
def test_default_fleet_has_at_least_100_tentacles(fleet):
    st = fleet.status()
    assert st["n"] >= 100
    assert st["same_key"] is True                    # ★统一密钥
    assert st["key_loaded"] is True
    assert st["key_id"] == key_fingerprint(os.environ["OPENAI_API_KEY"])
    assert st["key_source"] == "OPENAI_API_KEY"      # 与主脑同一把的来源
    assert len({t.key.key_id for t in fleet.tentacles.values()}) == 1
    assert st["commander"] == COMMANDER_NAME


def test_fleet_size_configurable(monkeypatch, key_env):
    f = TentacleFleet(ledger=None, n=128, client_factory=lambda t: FakeClient(t))
    assert f.status()["n"] == 128
    assert sorted(f.tentacles)[0] == "t001" and sorted(f.tentacles)[-1] == "t128"


def test_missing_key_is_explicit_not_faked(monkeypatch):
    for name in KEY_ENV_NAMES:
        monkeypatch.delenv(name.strip(), raising=False)
    k = UnifiedKey()
    assert k.loaded is False and k.key_id == ""
    with pytest.raises(RuntimeError, match="统一密钥缺失"):
        k.client_for("t001")


def test_all_tentacles_use_the_same_client_key(fleet):
    """同 key → 每个触手构造出的客户端用的是同一把（指纹一致）。"""
    keys = {fleet.tentacles[t].key.key_id for t in fleet.tentacles}
    assert keys == {fleet.key.key_id}


# ═══ ② 指挥闸门：没有工单不许驱动 ═══
def test_drive_without_order_is_refused_when_auto_order_off(fleet):
    r = fleet.drive("t001", "自己偷偷干活", auto_order=False)
    assert r["ok"] is False and r["reason"] == "no_order"
    assert fleet._calls == [] and fleet.tentacles["t001"].drives == 0


def test_drive_with_commander_order_works_and_is_audited(fleet):
    r = fleet.drive("t007", "扫描 src 目录并返回 JSON")
    assert r["ok"] is True and r["tentacle"] == "t007"
    assert r["order"] and r["trace_id"]
    assert fleet.tentacles["t007"].drives == 1
    assert fleet._calls and fleet._calls[0]["model"]                 # 真发了请求
    st = fleet.status()
    assert st["drives"] == 1 and st["tokens"] >= 1


def test_order_must_come_from_commander(fleet):
    # ① 改签发者 = 篡改 → 签名先炸
    order = fleet.order("t002", "任务")
    order["issued_by"] = "某个自封的触手"
    assert fleet.gate.verify(order, tentacle_id="t002")[0] is False
    assert fleet.drive("t002", "x", order=order)["ok"] is False
    # ② 签名正确、但签发者不是指挥官 → 也要拒（拿到密钥也不许自封指挥官）
    forged = {"order_id": "self-1", "tentacle_id": "t002", "task": "自封指挥官",
              "issued_by": "自封的触手", "issued_at": 0, "expires_at": 2 ** 31,
              "trace_id": "tr-self"}
    forged["sig"] = fleet.gate._sign(forged)
    ok, why = fleet.gate.verify(forged, tentacle_id="t002")
    assert ok is False and "not_from_commander" in why


def test_tampered_order_refused(fleet):
    order = fleet.order("t003", "原任务")
    order["task"] = "改成删库跑路"
    r = fleet.drive("t003", "x", order=order)
    assert r["ok"] is False and "bad_signature" in r["reason"]


def test_order_expires(fleet, monkeypatch):
    order = fleet.order("t004", "任务", ttl=-1)          # 已过期
    r = fleet.drive("t004", "x", order=order)
    assert r["ok"] is False and r["reason"] == "order_expired"


def test_order_is_one_time(fleet):
    order = fleet.order("t005", "任务")
    assert fleet.drive("t005", "x", order=order)["ok"] is True
    second = fleet.drive("t005", "x", order=order)
    assert second["ok"] is False and "order_replayed" in second["reason"]


def test_order_bound_to_one_tentacle(fleet):
    order = fleet.order("t006", "任务")
    r = fleet.drive("t009", "x", order=order)            # 拿 t006 的单去驱动 t009
    assert r["ok"] is False and r["reason"] == "order_for_other_tentacle"


# ═══ 配额：同 key 下唯一能做的自律 ═══
def test_rpm_quota_blocks_and_is_reported(fleet):
    fleet.tentacles["t010"].rpm = 1
    assert fleet.drive("t010", "第一次")["ok"] is True
    second = fleet.drive("t010", "第二次")
    assert second["ok"] is False and "rpm_exceeded" in second["reason"]
    assert fleet.tentacles["t010"].failures >= 1


# ═══ LLM 报错不许伪装成功 ═══
def test_llm_error_reported_not_swallowed(key_env):
    class Boom(FakeClient):
        def __init__(self, t, calls):
            super().__init__(t, calls, raise_with=RuntimeError("gateway 429"))

    f = TentacleFleet(ledger=None, client_factory=lambda t: Boom(t, []))
    r = f.drive("t011", "任务")
    assert r["ok"] is False and r["reason"].startswith("llm_error:")


# ═══ 批量驱动：每根仍逐单过闸门 ═══
def test_drive_many_every_tentacle_gets_its_own_order(fleet):
    out = fleet.drive_many(lambda tid: f"任务给 {tid}", limit=5)
    assert out["driven"] == 5 and out["ok"] == 5
    ids = {r["order"] for r in out["results"]}
    assert len(ids) == 5                                  # 一单一根，不共用


# ═══ 账本落账：每次驱动可查 ═══
def test_drive_is_written_to_ledger(tmp_path, key_env):
    from audit.ledger import Ledger
    led = Ledger(db=str(tmp_path / "fleet.db"))
    try:
        f = TentacleFleet(ledger=led, n=3, client_factory=lambda t: FakeClient(t))
        r = f.drive("t001", "写账本的任务")
        assert r["ok"] is True and r["logged"] is True      # 审计真写进去了
        rows = f.table(5)
        assert rows and rows[0]["tentacle_id"] == "t001" and rows[0]["ok"] == 1
        assert rows[0]["key_id"] == f.key.key_id
        assert rows[0]["issued_by"] == COMMANDER_NAME
    finally:
        led.close()


# ═══ 工单闸门本体 ═══
def test_gate_requires_signature_and_commitment():
    g = OrderGate(secret="s" * 10)
    ok, why = g.verify(None)
    assert ok is False and "missing_order" in why
    ok, why = g.verify({"order_id": "x", "tentacle_id": "t1", "issued_by": COMMANDER_NAME,
                        "expires_at": 2 ** 31, "sig": "deadbeef"})
    assert ok is False and "bad_signature" in why
