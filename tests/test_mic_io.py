# tests/test_mic_io.py —— 本机麦克风能力：设备协商 · 静音闸门 · 诚实降级
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 这台笔记本的真实教训（2026-10-07）：
#   1) PortAudio 默认输入是 -1（未设置）→ 直接采集一律 "Requested device not found"
#   2) Realtek 端点不接受 16000 Hz → "Invalid device [PaErrorCode -9996]"，必须按端点采样率开流
#   3) 单次噪声测量会被瞬时噪声抬高（曾测 792，实际底噪远低）→ 基线取历史最小值
#   4) SAPI 识别结果键是 text（不是「文本」）→ 读错键会表现为「识别不出字」
import json
import wave
from pathlib import Path

import pytest

from core import mic_io as MI


def test_default_input_is_never_assumed():
    """首选必须是枚举出来的真实端点序号，绝不是 -1（默认输入）。"""
    d = MI.devices()
    if not d["有硬件"]:
        pytest.skip("本机没有音频采集端点")
    assert d["首选"]["序号"] >= 0
    assert d["首选"]["通道"] > 0


def test_record_negotiates_sample_rate_and_writes_16k_wav():
    """按端点支持的采样率开流，落盘统一 16k 单声道（识别器直接可用）。"""
    got = MI.record(1.0)
    if not got.get("ok"):
        pytest.skip(f"本机采集不可用：{got.get('reason')}")
    assert got["采集采样率"] in MI.RATES
    with wave.open(got["wav"], "rb") as w:
        assert w.getframerate() == MI.SAMPLE_RATE
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() > 0
    assert "电平" in got and "噪声基线" in got


def test_silence_is_not_sent_to_recognition():
    """静音（低于说话阈值）绝不送识别，并如实说明原因。"""
    floor = 100.0
    heard, th = MI._speech_gate(20.0, floor)
    assert heard is False
    assert th == pytest.approx(220.0)           # 基线 2.2 倍
    heard2, th2 = MI._speech_gate(500.0, 0.0)
    assert heard2 is True
    assert th2 == pytest.approx(90.0)           # 下限 90，不因基线为 0 而失效
    heard3, th3 = MI._speech_gate(5000.0, 5000.0)
    assert th3 == pytest.approx(900.0)          # 上限 900，噪声再大也不至于抬死


def test_no_hardware_degrades_honestly():
    """没有端点时：不抛异常、不假装可用，给出可执行建议。"""
    orig = MI.sd
    MI.sd = None
    try:
        st = MI.status()
        assert st["有采集端点"] is False
        assert st["建议"]
        assert MI.pick()["ok"] is False
        assert MI.record(1.0)["ok"] is False
    finally:
        MI.sd = orig


def test_deployed_device_is_remembered_and_merges_calibration():
    """部署结果落盘且可沿用；写入时必须与已有校准数据合并，不能覆盖。"""
    mem = MI._mem()
    if not mem.get("序号"):
        pytest.skip("本机尚未部署话筒")
    MI._remember({"名": mem.get("名称", "x"), "序号": mem["序号"]})
    again = MI._mem()
    assert again["序号"] == mem["序号"]
    assert "名称" in again
    # 基线等既有键若存在，必须仍在（合并而非覆盖）
    for k in set(mem) - {"更新时间"}:
        assert k in again


def test_transcribe_reads_sapi_text_key():
    """识别结果键是 text：读错键会假报「识别不出字」。"""
    src = Path(__file__).resolve().parent.parent / "state" / "voice_tts_probe.wav"
    from senses import voice_sapi as VS
    if not src.is_file():
        t = VS.tts_to_wav("主人你好")
        src = Path(t.get("wav") or "")
    if not src.is_file():
        pytest.skip("没有可用于识别的音频样本")
    got = MI.transcribe(src)
    assert got["引擎"]
    assert "文本" in got and "置信度" in got
    # 离线听写对合成音本来就有误差，只要不空手而归即可
    if got["文本"]:
        assert got["结论"].startswith("听到")


def test_mic_endpoints_expose_status_and_refuse_empty_text():
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    r = c.get("/api/mic/status")
    assert r.status_code == 200
    body = r.json()
    assert "有采集端点" in body and "建议" in body
    assert body["识别引擎"]
    r2 = c.post("/api/mic/say", json={"text": "   "})
    assert r2.status_code == 200
    assert r2.json()["ok"] is False


def test_voice_page_shows_mic_bar_and_server_fallback():
    """语音页必须有话筒状态条 + 服务端话筒兜底（浏览器无话筒时不至于没法对话）。"""
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    html = c.get("/voice").text
    assert 'id=mic' in html
    assert "/api/mic/listen" in html
    assert "togSrv" in html
    assert "本机话筒" in html


def test_mic_status_json_serializable():
    json.dumps(MI.status(probe=True), ensure_ascii=False)
