# tests/test_panel_production_cards.py —— 面板卡片"必须都是能用的生产件"
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 老板的话：不能在面板上有任何"不是生产的、还没推进"的格子。
# 于是这里守三类事：
#   ① 卡片要读的字段，接口必须真给（字段名对不上 → 页面显示 undefined / "无记录"）
#   ② 卡上的按钮必须真能跑出一条**真证据**（听写自证 / 脉冲自检）
#   ③ 通道状态要如实：装了什么说什么，没装就写清怎么装
import os

import pytest


def test_ttl_cache_serves_stale_while_refreshing():
    """面板重活缓存必须"过期先给旧值 + 后台刷新"，否则页面会每个周期卡一次。"""
    import time
    from common.ttl_cache import TTLCache
    calls = []

    def slow():
        calls.append(1)
        time.sleep(0.3)
        return "v%d" % len(calls)

    c = TTLCache(ttl=0.4, name="t")
    assert c.get("k", slow) == "v1"                     # 冷启动：只能等
    t0 = time.time()
    assert c.get("k", slow) == "v1"                     # 命中：立刻
    assert time.time() - t0 < 0.1
    time.sleep(0.5)                                     # 过期
    t0 = time.time()
    assert c.get("k", slow) == "v1"                     # 过期也先给旧值（不卡）
    assert time.time() - t0 < 0.1
    time.sleep(0.5)
    assert c.get("k", slow) == "v2"                     # 后台已经刷新过
    assert c.info()["keys"] == ["k"]


def test_scale_card_fields_are_all_present_for_sqlite():
    """SQLite 后端也要把用量/斜率/ETA/控制器算出来；字段名必须与卡片渲染一致。"""
    os.environ.setdefault("LEDGER_BACKEND", "sqlite")
    os.environ.pop("DATABASE_URL", None)
    from panel.server import api_scale
    d = api_scale()
    assert d["enabled"] is True
    for k in ("used_pct", "used_gb", "capacity_gb", "growth", "eta_days",
              "daemon", "warnings", "controller_why"):
        assert k in d, f"卡片要读 {k}，接口没给 → 页面会显示 undefined"
    assert d["daemon"]["online"] is True                  # 不许渲染成红色"无记录"
    assert isinstance(d["daemon"]["state"], str) and d["daemon"]["state"]
    if d["growth"] is not None:
        assert set(d["growth"]) == {"gb_day", "mb_hour"}


def test_senses_exposes_asr_channel_honestly():
    from panel.server import api_senses
    s = api_senses()
    assert "asr" in s
    a = s["asr"]
    assert a["离线"] is True and a["占显存"] == 0 and a["需凭据"] is False
    assert ("可用" in a) and ("说明" in a)
    if not a["可用"]:
        assert a.get("装中文识别器的方法")
    # 表还没建的时候不许写成"未建（...未启用过）"这种死胡同，要说清怎么产生第一条
    assert "table" not in (s.get("mic") or {})
    assert "table" not in (s.get("voice") or {})


def test_pulse_selftest_plugs_real_sockets_and_logs():
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.capability_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    d = c.post("/api/pulse/selftest").json()
    assert d["ok"] is True
    assert d["插座数"] >= 3
    assert d["插上"], "至少要真插上一个插座"
    assert d["分发"]["handled"] is True
    # 故意放进去的非法类型必须**如实失败**（不是把失败藏起来）
    assert any("not_a_kind" in x for x in d["失败"])


def test_asr_selftest_transcribes_and_writes_a_row():
    from senses import voice_sapi as VS
    if not VS.asr_status(fresh=True)["可用"]:
        pytest.skip("本机没有中文听写识别器")
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.capability_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    d = c.post("/api/asr/selftest", json={"text": "今天天气不错"}).json()
    assert d["ok"] is True and d["转写"]
    assert d["wav_bytes"] > 1024                       # 真合成了音频
    assert "落账失败" not in d                          # 转写记录必须真落账
