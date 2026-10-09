# tests/test_local_channel_cpu.py —— 本机通道的根因修复：纯 CPU 原生 API + 非思考型模型 + 去围栏
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机根因（值得锁死）：AMD 核显上 Ollama 走 GPU 卸载会让解码退化 →
#   500「token repeat limit reached」；OpenAI 兼容层 + 思考型模型还会返回空内容。
# 修复三件套：① 原生 ollama API；② options.num_gpu=0（强制纯 CPU）；③ 偏好非思考型模型 + 剥代码围栏。
import json

import pytest

from core import tentacle_fleet as TF


def test_strip_fence_handles_json_code_block():
    raw = '```json\n{"step": "read_frame", "frame": 1}\n```'
    assert json.loads(TF._strip_fence(raw))["frame"] == 1
    assert TF._strip_fence('  ```\n{"a":1}\n```  ') == '{"a":1}'
    assert TF._strip_fence('{"a":1}') == '{"a":1}'          # 没围栏就别动
    assert TF._strip_fence("") == ""


def test_pick_local_model_prefers_non_thinking():
    # 有非思考型 → 必须优先选它（思考型常返空内容）
    got = TF.pick_local_model(["qwen3:0.6b", "qwen2.5:1.5b-instruct", "qwen3:latest"])
    assert got["ok"] and got["model"] == "qwen2.5:1.5b-instruct"
    assert "偏好序" in got["reason"]
    # 偏好序没命中时：仍要绕开思考型
    only_think = TF.pick_local_model(["qwen3:0.6b", "mistral:7b"])
    assert only_think["model"] == "mistral:7b"
    assert TF.pick_local_model([])["ok"] is False


def test_local_client_forces_cpu_and_maps_options(monkeypatch):
    """原生适配器必须把 num_gpu=0 传下去（这就是根因开关），并把返回值映射成 SDK 形状。"""
    seen = {}

    class _FakeOllama:
        @staticmethod
        def chat(**kw):
            seen.update(kw)
            return {"message": {"content": '```json\n{"ok": true}\n```'},
                    "eval_count": 12}

    import sys
    monkeypatch.setitem(sys.modules, "ollama", _FakeOllama)
    cli = TF.local_client()
    assert cli is not None
    resp = cli.chat.completions.create(model="qwen2.5:1.5b-instruct",
                                       messages=[{"role": "user", "content": "hi"}],
                                       temperature=0.8, max_tokens=64)
    assert seen["model"] == "qwen2.5:1.5b-instruct"
    assert seen["options"]["num_gpu"] == 0                   # ★根因开关
    assert seen["options"]["num_predict"] == 64
    assert "repeat_penalty" in seen["options"]
    assert resp.choices[0].message.content.startswith("```json")   # 原样返回，由编队剥围栏
    assert resp.usage.total_tokens == 12


def test_local_client_absent_when_ollama_package_missing(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "ollama", None)
    # 没有 ollama 包时要安全退化（返回 None，由编队回退到 OpenAI SDK 客户端）
    assert TF.local_client() is None


def test_preflight_reports_channel_and_reason():
    f = TF.TentacleFleet(n=1, client_factory=lambda t: None)
    pf = f.preflight()
    assert "可驱动" in pf and "通道" in pf
    # 注入了假客户端 → 通道是 given，不发网络请求
    assert pf["通道"] in ("given", "primary", "free-local", "blocked")
