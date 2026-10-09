# senses/voice_tw.py —— 台湾腔女声正解通道（微软神经语音 zh-TW，女声）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（反复强调过）：数字人必须用**固定女声 · 台湾腔**说话。
# 本机没有 zh-TW 语音库（SAPI 只有 zh-CN 女声）→ 只用"台湾腔韵律"是近似，不算落实。
# 这里接微软 Edge 神经语音里的**台湾女声**做正解：
#   · zh-TW-HsiaoChenNeural（晓臻，台湾女声，默认）
#   · zh-TW-HsiaoYuNeural / zh-TW-YunJheNeural（备选，仍限女性）
# 纪律：
#   · 先过 body.prosody 的台湾腔韵律（用词/句末软收），再合成 → 腔调与用词一起到位；
#   · 需要联网（微软语音服务）。**断网不许装作成功**：如实返回原因，由上层回落本机 SAPI，
#     并在状态里标明"当前真在用哪条通道"；
#   · 合成产物落 state/voice_tw/，文件名用内容哈希（同句复用，不重复下载）。
from core.swallow import swallow as _swallow
import asyncio
import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "state" / "voice_tw"
VOICE_DEFAULT = os.environ.get("V9_TW_VOICE", "zh-TW-HsiaoChenNeural")
VOICE_FALLBACKS = ("zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural")
RATE = os.environ.get("V9_TW_RATE", "+4%")
PITCH = os.environ.get("V9_TW_PITCH", "+2Hz")


def _mod():
    try:
        import edge_tts                                         # noqa: F401
        return True
    except Exception:                                           # noqa: BLE001
        return False


def _player() -> str:
    for name in ("ffplay", "ffplay.exe"):
        p = shutil.which(name)
        if p:
            return p
    return ""


def available() -> dict:
    """能不能用这条通道（模块 + 播放器；联网情况由合成时的真实结果决定）。"""
    m, pl = _mod(), _player()
    return {"ok": bool(m and pl), "模块": m, "播放器": pl or "（缺 ffplay）",
            "语音": VOICE_DEFAULT, "备选": list(VOICE_FALLBACKS),
            "rate": RATE, "pitch": PITCH,
            "说明": "微软神经语音 · 台湾女声（需联网）；断网会自动回落本机 SAPI"}


def _path_for(text: str, voice: str) -> Path:
    h = hashlib.sha256(f"{voice}|{RATE}|{PITCH}|{text}".encode("utf-8")).hexdigest()[:16]
    return OUT / f"tw_{h}.mp3"


def synthesize(text: str, *, voice: str | None = None) -> dict:
    """合成到文件（同句命中缓存）。返回 {ok, path, voice, cached}。"""
    raw = str(text or "").strip()
    if not raw:
        return {"ok": False, "reason": "空文本"}
    if not _mod():
        return {"ok": False, "reason": "edge_tts 未安装"}
    voice_use = voice or VOICE_DEFAULT
    p = _path_for(raw, voice_use)
    if p.is_file() and p.stat().st_size > 2000:
        return {"ok": True, "path": str(p), "voice": voice_use, "cached": True}

    async def _run(v: str):
        import edge_tts
        c = edge_tts.Communicate(raw[:800], v, rate=RATE, pitch=PITCH)
        await c.save(str(p))

    OUT.mkdir(parents=True, exist_ok=True)
    last = ""
    for v in (voice_use, *VOICE_FALLBACKS):
        try:
            asyncio.run(_run(v))
            if p.is_file() and p.stat().st_size > 2000:
                return {"ok": True, "path": str(p), "voice": v, "cached": False}
            last = "产物过小"
        except Exception as exc:                                # noqa: BLE001
            last = f"{type(exc).__name__}: {str(exc)[:80]}"
    return {"ok": False, "reason": f"合成失败（可能断网）：{last}", "voice": voice_use}


def play(path: str, *, timeout: float = 60.0) -> dict:
    """播放 mp3（ffplay 无窗口、播完自退；参数列表，无 shell）。"""
    pl = _player()
    if not pl:
        return {"ok": False, "reason": "缺 ffplay（装 ffmpeg 即可）"}
    try:
        r = subprocess.run([pl, "-nodisp", "-autoexit", "-loglevel", "error", str(path)],
                           capture_output=True, timeout=timeout, shell=False,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"ok": r.returncode == 0, "rc": r.returncode}
    except Exception as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def speak(text: str, *, taiwan: bool = True, style: str | None = None,
          play_audio: bool = True, voice: str | None = None) -> dict:
    """台湾女声朗读：台湾腔韵律 → 神经语音合成 → 播放。断网则如实失败（上层回落 SAPI）。"""
    from core import deploy_ledger as _J
    raw = str(text or "").strip()
    if not raw:
        return {"ok": False, "reason": "空文本，不朗读"}
    spoken = raw
    if taiwan:
        try:
            from body.prosody import STYLES, render
            pr = render(raw, STYLES.get(style or "文静台湾腔", STYLES["平和"]),
                        ssml=False, intensity=0.7)
            spoken = pr.text
        except Exception as e:
            _swallow(__file__, e)
        try:
            from senses.voice_sapi import taiwanize
            spoken = taiwanize(spoken)
        except Exception as e:
            _swallow(__file__, e)
    t0 = time.time()
    r = synthesize(spoken, voice=voice)
    if not r.get("ok"):
        return {"ok": False, "channel": "edge-tw", "reason": r.get("reason", "合成失败")}
    pr = play(r["path"]) if play_audio else {"ok": True}
    out = {"ok": bool(pr.get("ok")), "channel": "edge-tw", "voice": r.get("voice"),
           "path": r["path"], "cached": r.get("cached"),
           "ms": int((time.time() - t0) * 1000), "文本": spoken[:80]}
    try:
        _J.record("deploy", "voice_tw:speak",
                  detail={"channel": "edge-tw", "voice": r.get("voice"),
                          "ms": out["ms"], "ok": out["ok"]})
    except Exception as e:
        _swallow(__file__, e)
    return out


def status() -> dict:
    av = available()
    return {"通道": "edge-tw（微软神经语音）", "可用": av["ok"],
            "锁定语音": VOICE_DEFAULT, "备选": av["备选"], "韵律": {"rate": RATE, "pitch": PITCH},
            "台湾腔落地": "zh-TW 女声 + 台湾腔韵律（真台湾发音）" if av["ok"]
                          else "通道不可用（缺 edge_tts 或 ffplay）",
            "说明": av["说明"], "产物目录": str(OUT)}


__all__ = ["VOICE_DEFAULT", "VOICE_FALLBACKS", "available", "synthesize", "play",
           "speak", "status"]


if __name__ == "__main__":                                      # pragma: no cover
    import json
    print(json.dumps({"status": status(), "试听": speak("哈囉，我是小土豆，現在用台灣女聲跟你說話喔。",
                                                       play_audio=False)},
                     ensure_ascii=False, indent=1))
