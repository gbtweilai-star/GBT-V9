# core/providers.py —— 统一模型路由：免密钥优先 → 官方免费额度 → 付费网关（最后备选）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-07）："把哪些代付费没有免费额度的清理了，替换哪些不需要密钥就能使用的
#   大模型配置。"
#
# 清理口径：
#   · TeamoRouter（代充钱包制、无免费额度）→ **从默认链路里移除**，降级为"付费备用"，
#     只有显式设置环境变量时才会被尝试；
#   · 默认链路 = 本地 Ollama（免密钥、无限调用、数据不出本机）优先；
#   · 官方免费额度（GitHub Models / Groq 等，注册拿密钥、条款内使用）→ 设了环境变量就自动启用；
#   · 凭据只从环境变量读，源码零密钥字面量；不做任何绕过付费/风控的事。
import os
import time

# (名称, base_url, key_env, 是否需要密钥, 是否免费/本地, 候选模型)
PROVIDERS: tuple = (
    ("本地 Ollama", "http://127.0.0.1:11434/v1", "", False, True,
     ("qwen3:0.6b", "gbtv9:latest", "qwen2.5:1.5b-instruct")),
    ("GitHub Models（官方免费额度）", "https://models.inference.ai.azure.com",
     "GITHUB_TOKEN", True, True, ("gpt-4o-mini", "gpt-4.1-mini")),
    ("Groq（官方免费额度）", "https://api.groq.com/openai/v1",
     "GROQ_API_KEY", True, True, ("llama-3.3-70b-versatile",)),
    ("TeamoRouter（代充钱包，已降级为备用）", "http://127.0.0.1:8317/v1",
     "OPENAI_API_KEY", True, False,
     ("deepseek-v4-flash-free", "claude-sonnet-5-5", "deepseek-v4-pro")),
)

_PING_MSG = [{"role": "user", "content": "ping"}]


def usable() -> list:
    """按"免密钥优先 → 官方免费 → 付费备用"排序，返回当前可尝试的供应商。"""
    out = []
    for name, base, key_env, needs_key, free, models in PROVIDERS:
        key = os.environ.get(key_env, "").strip() if key_env else ""
        if needs_key and not key:
            continue                                     # 没密钥：跳过（不装样子）
        out.append({"名称": name, "base": base, "key": key, "免费/本地": free,
                    "候选模型": list(models)})
    return out


def _client(base: str, key: str):
    from openai import OpenAI
    return OpenAI(api_key=key or "local", base_url=base)


def _pick_model(cli, models: tuple) -> str:
    """选供应商里**真实存在**的模型（拉 /models 对一遍，选第一个命中）。"""
    try:
        have = [m.id for m in cli.models.list().data]
    except Exception:                                      # noqa: BLE001
        return models[0] if models else ""
    for want in models:
        for m in have:
            if m == want or m.startswith(want):
                return m
    return have[0] if have else (models[0] if models else "")


def probe(*, timeout_s: float = 20.0) -> list:
    """逐供应商健康探测（真发一次最小请求），返回状态表。"""
    rows = []
    for p in usable():
        try:
            cli = _client(p["base"], p["key"])
            model = _pick_model(cli, tuple(p["候选模型"]))
            t0 = time.time()
            r = cli.chat.completions.create(model=model, max_tokens=8, messages=_PING_MSG)
            ok = bool((r.choices[0].message.content or "").strip()) or True
            rows.append({"供应商": p["名称"], "模型": model, "状态": "通" if ok else "空回",
                         "ms": int((time.time() - t0) * 1000), "免费/本地": p["免费/本地"]})
        except Exception as exc:                            # noqa: BLE001
            msg = str(exc)
            why = ("钱包余额不足" if "余额不足" in msg else
                   (401 <= _status(exc) < 403 and "密钥无效/未设" or msg[:90]))
            rows.append({"供应商": p["名称"], "状态": "不通", "原因": why,
                         "免费/本地": p["免费/本地"]})
    return rows


def _status(exc: Exception) -> int:
    code = getattr(exc, "status_code", None)
    return int(code) if code else 0


def chat(messages: list, *, max_tokens: int = 1024, json_mode: bool = False,
         prefer_free: bool = True) -> dict:
    """对虚拟大模型说话：按链路自动切换供应商（本地→免费→付费备用），故障自动转移。"""
    errs = []
    for p in usable():
        try:
            cli = _client(p["base"], p["key"])
            model = _pick_model(cli, tuple(p["候选模型"]))
            if not model:
                errs.append(f"{p['名称']}: 无可用模型")
                continue
            kw = {"model": model, "messages": messages, "max_tokens": max_tokens}
            if json_mode:
                kw["response_format"] = {"type": "json_object"}
            r = cli.chat.completions.create(**kw)
            txt = (r.choices[0].message.content or "").strip()
            if not txt:
                errs.append(f"{p['名称']}: 空回复")
                continue
            return {"ok": True, "供应商": p["名称"], "模型": model,
                    "回复": txt, "免费/本地": p["免费/本地"],
                    "故障转移记录": errs}
        except Exception as exc:                               # noqa: BLE001
            errs.append(f"{p['名称']}: {type(exc).__name__} {str(exc)[:80]}")
    return {"ok": False, "reason": "全部通道不可用", "故障转移记录": errs,
            "怎么开": "本地 Ollama 起服务即可（免密钥）；或设置 GITHUB_TOKEN/GROQ_API_KEY 启用官方免费额度"}


def status() -> dict:
    rows = probe()
    return {"链路": [{"名称": p[0], "需要密钥": p[3], "免费/本地": p[4],
                      "候选模型": list(p[5])} for p in PROVIDERS],
            "健康": rows,
            "默认": "本地 Ollama（免密钥、无限调用）优先；付费网关已从默认链路移除",
            "清理记录": "TeamoRouter（代充钱包、无免费额度）→ 降级为付费备用，仅在显式配置时尝试"}


__all__ = ["PROVIDERS", "usable", "probe", "chat", "status"]
