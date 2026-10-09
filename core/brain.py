# core/brain.py —— 主脑：唯一 LLM 出口 + god_view + 语音回执
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from core.swallow import swallow as _swallow
import os, json, time
from openai import OpenAI

BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8317/v1")
API_KEY  = os.environ.get("OPENAI_API_KEY", "tk-brain-000")
FALLBACK_MODELS = os.environ.get("BRAIN_MODELS", "gpt-4o-mini,gpt-4o").split(",")

SYS_VERDICT = ("你是主脑。触手报告了发现或卡点。只回答JSON:"
               '{"verdict":"...","cmd":"continue|skip|fix|abort","hint":"下一步指令"}')

class Brain:
    def __init__(self, memory=None):
        self.mem = memory
        self.voice = None                          # 外部注入 VoiceAdapter
        self.client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
        self.calls = 0

    # ── 底层：重试 + 多模型降级，永不抛 ──
    def chat(self, messages, model=None, retries=3, json_mode=True):
        # ① 统一模型路由优先：本地 Ollama（免密钥、无限调用）→ 官方免费额度 → 付费网关备用。
        #    主人要求清理代付费通道：默认链路不再依赖 TeamoRouter 钱包。
        try:
            from core import providers as PR
            got = PR.chat(messages, json_mode=json_mode)
            if got.get("ok"):
                self.calls += 1
                self.last_provider = got.get("供应商")
                txt = got["回复"]
                if not json_mode:
                    return txt
                try:
                    return json.loads(txt)
                except json.JSONDecodeError:
                    return {"verdict": "ok", "cmd": "continue", "hint": txt[:400]}
            last = " / ".join(got.get("故障转移记录") or [got.get("reason", "")])
        except Exception as e:                                 # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
        # ② 旧网关链路保留为最后备选（显式指定 model 时仍走这里）
        chain = ([model] if model else []) + [m for m in FALLBACK_MODELS if m]
        for m in chain:
            for attempt in range(retries):
                try:
                    kw = {"model": m, "messages": messages}
                    if json_mode:
                        kw["response_format"] = {"type": "json_object"}
                    r = self.client.chat.completions.create(**kw)
                    self.calls += 1
                    txt = r.choices[0].message.content or "{}"
                    if not json_mode:
                        return txt
                    try:
                        return json.loads(txt)
                    except json.JSONDecodeError:
                        last = "模型未返回合法JSON"
                        continue
                except Exception as e:
                    last = f"{type(e).__name__}: {e}"
                    time.sleep(2 ** attempt)       # 指数退避
        # 全链路失败 → 降级返回，不中断调用方
        return {"verdict": "llm_unavailable", "cmd": "continue",
                "hint": f"降级继续推进: {last}"}

    # ── 触手问路：带该触手记忆上下文 ──
    def ask(self, tentacle_id, target, detail):
        ctx = self.mem.recall(tentacle_id) if self.mem else {}
        r = self.chat([
            {"role": "system", "content": SYS_VERDICT},
            {"role": "user", "content":
             f"触手={tentacle_id} 目标={target} 情况={detail} 记忆={ctx}"}])
        # 需人工关注的动作才语音回执，不朗读全文
        if self.voice and r.get("cmd") in ("abort", "fix"):
            try: self.voice.say_verdict(r, target)
            except Exception as e:
                _swallow(__file__, e)
        return r

    # ── 上帝视角：查看所有触手记忆（仅主脑可调）──
    def god_view(self):
        return self.mem.inspect_all() if self.mem else {}

    # ── 供面板查询 ──
    def status(self):
        return {"base_url": BASE_URL, "calls": self.calls,
                "models": FALLBACK_MODELS}
