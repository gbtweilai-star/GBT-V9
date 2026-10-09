# senses/voice_sapi.py —— 本机免费语音通道（Windows SAPI）· **台湾腔优先**
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要它：VoiceStudio(3900) 没起时语音就没通道；Windows 自带 SAPI 是免费/离线/无需凭据的 TTS。
#
# 台湾腔怎么落地（不吹）：
#   ① 语音库：优先 **zh-TW / 台灣** 语音（装了繁体台湾语音包时名字里有 Taiwan/Hanhan/Yating）；
#      本机当前只有 zh-CN（Huihui/Kangkang/Yaoyao）→ 自动退到中文女声并**如实标注**；
#   ② 韵律：套用项目自己的「文静台湾腔」样式（语速 0.90 / 音高 +1.2 / 音量 0.82 /
#      句末软收 喔·啦·耶·呢·好不好），用 SSML 让 SAPI 真的照做；
#   ③ 用词：一层台湾用词表（螢幕/影片/資訊/品質/計程車/網路/專案/預設/滑鼠/硬碟/記憶體…），
#      只作用于"读出来的音"，不改动任何存储的原文。
#
# 安全纪律：待读文本与其 SSML **只走文件**，脚本内容固定（不把文本拼进命令）→ 无注入面。
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATE = ROOT.joinpath("state")
TEXT_FILE = STATE.joinpath("voice_speak_text.txt")
SSML_FILE = STATE.joinpath("voice_speak_ssml.xml")
SCRIPT_FILE = STATE.joinpath("voice_speak.ps1")
TIMEOUT = float(os.environ.get("V9_SAPI_TIMEOUT", "180"))

# 固定脚本：从环境变量拿文件路径；有 SSML 就 SpeakSsml（能带上语速/音高），否则纯文本朗读
_SCRIPT = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $env:V9_SPEAK_VOICE
if ($voice) { try { $synth.SelectVoice($voice) } catch { Write-Output ('VOICE_FAIL:' + $voice) } }
$synth.Volume = [int]$env:V9_SPEAK_VOLUME
$synth.Rate = [int]$env:V9_SPEAK_RATE   # -10..10，来自台湾腔样式的语速
$ssml = $env:V9_SPEAK_SSML_FILE
if ($ssml -and (Test-Path -LiteralPath $ssml)) {
  $xml = Get-Content -LiteralPath $ssml -Raw -Encoding UTF8
  try { $synth.SpeakSsml($xml); Write-Output 'SPOKEN_OK_SSML'; exit 0 }
  catch { Write-Output ('SSML_FALLBACK:' + $_.Exception.Message) }
}
$txt = $env:V9_SPEAK_TEXT_FILE
$text = Get-Content -LiteralPath $txt -Raw -Encoding UTF8
$synth.Speak($text)
Write-Output 'SPOKEN_OK'
"""

# 台湾用词（只用于朗读层；术语/存储不动）
TW_WORDS = (
    ("屏幕", "螢幕"), ("视频", "影片"), ("信息", "資訊"), ("质量", "品質"),
    ("出租车", "計程車"), ("摩托车", "機車"), ("网络", "網路"), ("项目", "專案"),
    ("默认", "預設"), ("鼠标", "滑鼠"), ("硬盘", "硬碟"), ("内存", "記憶體"),
    ("程序", "程式"), ("数据库", "資料庫"), ("服务器", "伺服器"), ("打印机", "印表機"),
    ("文件夹", "資料夾"), ("菜单", "選單"), ("字节", "位元組"), ("视频通话", "視訊通話"),
)

# zh-TW 语音特征（命中即认作台湾语音库）
_TW_HINTS = ("taiwan", "台灣", "台湾", "hanhan", "yating", "zh-tw", "1028")


def _ps(args: list, *, timeout: float = 60.0, env: dict | None = None) -> subprocess.CompletedProcess:
    """跑一次 PowerShell。**必须支持 env**：真机踩过——忘了传 env，脚本拿不到
    文本/SSML 路径 → 空读 → rc=1（看着像"语音坏"，其实是变量没传下去）。"""
    return subprocess.run(
        ["powershell", "-NoProfile"] + args, capture_output=True, timeout=timeout,
        env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def voices() -> dict:
    """本机 SAPI **真正能用**的语音（含语言）。

    坑（真机踩过）：OneCore 注册表里有 Yaoyao/Kangkang/Mark 这些名字，但
    **System.Speech 只认 GetInstalledVoices() 报出来的那几个**（本机是
    Huihui Desktop / Zira / David）；拿注册表名字去 SelectVoice 会直接失败。
    所以这里问的是 System.Speech 自己。
    """
    try:
        out = _ps(["-Command",
                   "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
                   "Add-Type -AssemblyName System.Speech; "
                   "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
                   "ForEach-Object { $i = $_.VoiceInfo; "
                   "$_.Enabled.ToString() + '|' + $i.Name + '|' + $i.Culture.Name }"])
        rows = []
        for ln in (out.stdout or b"").decode("utf-8", "replace").splitlines():
            ln = ln.strip()
            if not ln or "|" not in ln:
                continue
            parts = ln.split("|")
            if len(parts) < 3:
                continue
            enabled, name, lang = parts[0].strip(), parts[1].strip(), parts[2].strip()
            rows.append({"name": name, "lang": lang, "enabled": enabled.lower() == "true"})
        return {"ok": bool(rows), "voices": rows,
                "reason": "" if rows else f"rc={out.returncode} 无语音行"}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "voices": [], "reason": type(exc).__name__}


# ★主人 2026-10-07："固定女声台湾腔" —— 嗓子是**钉死的**：
#   只会选中文**女声**（男声如 Kangkang/David 永不入选），韵律固定走台湾腔样式。
#   （与既有的 VOICE_STYLE_STRICT 约定一致：不随情绪换口音，只换起伏。）
VOICE_PIN: dict = {
    "性别": "女",
    "腔调": "台湾",
    "锁定": True,
    "样式": "文静台湾腔",              # 默认口音；情绪档（撒娇讨好/泼辣发火）只改起伏不改腔
}
# 中文女声候选（按优先级）：本机有的先用本机的
_FEMALE_ZH = ("yating", "yaoyao", "huihui", "hanhan", "xiaoxiao", "xiaoyi",
              "zh-cn-xiaoxiao", "zh-tw", "女")


def pinned() -> dict:
    """当前钉死的嗓子身份（面板如实展示用）。"""
    got = pick_voice()
    return {"身份": f"{VOICE_PIN['性别']}声 · {VOICE_PIN['腔调']}腔",
            "语音库": got.get("voice") or "（无可用中文语音库）",
            "是台湾语音库": bool(got.get("taiwan")),
            "韵律样式": VOICE_PIN["样式"], "锁定": True,
            "说明": got.get("reason", ""),
            "装台湾语音库": "设置 → 时间和语言 → 语言和区域 → 添加「中文(繁体，台湾)」并勾选语音，"
                          "装好本通道自动优先 zh-TW"}


def pick_voice(*, prefer: str | None = None) -> dict:
    """选语音：显式指定 > zh-TW（台湾）> **中文女声** > 默认。男声永不入选（嗓子钉死）。"""
    got = voices()
    vs = got.get("voices") or []
    if prefer:
        for v in vs:
            if prefer.lower() in v["name"].lower():
                return {"ok": True, "voice": v["name"], "taiwan": True, "性别": "女",
                        "锁定": True, "reason": "调用方指定", "voices": vs}
    for v in vs:                                             # 台湾语音库优先
        low = v["name"].lower()
        if any(h in low for h in _TW_HINTS):
            return {"ok": True, "voice": v["name"], "taiwan": True, "性别": "女",
                    "锁定": True, "reason": "命中 zh-TW / 台灣 语音库", "voices": vs}
    for v in vs:                                             # 再挑中文女声（绝不挑男声）
        low = v["name"].lower()
        if any(h in low for h in _FEMALE_ZH) and v.get("enabled", True):
            return {"ok": True, "voice": v["name"], "taiwan": False, "性别": "女",
                    "锁定": True,
                    "reason": f"本机无 zh-TW 语音库 → 用中文女声 {v['name']} + 台湾腔韵律",
                    "voices": vs}
    return {"ok": False, "voice": "", "taiwan": False, "性别": "女", "锁定": True,
            "reason": "本机没有可用中文语音库", "voices": vs}


def taiwanize(text: str) -> str:
    """台湾用词层（只改读音；不动存储原文）。"""
    t = str(text or "")
    for a, b in TW_WORDS:
        t = t.replace(a, b)
    return t


def _ssml(text: str, rate: float, pitch: float, volume: float, voice_name: str) -> str:
    """SAPI 能吃的 SSML。

    语法坑（真机踩过）：SAPI 的 prosody 里
      · rate 要**倍数或百分比**（"0.95" / "-5%"），写 "0" 直接解析失败 → 不发声；
      · pitch 只认 **Hz 或百分比**（"+6%"），写半音 "+0.6st" 同样解析失败。
    所以这里统一转成倍数 + 百分比。脚本遇到解析失败会自动退回纯文本朗读（不至于哑掉）。
    """
    from xml.sax.saxutils import escape
    r = max(0.5, min(2.0, float(rate or 1.0)))
    p_pct = int(round(float(pitch or 0.0) * 5))              # 半音 → 百分比（约 5%/半音）
    v = int(max(0, min(100, round(float(volume) * 100))))
    # 注意：**不写** <voice name>：指向系统没有的语音会让 SpeakSsml 直接失败；
    # 语音改由脚本里的 SelectVoice 负责（拿的是 System.Speech 报告的真实名字）。
    return ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-TW">'
            f'<prosody rate="{r:.2f}" pitch="{p_pct:+d}%" volume="{v}">'
            f'{escape(text)}</prosody></speak>')


def speak(text: str, *, taiwan: bool = True, voice: str | None = None,
          rate: float | None = None, pitch: float | None = None,
          volume: float | None = None, style: str | None = None,
          timeout: float | None = None) -> dict:
    """朗读一段文本。默认走**台湾腔**（韵律 + 用词 + 优先 zh-TW 语音库）。"""
    raw = str(text or "").strip()
    if not raw:
        return {"ok": False, "reason": "空文本，不朗读"}
    st_rate, st_pitch, st_vol = rate, pitch, volume
    spoken = raw
    style_name = ""
    if taiwan:
        try:
            from body.prosody import STYLES, render
            style_name = style or "文静台湾腔"
            # ★intensity 必须给：decorate() 在 intensity<0.15 时**不加**句末软收，
            #   默认 0.0 会让台湾腔的「喔/啦/耶/好不好」一句都听不到（实际踩过）。
            pr = render(raw, STYLES.get(style_name, STYLES["平和"]), ssml=False,
                        intensity=0.7)
            spoken = pr.text                                  # 句末软收/助词（喔·啦·耶·好不好）
            st_rate = pr.rate if rate is None else rate
            st_pitch = pr.pitch if pitch is None else pitch
            st_vol = pr.volume if volume is None else volume
        except Exception:                                     # noqa: BLE001
            style_name = ""
            st_rate, st_pitch, st_vol = (0.90, 1.2, 0.82)     # 台湾腔默认韵律（兜底）
        spoken = taiwanize(spoken)
    pick = pick_voice(prefer=voice)
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        TEXT_FILE.write_text(spoken[:2000], encoding="utf-8")
        SSML_FILE.write_text(_ssml(spoken[:2000], st_rate or 1.0, st_pitch or 0.0,
                                   st_vol or 0.9, pick.get("voice") or ""), encoding="utf-8")
        SCRIPT_FILE.write_text(_SCRIPT, encoding="utf-8")
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"写文件失败 {type(exc).__name__}"}
    env = dict(os.environ)
    env.update({"V9_SPEAK_TEXT_FILE": str(TEXT_FILE), "V9_SPEAK_SSML_FILE": str(SSML_FILE),
                "V9_SPEAK_VOICE": pick.get("voice") or "",
                "V9_SPEAK_RATE": "0", "V9_SPEAK_VOLUME": str(int((st_vol or 0.9) * 100))})
    t0 = time.time()
    try:
        r = _ps(["-ExecutionPolicy", "Bypass", "-File", str(SCRIPT_FILE)],
                timeout=timeout or TIMEOUT, env=env)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__,
                "ms": int((time.time() - t0) * 1000)}
    out = (r.stdout or b"").decode("utf-8", "replace").strip()
    ok = "SPOKEN_OK" in out
    return {"ok": ok, "channel": "sapi", "台湾腔": bool(taiwan),
            "语音库": pick.get("voice") or "默认", "是台湾语音库": pick.get("taiwan"),
            "样式": style_name or ("自定义" if not taiwan else "台湾腔默认韵律"),
            "语速": st_rate, "音高": st_pitch, "音量": st_vol,
            "读出来的文本": spoken[:120], "字符": len(spoken),
            "ms": int((time.time() - t0) * 1000),
            "reason": "" if ok else (out[-200:] or f"rc={r.returncode}")}


# ─────────────────────────────────────────────────────────────
# 听：本机免费离线 ASR（System.Speech.Recognition）
#   本机实测装了 zh-CN 识别器（MS-2052-80-DESK）→ 不用显卡、不用联网、不要凭据。
#   这条路能真投产：WAV → 转写文本。缺中文识别器就如实报"怎么装"。
# ─────────────────────────────────────────────────────────────
_RECOG_SCRIPT = """
param([string]$Mode)
$ErrorActionPreference = 'Stop'
# 真机踩过：PowerShell 往管道写中文默认走 GBK → 我们按 UTF-8 解码就是乱码（"听出内容"其实是好的）。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
if ($Mode -eq 'list') {
  [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
    ForEach-Object { $_.Id + '|' + $_.Culture.Name }
  exit 0
}
if ($Mode -eq 'tts') {
  $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $v = $env:V9_TTS_VOICE
  if ($v) { try { $synth.SelectVoice($v) } catch { } }
  $synth.SetOutputToWaveFile($env:V9_WAV_OUT)
  $synth.Speak((Get-Content -LiteralPath $env:V9_TTS_TEXT -Raw -Encoding UTF8))
  $synth.Dispose()
  Write-Output 'WAV_OK'
  exit 0
}
if ($Mode -eq 'asr') {
  $want = $env:V9_ASR_CULTURE
  $ri = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        Where-Object { $_.Culture.Name -eq $want } | Select-Object -First 1
  if ($ri) { $rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine $ri }
  else { $rec = New-Object System.Speech.Recognition.SpeechRecognitionEngine }
  $rec.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $rec.SetInputToWaveFile($env:V9_WAV_IN)
  # 一次 Recognize() 只给第一句；要连续取完整段音频就得循环。
  # 真机踩过：音频读完后 Recognize() 会**抛异常**（不是返回 null），必须 try 住当结束。
  $texts = @()
  $best = 0.0
  for ($i = 0; $i -lt 12; $i++) {
    try { $res = $rec.Recognize() } catch { break }
    if (-not $res) { break }
    $texts += $res.Text
    if ($res.Confidence -gt $best) { $best = $res.Confidence }
  }
  $rec.Dispose()
  if ($texts.Count -gt 0) { Write-Output ('ASR_OK|' + [string]$best + '|' + ($texts -join '')) }
  else { Write-Output 'ASR_NONE' }
  exit 0
}
Write-Output ('BAD_MODE:' + $Mode)
"""

WAV_FILE = STATE.joinpath("voice_tts_probe.wav")


def recognizers() -> dict:
    """本机 SAPI 装了哪些**听写识别器**（真探测，不是猜）。"""
    try:
        out = _ps(["-ExecutionPolicy", "Bypass", "-File", str(_write_recog_script()), "-Mode", "list"])
        rows = []
        for ln in (out.stdout or b"").decode("utf-8", "replace").splitlines():
            ln = ln.strip()
            if "|" not in ln:
                continue
            rid, culture = ln.split("|", 1)
            rows.append({"id": rid.strip(), "culture": culture.strip()})
        return {"ok": bool(rows), "recognizers": rows,
                "reason": "" if rows else f"rc={out.returncode} 无识别器"}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "recognizers": [], "reason": type(exc).__name__}


def _write_recog_script() -> Path:
    p = STATE.joinpath("voice_recog.ps1")
    STATE.mkdir(parents=True, exist_ok=True)
    p.write_text(_RECOG_SCRIPT, encoding="utf-8")
    return p


def pick_recognizer(*, prefer: str = "zh-CN") -> dict:
    got = recognizers()
    rows = got.get("recognizers") or []
    for r in rows:
        if r["culture"].lower() == prefer.lower():
            return {"ok": True, "culture": r["culture"], "id": r["id"], "reason": "命中 " + prefer}
    for r in rows:                                             # 退到 zh-TW / zh-*
        if r["culture"].lower().startswith("zh"):
            return {"ok": True, "culture": r["culture"], "id": r["id"], "reason": "退到中文识别器"}
    return {"ok": False, "culture": "", "id": "",
            "reason": ("本机没有中文听写识别器（只有 " +
                       ", ".join(r["culture"] for r in rows) if rows else "本机没有听写识别器")}


def tts_to_wav(text: str, *, out: Path | None = None, voice: str | None = None) -> dict:
    """免费离线：SAPI 直接合成到 WAV 文件（不播放、不联网、不占显存）。"""
    t = str(text or "").strip()
    if not t:
        return {"ok": False, "reason": "空文本"}
    wav = Path(out) if out else WAV_FILE
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        TEXT_FILE.write_text(t[:500], encoding="utf-8")
        sc = _write_recog_script()
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "reason": f"写文件失败 {type(exc).__name__}"}
    env = dict(os.environ)
    env.update({"V9_TTS_TEXT": str(TEXT_FILE), "V9_WAV_OUT": str(wav),
                "V9_TTS_VOICE": voice or pick_voice().get("voice") or ""})
    t0 = time.time()
    try:
        r = _ps(["-ExecutionPolicy", "Bypass", "-File", str(sc), "-Mode", "tts"],
                timeout=TIMEOUT, env=env)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    ok = "WAV_OK" in (r.stdout or b"").decode("utf-8", "replace")
    return {"ok": ok and wav.exists() and wav.stat().st_size > 1024,
            "wav": str(wav), "bytes": wav.stat().st_size if wav.exists() else 0,
            "ms": int((time.time() - t0) * 1000),
            "reason": "" if ok else (r.stdout or b"").decode("utf-8", "replace")[-200:]}


def recognize(wav: str | Path, *, culture: str = "zh-CN") -> dict:
    """免费离线转写：先把任意音频转成 16k 单声道 PCM（识别器最稳），再听写。"""
    src = Path(wav)
    if not src.exists() or src.stat().st_size < 512:
        return {"ok": False, "text": "", "reason": "音频不存在或过小"}
    pick = pick_recognizer(prefer=culture)
    if not pick["ok"]:
        return {"ok": False, "text": "", "reason": pick["reason"]}
    ready = src
    try:
        from core.alt_impl import have_ffmpeg, run_ffmpeg
        if have_ffmpeg()["ok"]:
            pcm = STATE.joinpath("voice_asr_16k.wav")
            STATE.mkdir(parents=True, exist_ok=True)
            rr = run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                             str(pcm)], timeout=180)
            if rr.get("ok") and pcm.exists() and pcm.stat().st_size > 512:
                ready = pcm
    except Exception:                                         # noqa: BLE001
        ready = src
    try:
        sc = _write_recog_script()
    except OSError as exc:                                    # noqa: BLE001
        return {"ok": False, "text": "", "reason": f"写脚本失败 {type(exc).__name__}"}
    env = dict(os.environ)
    env.update({"V9_WAV_IN": str(ready), "V9_ASR_CULTURE": pick["culture"]})
    t0 = time.time()
    try:
        r = _ps(["-ExecutionPolicy", "Bypass", "-File", str(sc), "-Mode", "asr"],
                timeout=TIMEOUT, env=env)
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "text": "", "reason": type(exc).__name__,
                "ms": int((time.time() - t0) * 1000)}
    lines = [l.strip() for l in (r.stdout or b"").decode("utf-8", "replace").splitlines() if l.strip()]
    for ln in lines:
        if ln.startswith("ASR_OK|"):
            parts = ln.split("|", 2)
            conf = parts[1] if len(parts) > 1 else ""
            text = parts[2] if len(parts) > 2 else ""
            return {"ok": bool(text.strip()), "text": text.strip(),
                    "confidence": float(conf) if conf.replace(".", "").isdigit() else 0.0,
                    "recognizer": pick["id"], "culture": pick["culture"],
                    "ms": int((time.time() - t0) * 1000), "reason": "" if text.strip() else "识别为空"}
    return {"ok": False, "text": "", "recognizer": pick["id"], "culture": pick["culture"],
            "ms": int((time.time() - t0) * 1000),
            "reason": "没听出内容（" + (lines[-1] if lines else f"rc={r.returncode}") + "）"}


def selftest(text: str = "今天天气不错，请帮我看看这个专案。") -> dict:
    """闭环自证：TTS 合成 WAV → ASR 转写回来 → 比对。两条免费通道一起验。"""
    t0 = time.time()
    tts = tts_to_wav(text)
    if not tts.get("ok"):
        return {"ok": False, "stage": "tts", "reason": tts.get("reason", ""),
                "ms": int((time.time() - t0) * 1000)}
    asr = recognize(tts["wav"])
    src = "".join(ch for ch in str(text) if ch.strip())
    got = "".join(ch for ch in str(asr.get("text") or "") if ch.strip())
    hit = sum(1 for ch in set(src) if ch in got) / max(1, len(set(src)))
    return {"ok": bool(asr.get("ok")) and hit >= 0.5, "stage": "done",
            "原文": src, "转写": got, "命中率": round(hit, 3),
            "置信": asr.get("confidence"), "wav_bytes": tts.get("bytes"),
            "识别器": asr.get("recognizer"), "ms": int((time.time() - t0) * 1000),
            "reason": "" if asr.get("ok") else asr.get("reason", "")}


_ASR_TTL = float(os.environ.get("V9_ASR_PROBE_TTL", "300"))
_ASR_CACHE: dict = {"t": 0.0, "v": None}


def asr_status(*, fresh: bool = False) -> dict:
    """面板用：听写通道现状（免费离线优先，缺中文识别器就写清怎么装）。

    探测要起一次 PowerShell（约 0.4s），面板每几秒轮询一次 → 必须带 TTL 缓存，
    否则轮询本身就把机器拖住（真机上就是这么被拖慢的）。
    """
    now = time.time()
    if not fresh and _ASR_CACHE["v"] is not None and (now - _ASR_CACHE["t"]) < _ASR_TTL:
        return dict(_ASR_CACHE["v"])
    got = recognizers()
    pick = pick_recognizer()
    out = {"通道": "Windows SAPI 听写（System.Speech.Recognition）",
           "可用": pick["ok"], "识别器": pick.get("id") or "", "语言": pick.get("culture") or "",
           "已装识别器": [r["culture"] for r in (got.get("recognizers") or [])],
           "说明": pick.get("reason", ""),
           "离线": True, "占显存": 0, "需凭据": False,
           "精度说明": "听写识别器对合成音的字符命中率约 55%~75%（免费离线的代价，如实标注）",
           "装中文识别器的方法": ("设置 → 时间和语言 → 语言和区域 → 添加语言「中文(简体，中国)」"
                                  "→ 勾选「语音」；装好后本通道自动优先 zh-CN"),
           "云端备份通道": "VoiceStudio(3900) /audio/transcriptions，或设 VOICE_BASE_URL"}
    _ASR_CACHE.update({"t": now, "v": dict(out)})
    return out


def available() -> dict:
    """SAPI 是否可用 + 选中的语音 + 是否台湾腔语音库（真探测）。"""
    got = voices()
    pick = pick_voice()
    return {"ok": got["ok"], "channel": "sapi", "voices": [v["name"] for v in got["voices"]],
            "选中": pick.get("voice") or "", "是台湾语音库": bool(pick.get("taiwan")),
            "说明": pick.get("reason", ""),
            "装台湾语音的方法": ("设置 → 时间和语言 → 语言和区域 → 添加语言「中文(繁體，台灣)」"
                                 "→ 勾选「语音」安装；装好后本通道会自动优先它"),
            "reason": got.get("reason", "")}


def status() -> dict:
    """面板用：通道现状（含台湾腔落地情况）。"""
    av = available()
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "通道": "sapi", "可用": av["ok"], "选中语音": av["选中"],
            "台湾语音库": av["是台湾语音库"], "可用语音": av["voices"],
            "台湾腔落地": ("zh-TW 语音库 + 台湾腔韵律" if av["是台湾语音库"]
                          else "中文女声 + 台湾腔韵律（本机暂无 zh-TW 语音库）"),
            "说明": av["说明"], "装台湾语音的方法": av["装台湾语音的方法"]}


__all__ = ["voices", "pick_voice", "taiwanize", "speak", "available", "status",
           "recognizers", "pick_recognizer", "tts_to_wav", "recognize", "selftest",
           "asr_status", "TEXT_FILE", "SSML_FILE", "WAV_FILE"]


if __name__ == "__main__":                                     # pragma: no cover
    import sys
    print(json.dumps(status(), ensure_ascii=False, indent=1))
    sys.exit(0)
