# senses/voice_server.py —— 本机 TTS 服务（OpenAI 兼容 /v1/audio/speech，OmniVoice 驱动）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么需要它：V9 的语音层（senses/voice.py）指的是 **VoiceStudio/OmniVoice 的 OpenAI 兼容服务**
#   （VOICE_BASE_URL 默认 http://127.0.0.1:3900/v1）。真机上这台机器里：
#     · OmniVoice 本体已装（pip，k2-fsa/OmniVoice，权重 3.1GB 已缓存）；
#     · 但**服务端不在包里** → 3900 一直不通，语音链就断在这里。
#   所以这里补上服务端：一次加载模型，之后 `POST /v1/audio/speech` 出音频（OpenAI 形状）。
#
# 台湾腔：OmniVoice 是零样本克隆 TTS ——
#   · 有台湾参考音（V9_TTS_REF_AUDIO）→ 克隆出台湾腔；
#   · 没有参考音 → 用语言 zh + 风格提示（V9_TTS_INSTRUCT，例如"台湾腔，温柔一点"）。
#   文案侧的台湾用词/句末软收由 body.prosody + senses.voice_sapi.taiwanize 负责。
#
# 纪律：只监听回环；不引外部服务；模型缺失/权重未下载要如实报错（不假装能出声）。
from core.swallow import swallow as _swallow
import asyncio
import os
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = ROOT.joinpath("state", "voice_out")
MODEL_ID = os.environ.get("V9_TTS_MODEL", "k2-fsa/OmniVoice")
HOST = os.environ.get("V9_TTS_HOST", "127.0.0.1")
PORT = int(os.environ.get("V9_TTS_PORT", "3900"))
# 台湾腔怎么落地（真机结论）：
#   · OmniVoice 的 instruct 是**枚举**（合法中文项：东北话/四川话/河南话… 女/男/青年/中年/
#     高音调/低音调/耳语 等），**内置方言不含台湾** —— 写自由文本（如"台湾腔"）会直接 503。
#   · 所以台湾腔的正解是**零样本克隆**：给一段台湾国语参考音（V9_TTS_REF_AUDIO），
#     OmniVoice 会用那个腔调说任何话；参考音未设时输出是普通话，如实标注。
#   · 无参考音时，文案侧仍套台湾用词 + 句末软收（body.prosody 的「文静台湾腔」）。
LANGUAGE = os.environ.get("V9_TTS_LANGUAGE", "zh")
INSTRUCT = os.environ.get("V9_TTS_INSTRUCT", "女，青年，中音调")
REF_AUDIO_ENV = os.environ.get("V9_TTS_REF_AUDIO", "")   # 台湾参考音（env 兜底）
REF_POINTER = ROOT.joinpath("state", "voice_ref.txt")     # 运行期指定的参考音路径
NUM_STEP = int(os.environ.get("V9_TTS_STEPS", "16"))   # 步数越少越快（CPU 上很关键）
OUT_DIR.mkdir(parents=True, exist_ok=True)

_STATE = {"model": None, "loaded_at": 0.0, "load_ms": None, "syntheses": 0, "errors": 0,
          "last_error": "", "last_ms": None, "device": os.environ.get("V9_TTS_DEVICE", "cpu")}


def current_ref() -> str:
    """当前克隆参考音：优先 state/voice_ref.txt（可运行期更换），其次环境变量。"""
    try:
        if REF_POINTER.is_file():
            v = REF_POINTER.read_text(encoding="utf-8", errors="replace").strip()
            if v and Path(v).is_file():
                return v
    except OSError as e:
        _swallow(__file__, e)
    return REF_AUDIO_ENV if REF_AUDIO_ENV and Path(REF_AUDIO_ENV).is_file() else ""


def _load_model():
    """惰性加载 OmniVoice（一次）；失败如实返回原因。

    照它的 CLI 口径：`OmniVoice.from_pretrained(model, device_map=..., dtype=torch.float16)`
    （dtype 传 float16 会在 CPU 上报错时自动退回默认精度）。
    """
    if _STATE["model"] is not None:
        return {"ok": True, "cached": True}
    t0 = time.time()
    try:
        import torch
        from omnivoice import OmniVoice
        try:
            _STATE["model"] = OmniVoice.from_pretrained(
                MODEL_ID, device_map=_STATE["device"], dtype=torch.float16)
        except Exception:                                     # noqa: BLE001
            _STATE["model"] = OmniVoice.from_pretrained(MODEL_ID, device_map=_STATE["device"])
        _STATE["load_ms"] = int((time.time() - t0) * 1000)
        _STATE["loaded_at"] = time.time()
        return {"ok": True, "cached": False, "load_ms": _STATE["load_ms"]}
    except Exception as exc:                                  # noqa: BLE001
        _STATE["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return {"ok": False, "reason": _STATE["last_error"]}


def synthesize(text: str, *, voice: str | None = None, speed: float | None = None,
               language: str | None = None, instruct: str | None = None) -> dict:
    """合成一段语音到 state/voice_out/tts_<序号>.wav，返回路径与耗时。

    真机口径：OmniVoice 的入口是 `model.generate(text=..., language=..., instruct=...,
    num_step=..., speed=...)` → 返回波形列表，采样率取 `model.sampling_rate`。
    """
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "reason": "空文本"}
    got = _load_model()
    if not got.get("ok"):
        _STATE["errors"] += 1
        return {"ok": False, "reason": f"模型加载失败：{got.get('reason')}"}
    out = OUT_DIR.joinpath(f"tts_{_STATE['syntheses'] + 1:04d}.wav")
    t0 = time.time()
    try:
        import soundfile as sf
        kw = {"text": t[:400], "language": language or LANGUAGE, "num_step": NUM_STEP}
        ins = instruct or INSTRUCT
        if ins:
            kw["instruct"] = ins
        ref = current_ref()
        if ref:
            kw["ref_audio"] = ref
        if speed:
            kw["speed"] = float(speed)
        try:
            audios = _STATE["model"].generate(**kw)
        except Exception as exc:                              # noqa: BLE001
            msg = str(exc)
            if "instruct" in kw and ("Unsupported" in msg or "Cannot mix" in msg):
                # 非法/冲突的 instruct 不能让它整条失败 → 摘掉重来一次（如实记账）
                kw.pop("instruct", None)
                _STATE["last_instruct_dropped"] = ins
                audios = _STATE["model"].generate(**kw)
            else:
                raise
        sf.write(str(out), audios[0], _STATE["model"].sampling_rate)
    except Exception as exc:                                  # noqa: BLE001
        _STATE["errors"] += 1
        _STATE["last_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return {"ok": False, "reason": _STATE["last_error"]}
    _STATE["syntheses"] += 1
    _STATE["last_ms"] = int((time.time() - t0) * 1000)
    try:
        size = out.stat().st_size
    except OSError:
        size = None
    return {"ok": True, "file": str(out), "bytes": size, "ms": _STATE["last_ms"],
            "language": language or LANGUAGE, "instruct": instruct or INSTRUCT,
            # ★2026-10-08：原写 REF_AUDIO —— 那是**未定义名**（全仓只有 REF_AUDIO_ENV），
            #   于是合成成功后抛 NameError → /v1/audio/speech 返 500（"起了也不行"的真身）
            "克隆参考": bool(current_ref()), "device": _STATE["device"]}


def status() -> dict:
    tw = bool(current_ref())
    return {"model": MODEL_ID, "endpoint": f"http://{HOST}:{PORT}/v1",
            "device": _STATE["device"], "num_step": NUM_STEP,
            "语言": LANGUAGE, "风格提示": INSTRUCT, "克隆参考": current_ref() or "（未设）",
            "台湾腔": ("已启用（克隆参考音）" if tw else
                       "未启用：OmniVoice 内置中文方言不含台湾 → 需设 V9_TTS_REF_AUDIO "
                       "指向一段台湾国语参考音（5~20 秒）才能克隆出台湾腔"),
            "参考音接入": "把台湾国语片段放到 state/voice_ref/tw.wav，或调 "
                          "POST /api/voice/ref 指定路径 → 下一次合成即用该腔调",
            "已加载": _STATE["model"] is not None, "加载耗时ms": _STATE["load_ms"],
            "合成次数": _STATE["syntheses"], "错误次数": _STATE["errors"],
            "最近错误": _STATE["last_error"], "最近耗时ms": _STATE["last_ms"],
            "被丢弃的非法 instruct": _STATE.get("last_instruct_dropped", ""),
            "合法中文方言": "东北话/云南话/四川话/宁夏话/桂林话/河南话/济南话/甘肃话/"
                            "石家庄话/贵州话/陕西话/青岛话（含 女/男/青年/中年/老年/儿童/"
                            "少年/耳语/高中低音调）——**不含台湾**",
            "说明": "OpenAI 兼容：POST /v1/audio/speech（V9 的 VOICE_BASE_URL 默认就指这里）"}


def build_app():
    """FastAPI 应用（OpenAI 兼容最小面：/v1/audio/speech、/v1/voices、/health）。"""
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, JSONResponse
    from pydantic import BaseModel

    app = FastAPI(title="V9 本机 TTS（OmniVoice）")

    class SpeechBody(BaseModel):
        model: str | None = None
        input: str = ""
        voice: str | None = None
        response_format: str | None = None
        speed: float | None = None

    @app.get("/health")
    def health():
        return {"ok": _STATE["model"] is not None or True, **status()}

    @app.get("/v1/health")
    def v1_health():
        return health()

    @app.get("/v1/voices")
    def voices():
        return {"voices": [{"id": "omnivoice-default", "language": LANGUAGE,
                            "note": "零样本克隆；可用 V9_TTS_REF_AUDIO 指定台湾参考音"}]}

    @app.post("/v1/audio/speech")
    def speech(body: SpeechBody):
        r = synthesize(body.input, voice=body.voice, speed=body.speed)
        if not r.get("ok"):
            raise HTTPException(503, r.get("reason") or "synthesis_failed")
        return FileResponse(r["file"], media_type="audio/wav",
                            headers={"X-Synth-Ms": str(r.get("ms")),
                                     "X-V9-Channel": "omnivoice"})

    @app.get("/v1/status")
    def st():
        return JSONResponse(status())

    return app


def main(argv=None) -> int:                                   # pragma: no cover
    import uvicorn
    print(f"[v9-tts] OmniVoice 服务 → http://{HOST}:{PORT}/v1 （模型 {MODEL_ID}，设备 "
          f"{_STATE['device']}）")
    uvicorn.run(build_app(), host=HOST, port=PORT, log_level="info")
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    raise SystemExit(main())
