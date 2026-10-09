# core/voice_control.py —— 语音指令闭环：一句话 → 执行 → 语音回复（本机免费通道兜底）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求：数字人能"通过语音对讲来执行任何操控和通用能力"，并且要**能真跑**，不是半成品。
# 这里补上缺的那一环（控制器）：
#   ① order(text) —— 一句话进 → core.action_loop.handle（术语 → 执行器 → 审计 → 通知）→ 出结果
#   ② speak(text) —— 优先 VoiceStudio(3900)；不通就走 **本机 SAPI**（免费/离线/无需凭据）
#   ③ status()    —— TTS/ASR 通道现状（谁在顶、缺什么），面板与数字人页共用
# 纪律：文本只走文件传给 SAPI（不拼命令）；执行动作仍受授权闸门与审计约束（沿用 action_loop）。
from core.swallow import swallow as _swallow
import os
import time

from core import deploy_ledger as J


def _sapi():
    try:
        from senses import voice_sapi as vs
        return vs
    except Exception:                                     # noqa: BLE001
        return None


def tts_channel() -> dict:
    """当前可用的回声通道：先问 VoiceStudio，再退到本机 SAPI。"""
    studio = {"ok": False, "reason": ""}
    try:
        import socket
        s = socket.socket()
        s.settimeout(1.2)
        studio["ok"] = s.connect_ex(("127.0.0.1", 3900)) == 0
        if not studio["ok"]:
            studio["reason"] = "VoiceStudio(3900) 未运行"
        s.close()
    except OSError as exc:                                # noqa: BLE001
        studio["reason"] = type(exc).__name__
    vs = _sapi()
    sapi = vs.available() if vs else {"ok": False, "reason": "模块缺失"}
    tw = {"ok": False, "reason": "模块缺失"}
    try:
        from senses import voice_tw as _vt
        tw = _vt.available()
    except Exception as exc:                                    # noqa: BLE001
        tw = {"ok": False, "reason": type(exc).__name__}
    # 台湾女声优先：主人反复要求"固定女声·台湾腔"，而本机 SAPI 没有 zh-TW 语音库，
    # 只用韵律是近似；神经语音的 zh-TW 女声才是正解。断网时它自己会失败 → 落到 SAPI。
    # ★主人 2026-10-08：「她的声音默认优先台湾腔女声」—— 这条**钉死在选道逻辑里**：
    #   台湾神经女声(zh-TW) 在 → 一定用它；不在才退 VoiceStudio/SAPI，并**如实标注退到了哪一档**。
    prefer = os.environ.get("V9_VOICE_PREFER", "tw").strip().lower()
    order = (["edge-tw", "voicestudio", "sapi"] if prefer in ("", "tw", "taiwan")
             else ["voicestudio", "sapi", "edge-tw"])
    avail = {"edge-tw": bool(tw.get("ok")), "voicestudio": bool(studio["ok"]),
             "sapi": bool(sapi.get("ok"))}
    pick = next((k for k in order if avail.get(k)), "")
    return {"选中": pick, "台湾女声(edge-tw)": tw, "VoiceStudio": studio, "本机SAPI": sapi,
            "优先序": order, "台湾腔优先": pick == "edge-tw",
            "说明": "默认台湾女声优先（edge-tw zh-TW）；不通才退 VoiceStudio；再不通用 SAPI 兜底。"
                    "可用 V9_VOICE_PREFER 改序（默认 tw）"}


def asr_channel() -> dict:
    """听写通道：**先问本机 SAPI 离线识别器**（免凭据、离线、一直都在），再看 VoiceStudio(3900)。

    真机病因（2026-10-08 实测）：原先只探 3900，而 3900 从未启动、且它根本不提供
    /audio/transcriptions ⇒ 报告层一律判「能说不能听」，把本机现成的识别器（MS-2052-80-DESK zh-CN）
    整个挡在视野之外。本机 SAPI 实测能把合成音频听回文本，闭环成立。
    """
    local = {"ok": False, "reason": "模块缺失"}
    try:
        from senses import voice_sapi as _vs
        local = _vs.asr_status()
    except Exception as exc:                                    # noqa: BLE001
        local = {"ok": False, "reason": type(exc).__name__}
    try:
        import socket
        s = socket.socket()
        s.settimeout(1.2)
        up = s.connect_ex(("127.0.0.1", 3900)) == 0
        s.close()
    except OSError:
        up = False
    # senses/voice_sapi.asr_status() 回的是中文键「可用」(:440)，不是 ok —— 两个都认，
    # 否则就是本补丁自己在犯「只看一个键就判死」的毛病。
    local_ok = bool(local.get("ok") or local.get("可用"))
    pick = ("sapi-local" if local_ok else ("voicestudio" if up else ""))
    return {"ok": bool(pick), "选中": pick,
            "通道": ("本机 SAPI 离线识别（免凭据）" if pick == "sapi-local"
                     else "VoiceStudio(3900) /audio/transcriptions"),
            "本机SAPI": local,
            "VoiceStudio": {"ok": up, "说明": "3900 只实现 /v1/audio/speech，**不提供** /audio/transcriptions"},
            "reason": "" if pick else ("本机识别器不可用（" + str(local.get("reason") or local.get("说明") or "?")
                                          + "），3900 也没起")}


def speak(text: str, *, priority: str = "normal", taiwan: bool = True,
          style: str | None = None) -> dict:
    """回声：优先 OmniVoice（可克隆台湾腔），不通则本机 SAPI（台湾腔韵律）。

    OmniVoice 出来的音频会**真播放**（.NET SoundPlayer；音频路径走环境变量，不拼命令）。
    style 是韵律样式名（人格档位用「撒娇讨好」「泼辣发火」；不给就走台湾腔默认）。
    """
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "reason": "空文本"}
    ch = tts_channel()
    if ch["选中"] == "edge-tw":
        try:
            from senses import voice_tw as _vt
            got = _vt.speak(t, taiwan=taiwan, style=style)
            if got.get("ok"):
                return {"ok": True, "通道": "edge-tw(台湾女声)", "结果": got}
            # 断网/失败：如实记原因，继续往下回落，不装成功
            last_reason = got.get("reason", "edge-tw 失败")
        except Exception as exc:                                # noqa: BLE001
            last_reason = type(exc).__name__
    else:
        last_reason = ""
    if ch["选中"] == "voicestudio":
        got = _omnivoice_say(t, taiwan=taiwan)
        if got.get("ok"):
            return {"ok": True, "通道": "omnivoice", "结果": got}
    vs = _sapi()
    if vs and vs.available()["ok"]:
        r = vs.speak(t, taiwan=taiwan, style=style)
        return {"ok": bool(r.get("ok")), "通道": "sapi",
                "回落原因": last_reason, "结果": r}
    return {"ok": False, "通道": "",
            "reason": f"没有可用的语音通道（台湾女声不可用：{last_reason or '未知'}；SAPI 也不可用）"}


def _omnivoice_say(text: str, *, taiwan: bool = True) -> dict:
    """走 OmniVoice（3900）合成 → 真播放。

    台湾腔两层：① 有参考音（state/voice_ref.txt 或 V9_TTS_REF_AUDIO）→ 服务端克隆该腔调；
    ② 没参考音 → 先把文案做台湾化（用词 + 句末软收），再交给模型读（如实标注）。
    """
    import pathlib
    import subprocess as _sp
    said, mode = text, "普通话"
    if taiwan:
        try:
            from body.prosody import STYLES, render
            from senses.voice_sapi import taiwanize
            said = render(taiwanize(text), STYLES["文静台湾腔"], ssml=False).text
            mode = "台湾用词+软收尾（未见参考音，未启用克隆）"
        except Exception as e:
            _swallow(__file__, e)
    t0 = time.time()
    try:
        import httpx
        r = httpx.post("http://127.0.0.1:3900/v1/audio/speech",
                       json={"input": said, "speed": 0.95}, timeout=900.0)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}"}
    if r.status_code != 200:
        return {"ok": False, "reason": f"合成被拒 HTTP {r.status_code}"}
    out = pathlib.Path(__file__).resolve().parent.parent.joinpath(
        "state", "voice_out", "say_latest.wav")
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(r.content)
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"写音频失败 {type(exc).__name__}"}
    env = dict(os.environ)
    env["V9_PLAY_FILE"] = str(out)
    ps = ('$ErrorActionPreference="Stop"; '
          '(New-Object Media.SoundPlayer $env:V9_PLAY_FILE).PlaySync(); Write-Output PLAYED_OK')
    try:
        r2 = _sp.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                     timeout=600, env=env,
                     creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0))
        played = "PLAYED_OK" in (r2.stdout or b"").decode("utf-8", "replace")
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": f"播放失败 {type(exc).__name__}"}
    return {"ok": bool(played), "文件": str(out), "字节": len(r.content),
            "合成ms": r.headers.get("X-Synth-Ms"), "读法": mode, "说出的文本": said[:120],
            "接口等待s": round(time.time() - t0, 1),
            "reason": "" if played else "播放器未确认"}


def order(text: str, *, target: str | None = None, speak_reply: bool = True,
          ledger=None, director=None, **kw) -> dict:
    """一句话 → 执行 → （可选）语音回复。走的是 core.action_loop 的唯一动手入口。"""
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "reason": "空指令"}
    from core import action_loop as AL
    try:
        res = AL.handle(t, target=target, ledger=ledger, director=director, **kw)
    except Exception as exc:                              # noqa: BLE001
        res = {"ok": False, "reason": f"{type(exc).__name__}: {str(exc)[:160]}"}
    # 回复文本：优先 action_loop 给的话术，其次按结果拼
    say = (res or {}).get("say") or (res or {}).get("sentence") or ""
    if not say:
        say = ("完成：" if (res or {}).get("ok") else "没做成：") + str(
            (res or {}).get("reason") or (res or {}).get("intent") or "")[:120]
    spoken = {"ok": False, "reason": "未要求语音回复"}
    if speak_reply:
        spoken = speak(say, priority="normal")
    out = {"ok": bool((res or {}).get("ok")), "指令": t, "执行": res,
           "回复": say, "语音": spoken,
           "通道": tts_channel()["选中"], "at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                           time.gmtime())}
    try:
        J.record("deploy", "voice_order", detail={"指令": t[:60], "ok": out["ok"],
                                                 "通道": out["通道"],
                                                 "语音ok": spoken.get("ok"),
                                                 "回复": say[:80]}, ok=out["ok"],
                 reason=("" if out["ok"] else str((res or {}).get("reason"))[:120]))
    except Exception as e:
        _swallow(__file__, e)
    return out


def status() -> dict:
    """给面板/数字人页：TTS 与 ASR 通道现状 + 是否可语音对讲。"""
    tts, asr = tts_channel(), asr_channel()
    tw = {"可用": False}
    try:
        from senses import voice_tw as _vt
        tw = _vt.status()
    except Exception as e:
        _swallow(__file__, e)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "TTS": tts, "ASR": asr, "台湾女声": tw,
            "可语音回复": bool(tts["选中"]),
            "可语音听写": bool(asr["ok"]),
            "对讲可用度": ("双向" if (tts["选中"] and asr["ok"]) else
                          ("单向（能说不能听）" if tts["选中"] else "不可用")),
            "下一步": ("" if (tts["选中"] and asr["ok"]) else
                       "听写需 VoiceStudio(3900)：设 VOICE_BASE_URL 指向可用的 whisper 服务；"
                       "说话通道已有（" + (tts["选中"] or "无") + "）")}


__all__ = ["order", "speak", "status", "tts_channel", "asr_channel"]
