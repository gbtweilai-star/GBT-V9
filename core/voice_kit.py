# core/voice_kit.py —— 语音三件套：免费语音 / 生产语音 / 克隆语音（默认：**女声·台湾腔**）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令：**免费语音、生产语音、克隆语音全都要弄好；数字人默认语音＝女声·台湾腔**。
# 三档口径（不混为一谈）：
#   ① 免费语音（默认）：edge-tts，零成本；默认音色 **zh-TW-HsiaoChenNeural（女·台湾）**
#   ② 生产语音：同一引擎但按交付规格出（44.1k / 单声道 / 响度 -14 LUFS / 分句停顿可控）
#   ③ 克隆语音：**需本地模型**（XTTS/GPT-SoVITS/fish-speech/Piper）——本模块只做"就绪探针 + 音色资产登记"，
#      缺什么就如实报什么（不许假装克隆好了）
import json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "state" / "voice_assets.json"
OUTDIR = ROOT / "render" / "voice"
LEDGER = ROOT / "state" / "voice_runs.jsonl"

# 台湾腔候选（女优先）
TW_FEMALE_DEFAULT = "zh-TW-HsiaoChenNeural"
CATALOG = {
    "台湾腔·女·晓臻": "zh-TW-HsiaoChenNeural",
    "台湾腔·女·晓雨": "zh-TW-HsiaoYuNeural",
    "台湾腔·男·云哲": "zh-TW-YunJheNeural",
    "普通话·女·晓晓": "zh-CN-XiaoxiaoNeural",
    "普通话·男·云希": "zh-CN-YunxiNeural",
}


def _log(rec):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def ensure_assets() -> dict:
    """音色资产表（G5A）：默认女声台湾腔；没有就建，已有就沿用（不覆盖主人改过的）。"""
    if ASSETS.is_file():
        try:
            return json.loads(ASSETS.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    d = {"默认": {"音色名": "台湾腔·女·晓臻", "edge_voice": TW_FEMALE_DEFAULT, "性别": "女", "口音": "台湾"},
         "目录": CATALOG,
         "角色音色": {"旁白": "台湾腔·女·晓臻", "女主": "台湾腔·女·晓雨", "男主": "台湾腔·男·云哲"},
         "生产规格": {"采样率": 44100, "声道": 1, "响度": "-14 LUFS", "峰值": "-1 dBTP"},
         "克隆就绪": clone_readiness(),
         "更新": time.strftime("%Y-%m-%dT%H:%M:%S")}
    ASSETS.parent.mkdir(parents=True, exist_ok=True)
    ASSETS.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


def clone_readiness() -> dict:
    """克隆语音就绪探针：本地有没有可用的克隆模型/权重（如实报）。"""
    import importlib.util as u
    mods = {}
    for name in ("TTS", "torch", "piper", "fish_speech", "GPT_SoVITS"):
        try:
            mods[name] = bool(u.find_spec(name))
        except Exception:  # noqa: BLE001
            mods[name] = False
    for d in ("models/xtts", "models/piper", "models/gpt-sovits", "render/voice/refs"):
        mods["目录:" + d] = (ROOT / d).is_dir()
    ready = bool(mods.get("torch")) and (mods.get("TTS") or mods.get("piper") or mods.get("fish_speech"))
    return {"可用": ready, "探测": mods,
            "口径": "克隆要本地模型+参考音频；缺什么报什么（本模块不假装克隆好了）"}


def say(text: str, *, voice: str = "", out: Path | None = None, profile: str = "免费") -> dict:
    """说话：默认**台湾腔女声**；profile=免费/生产（生产档带规格化与响度归一）。"""
    a = ensure_assets()
    v = voice or a.get("默认", {}).get("edge_voice") or TW_FEMALE_DEFAULT
    OUTDIR.mkdir(parents=True, exist_ok=True)
    raw = out or (OUTDIR / ("say-" + str(int(time.time())) + ".mp3"))
    cmd = [sys.executable, "-m", "edge_tts", "--voice", v, "--text", text, "--write-media", str(raw)]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    ok = raw.is_file() and raw.stat().st_size > 1000
    final = raw
    if ok and profile == "生产":
        final = raw.with_name(raw.stem + "-生产规格.wav")
        subprocess.run(["ffmpeg", "-y", "-i", str(raw), "-ar", "44100", "-ac", "1",
                        "-af", "loudnorm=I=-14:TP=-1:LRA=11", "-c:a", "pcm_s16le", str(final)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok = final.is_file()
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "voice.say", "音色": v, "档": profile,
           "ok": ok, "文件": str(final.name), "字节": final.stat().st_size if final.is_file() else 0,
           "错误": (p.stderr or "")[-120:]}
    _log(rec)
    return rec


def status() -> dict:
    a = ensure_assets()
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-4:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"默认音色": a.get("默认"), "可选音色数": len(a.get("目录", {})), "角色映射": a.get("角色音色"),
            "生产规格": a.get("生产规格"), "克隆就绪": clone_readiness(), "最近": rows}


__all__ = ["TW_FEMALE_DEFAULT", "CATALOG", "ensure_assets", "clone_readiness", "say", "status", "ASSETS"]
