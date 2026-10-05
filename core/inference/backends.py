# core/inference/backends.py —— 可替换推理后端 + 路由降级
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 诚实点: vLLM 的 OpenAI 兼容是"接口兼容", 不等于行为完全一致 → 先探能力再调用
import os, json, time, threading, urllib.request, urllib.error
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable
from senses.sqldialect import txn


@dataclass
class BackendHealth:
    ok: bool
    detail: dict = field(default_factory=dict)
    error: str = ""


@runtime_checkable
class InferenceBackend(Protocol):
    name: str
    def chat(self, messages, *, model=None, stream=False, **params): ...
    def probe(self) -> BackendHealth: ...
    def metrics(self) -> dict: ...


def _req(url, data=None, headers=None, method=None, timeout=60, raw=False):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=(json.dumps(data).encode() if data and not raw
                                            else data), headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        return body if raw else json.loads(body or b"{}")


class VLLMBackend:
    """vLLM 服务（默认 :8000）"""
    name = "vllm"
    def __init__(self, base_url=None, model=None, api_key="EMPTY",
                 timeout=120, max_concurrency=None):
        self.base = (base_url or os.environ.get("VLLM_BASE",
                                               "http://127.0.0.1:8000")).rstrip("/")
        self.model = model or os.environ.get("VLLM_MODEL")
        self.h = {"Authorization": f"Bearer {api_key}"}
        self.timeout = timeout
        # 并发上限走部署配置, 不让面板随意改
        self._sem = threading.BoundedSemaphore(
            int(max_concurrency or os.environ.get("VLLM_MAX_CONCURRENCY", 8)))
        self.default_model = self.model

    def probe(self) -> BackendHealth:
        try:
            _req(f"{self.base}/health", headers=self.h, timeout=5)
            models = _req(f"{self.base}/v1/models", headers=self.h, timeout=5)
            ids = [m.get("id") for m in models.get("data", [])]
            return BackendHealth(True, {"models": ids, "base": self.base})
        except Exception as e:
            return BackendHealth(False, {"base": self.base}, str(e))

    def metrics(self) -> dict:
        """读 Prometheus /metrics 文本，只提取关键 gauge（不引入 prometheus_client）"""
        try:
            txt = _req(f"{self.base}/metrics", headers=self.h,
                       timeout=5, raw=True).decode("utf-8", "ignore")
        except Exception as e:
            return {"error": str(e)}
        keys = ("vllm:num_requests_running", "vllm:num_requests_waiting",
                "vllm:gpu_cache_usage_perc", "vllm:avg_prompt_throughput_toks_per_s",
                "vllm:avg_generation_throughput_toks_per_s")
        out = {}
        for line in txt.splitlines():
            if line.startswith("#"):
                continue
            for k in keys:
                if line.startswith(k):
                    try:
                        out[k] = float(line.rsplit(" ", 1)[1])
                    except Exception:
                        pass
        return out

    def chat(self, messages, *, model=None, stream=False, **params):
        m = model or self.default_model
        if not m:
            h = self.probe()
            if not h.ok:
                raise RuntimeError(f"vLLM 不可用: {h.error}")
            m = (h.detail.get("models") or [None])[0]
            self.default_model = m
        body = {"model": m, "messages": messages, "stream": bool(stream)}
        body.update({k: v for k, v in params.items() if v is not None})
        with self._sem:                            # 有界并发, 防打爆
            if not stream:
                r = _req(f"{self.base}/v1/chat/completions", body, self.h,
                         timeout=self.timeout)
                return r["choices"][0]["message"]["content"]
            return self._stream(body)

    def _stream(self, body):
        req = urllib.request.Request(f"{self.base}/v1/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={**self.h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    delta = json.loads(payload)["choices"][0]["delta"]
                    yield delta.get("content") or ""
                except Exception:
                    continue


class GatewayBackend:
    """现有 OpenAI 兼容网关（CLIProxyAPI 等）"""
    name = "gateway"
    def __init__(self, base_url=None, api_key=None, model=None, timeout=120):
        self.base = (base_url or os.environ.get("OPENAI_BASE_URL",
                                               "http://127.0.0.1:8317/v1")).rstrip("/")
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "tk-brain-000")
        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.h = {"Authorization": f"Bearer {self.key}"}
        self.timeout = timeout

    def probe(self) -> BackendHealth:
        try:
            _req(f"{self.base}/models", headers=self.h, timeout=5)
            return BackendHealth(True, {"base": self.base})
        except Exception as e:
            return BackendHealth(False, {"base": self.base}, str(e))

    def metrics(self) -> dict:
        return {}

    def chat(self, messages, *, model=None, stream=False, **params):
        body = {"model": model or self.model, "messages": messages,
                "stream": bool(stream)}
        body.update({k: v for k, v in params.items() if v is not None})
        if not stream:
            r = _req(f"{self.base}/chat/completions", body, self.h,
                     timeout=self.timeout)
            return r["choices"][0]["message"]["content"]
        return self._stream(body)

    def _stream(self, body):
        req = urllib.request.Request(f"{self.base}/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={**self.h, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    yield json.loads(payload)["choices"][0]["delta"].get("content") or ""
                except Exception:
                    continue


class OllamaBackend:
    """本机 Ollama 兜底"""
    name = "ollama"
    def __init__(self, base_url=None, model=None, timeout=180):
        self.base = (base_url or os.environ.get("OLLAMA_HOST",
                                                "http://127.0.0.1:11434")).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        self.timeout = timeout

    def probe(self) -> BackendHealth:
        try:
            t = _req(f"{self.base}/api/tags", timeout=5)
            return BackendHealth(True, {"models": [m.get("name") for m in
                                                    t.get("models", [])]})
        except Exception as e:
            return BackendHealth(False, {"base": self.base}, str(e))

    def metrics(self) -> dict:
        return {}

    def chat(self, messages, *, model=None, stream=False, **params):
        body = {"model": model or self.model, "messages": messages,
                "stream": bool(stream)}
        if stream:
            return self._stream(body)
        r = _req(f"{self.base}/api/chat", body, timeout=self.timeout)
        return r.get("message", {}).get("content", "")

    def _stream(self, body):
        req = urllib.request.Request(f"{self.base}/api/chat",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            for raw in r:
                try:
                    yield json.loads(raw.decode("utf-8", "ignore")).get(
                        "message", {}).get("content") or ""
                except Exception:
                    continue


class InferenceRouter:
    """按优先级尝试，逐级降级；任何降级都记账，不伪报成功"""
    def __init__(self, backends, ledger=None, brain=None):
        self.backends = backends
        self.led, self.brain = ledger, brain
        self._last = None

    def probe_all(self):
        return {b.name: b.probe() for b in self.backends}

    def active(self):
        return self._last.name if self._last else None

    name, version = "inference", "runtime-1.0"

    def spec(self) -> dict:
        return {
            "inputs": {
                "messages": {"type": "array", "required": True,
                             "help": "OpenAI 兼容消息数组"},
                "model": {"type": "string"},
                "stream": {"type": "boolean", "default": False},
            },
            "outputs": {"text": {"type": "string"}},
            "idempotent": False, "risk": "low",
        }

    def chat(self, messages, **kw):
        last_err = None
        for i, b in enumerate(self.backends):
            try:
                out = b.chat(messages, **kw)
                if getattr(out, "__iter__", None) and not isinstance(out, (str, bytes)):
                    return out                          # 流式直接透传
                self._last = b
                if i > 0:
                    self._record("degraded", b.name, f"从 {self.backends[0].name} 降级")
                return out
            except Exception as e:
                last_err = f"{b.name}: {e}"
                self._record("failed", b.name, str(e))
        raise RuntimeError(f"全部推理后端失败: {last_err}")

    def _record(self, status, backend, err):
        if not self.led:
            return
        try:
            with txn(self.led) as cur:
                d = self.led.dialect
                ph = "?" if d == "sqlite" else "%s"
                cur.execute(f"""CREATE TABLE IF NOT EXISTS inference_events(
                    ts {"REAL" if d=='sqlite' else 'DOUBLE PRECISION'},
                    backend TEXT, status TEXT, detail TEXT)""")
                cur.execute(f"INSERT INTO inference_events VALUES({','.join([ph]*4)})",
                            (time.time(), backend, status, err[:500]))
        except Exception:
            pass


def default_router(ledger=None, brain=None):
    order = os.environ.get("INFER_ORDER", "vllm,gateway,ollama").split(",")
    pool = {"vllm": VLLMBackend, "gateway": GatewayBackend, "ollama": OllamaBackend}
    return InferenceRouter([pool[n.strip()]() for n in order if n.strip() in pool],
                           ledger=ledger, brain=brain)
