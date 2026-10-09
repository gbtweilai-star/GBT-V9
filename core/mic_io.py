# core/mic_io.py —— 本机麦克风能力：设备自选 · 采集 · 转写 · 优选记忆
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么需要这一层：
#   本机 PortAudio 的「默认输入设备」是 -1（未设置），所以直接 sd.rec()
#   一律报 "Requested device not found"。能力层必须自己枚举设备、挑一台真正
#   存在的采集端，并把选择记住，之后话筒才可用。
#   采集端可能物理不存在（笔记本无机身麦克风）→ 如实报「有端点但无声」，
#   绝不假装听到。立体声混音(Stereo Mix)是回环端，可当「听系统声音」的耳朵。
from core.swallow import swallow as _swallow
import json, time
from pathlib import Path

try:
    import numpy as np
    import sounddevice as sd
except ImportError:                                            # pragma: no cover
    np = None
    sd = None

SAMPLE_RATE = 16000
# Realtek 等 WASAPI 端点不接受 16k（报 Invalid device），必须按端点支持的采样率开流，
# 再重采样到 16k 给识别器。顺序按「识别友好度」排。
RATES = (16000, 48000, 44100, 32000, 22050, 8000)
STATE = Path(__file__).resolve().parent.parent / "state" / "mic.json"

# 优选顺序：真麦克风 → 蓝牙免提 → 回环(混音) → 其它
_PREFER = ("麦克风 (realtek", "麦克风 (", "microphone", "hands-free", "headset",
           "立体声混音", "stereo mix", "input (")
_SKIP = ("@system32", "#4;")          # 纯占位端点，点名里带驱动路径的优先降权
_CACHE: dict = {"t": 0.0, "d": None}


def _mem() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:                                          # noqa: BLE001
        return {}


def _remember(dev: dict) -> None:
    """记下部署的采集端。必须与已有内容合并：基线等校准数据不能被覆盖掉。"""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    cur = _mem()
    cur.update({"名称": dev["名"], "序号": dev["序号"],
                "更新时间": time.strftime("%Y-%m-%d %H:%M:%S")})
    STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")


def _rank(name: str) -> int:
    low = name.lower()
    for i, key in enumerate(_PREFER):
        if key in low:
            return i
    return len(_PREFER) + (5 if any(s in low for s in _SKIP) else 0)


def devices(*, probe: bool = False, seconds: float = 0.4) -> dict:
    """枚举真实采集端。probe=True 时逐台试采，给出「有声/无声」实测结论。"""
    if sd is None or np is None:
        return {"有硬件": False, "设备": [], "结论": "sounddevice/numpy 未安装：本机采集不可用",
                "建议": ["pip install sounddevice numpy"]}
    try:
        raw = sd.query_devices()
    except Exception as exc:                                   # noqa: BLE001
        return {"有硬件": False, "设备": [], "结论": f"设备枚举失败：{type(exc).__name__} {exc}",
                "建议": ["检查音频驱动是否正常"]}
    rows = []
    for idx, d in enumerate(raw):
        ch = int(d.get("max_input_channels", 0))
        if ch <= 0:
            continue
        row = {"序号": idx, "名": d["name"].strip(), "通道": ch,
               "采样率": int(d.get("default_samplerate", 0) or 0)}
        if probe:
            try:
                rate, buf = _grab(idx, seconds)
                row["可用采样率"] = rate
                row["实测"] = "有声" if _rms(buf) > 30 else "无声"
                row["电平"] = round(float(_rms(buf)), 1)
            except Exception as exc:                           # noqa: BLE001
                row["实测"] = "打不开"
                row["原因"] = f"{type(exc).__name__}: {str(exc)[:60]}"
        rows.append(row)
    rows.sort(key=lambda r: (_rank(r["名"]), -r["通道"]))
    best = rows[0] if rows else None
    live = [r for r in rows if r.get("实测") == "有声"]
    if not rows:
        concl = "本机没有可用的采集端点（物理无机身麦克风）"
    elif probe and not live:
        concl = "有采集端点但都收不到声音（多为机身无麦，仅 3.5mm 插孔或回环端）"
    elif probe:
        concl = f"可用采集端：{live[0]['名']}"
    else:
        concl = f"首选采集端：{best['名']}"
    return {"有硬件": bool(rows), "设备": rows, "首选": best, "结论": concl, "建议": _advice(rows, live, probe)}


def _advice(rows: list, live: list, probed: bool) -> list:
    out = []
    if not rows:
        out.append("接一个 USB 麦克风或蓝牙耳机（免手 profile），插上即可用")
        out.append("也可用「立体声混音」当耳朵：听本机播放的声音")
    elif probed and not live:
        out.append("端点存在但无声：笔记本机身无麦克风时属正常，插耳机麦或 USB 麦即有声")
        out.append("Windows 隐私：设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问")
    if any("立体声混音" in r["名"] or "stereo mix" in r["名"].lower() for r in rows):
        out.append("回环端「立体声混音」可用：她可以听电脑正在播放的声音")
    out.append("没有麦克风时仍可打字对话，或用已有音频文件走识别")
    return out


def pick(*, force: bool = False) -> dict:
    """挑一台真实采集端并记住它（写 state/mic.json，重启后仍生效）。"""
    if sd is None:
        return {"ok": False, "reason": "sounddevice 未安装"}
    mem = _mem()
    all_dev = devices()
    if not all_dev.get("有硬件"):
        return {"ok": False, "reason": all_dev["结论"], "建议": all_dev.get("建议", [])}
    rows = all_dev["设备"]
    if not force and mem.get("序号") is not None:
        for r in rows:
            if r["序号"] == mem["序号"]:
                _apply(r)
                return {"ok": True, "设备": r, "来源": "记忆", "结论": f"沿用已部署话筒：{r['名']}"}
    best = all_dev["首选"]
    _apply(best)
    _remember(best)
    return {"ok": True, "设备": best, "来源": "优选",
            "结论": f"已部署话筒：{best['名']}（已记入 state/mic.json）"}


def _apply(dev: dict) -> None:
    try:
        sd.default.device = (dev["序号"], sd.default.device[1])
    except Exception as e:
        _swallow(__file__, e)


def _rms(buf) -> float:
    arr = np.asarray(buf, dtype="float32").reshape(-1)
    return float(np.sqrt((arr ** 2).mean())) if arr.size else 0.0


def _grab(device: int, seconds: float) -> tuple:
    """按端点支持的采样率开流采一段，返回 (实际采样率, int16 单声道缓冲)。"""
    last = None
    for rate in RATES:
        try:
            n = max(1, int(rate * seconds))
            buf = sd.rec(n, samplerate=rate, channels=1, dtype="int16", device=device)
            sd.wait()
            return rate, buf
        except Exception as exc:                               # noqa: BLE001
            last = exc
    raise last if last else RuntimeError("没有可用的采样率")


def _to16k(buf, src_rate: int) -> "np.ndarray":
    arr = np.asarray(buf, dtype="float32").reshape(-1)
    if src_rate == SAMPLE_RATE or arr.size == 0:
        return np.clip(arr, -32768, 32767).astype("<i2")
    dst_n = max(1, int(arr.size * SAMPLE_RATE / float(src_rate)))
    x_src = np.linspace(0.0, 1.0, arr.size, endpoint=False)
    x_dst = np.linspace(0.0, 1.0, dst_n, endpoint=False)
    return np.clip(np.interp(x_dst, x_src, arr), -32768, 32767).astype("<i2")


def _floor(device: int, *, fresh: bool = False) -> float:
    """端点噪声基线：取历史最小值并落盘。单次测量会被瞬时噪声抬高（曾测得 792），
    最小值才代表「安静时的底噪」，据此才不会把正常说话判成静音。"""
    key = f"floor:{device}"
    if not fresh and key in _CACHE:
        return float(_CACHE[key])
    mem = _mem()
    hist = mem.get("基线", {})
    prev = float(hist.get(str(device), 0) or 0)
    try:
        _, buf = _grab(device, 0.6)
        arr = np.asarray(buf, dtype="float32").reshape(-1)
        arr = arr[arr.size // 10:]                             # 丢掉开流瞬态
        val = float(np.sqrt((arr ** 2).mean())) if arr.size else 30.0
    except Exception:                                          # noqa: BLE001
        val = 30.0
    val = min(prev, val) if prev > 0 else val
    if val > 0:
        hist[str(device)] = round(val, 1)
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({**mem, "基线": hist, "更新时间":
                                     time.strftime("%Y-%m-%d %H:%M:%S")},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    _CACHE[key] = val
    return float(val)


def _speech_gate(level: float, floor: float) -> tuple:
    """(是否像有人说话, 阈值)。阈值 = 基线 2.2 倍，并夹在 [90, 900] 内防一手。"""
    th = min(max(90.0, floor * 2.2), 900.0)
    return level > th, th


def record(seconds: float = 3.0, *, device: int | None = None) -> dict:
    """采集一段 WAV（存 16k 单声道，识别器直接用）。未指定设备时用已部署话筒。"""
    if sd is None or np is None:
        return {"ok": False, "reason": "sounddevice/numpy 未安装"}
    idx = device
    if idx is None:
        got = pick()
        if not got.get("ok"):
            return {"ok": False, "reason": got.get("reason", "没有可用话筒"), "建议": got.get("建议", [])}
        idx = got["设备"]["序号"]
    seconds = max(0.5, min(30.0, float(seconds)))
    try:
        rate, buf = _grab(idx, seconds)
    except Exception as exc:                                   # noqa: BLE001
        return {"ok": False, "device": idx, "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
                "建议": ["换一台采集端（如立体声混音）", "确认该端点未被独占"]}
    level = _rms(buf)
    base = _floor(idx)
    heard, th = _speech_gate(level, base)
    out_dir = STATE.parent / "media" / "mic"
    out_dir.mkdir(parents=True, exist_ok=True)
    wav = out_dir / f"mic_{time.strftime('%Y%m%d_%H%M%S')}.wav"
    import wave
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_to16k(buf, rate).tobytes())
    return {"ok": True, "device": idx, "采集采样率": rate, "秒": seconds,
            "电平": round(level, 1), "噪声基线": round(base, 1), "说话阈值": round(th, 1),
            "有声": heard, "wav": str(wav),
            "提示": "" if heard else "没听到明显说话声：此端点只有底噪（未插麦 / 机身无麦 / 该端为静音设备）"}


def listen(seconds: float = 3.5, *, culture: str = "zh-CN") -> dict:
    """采集 + 离线识别（SAPI，免密钥），把话变成文字交给对话。"""
    rec = record(seconds)
    if not rec.get("ok"):
        return {**rec, "文本": "", "环节": "采集"}
    if not rec.get("有声"):
        return {**rec, "文本": "", "环节": "采集", "结论": rec.get("提示", "没听到说话声，未送识别")}
    got = transcribe(rec["wav"], culture=culture)
    out = {**rec, **got, "环节": "识别"}
    if not out.get("文本"):
        out["结论"] = "听到声音但没听清字（离线识别对噪声敏感，再说一次或改用打字）"
    return out


def transcribe(wav: str | Path, *, culture: str = "zh-CN") -> dict:
    """把手上的音频文件变成文字（免密钥离线识别）。"""
    from senses import voice_sapi as VS
    got = VS.recognize(wav, culture=culture)
    txt = ""
    if isinstance(got, dict):
        txt = (got.get("text") or got.get("文本") or "").strip()
    return {"文本": txt, "置信度": round(float(got.get("confidence", 0) or 0), 2)
            if isinstance(got, dict) else 0.0,
            "识别": got,
            "结论": (f"听到：「{txt}」" if txt else "识别没出字，可再说一次或改用打字"),
            "引擎": "Windows SAPI（离线，无需密钥）"}


def deploy(*, with_hooks: bool = True) -> dict:
    """话筒能力部署：设备枚举 → 实测收声 → 落盘记忆 → 固化。走钩子防偷懒。"""
    from core import hooks as H
    g = H.Guard("GBT小土豆V9·本机麦克风能力部署",
                must_steps=("设备枚举", "实测收声", "落盘部署"))
    with g.step("设备枚举", expect="列出全部采集端点并选出首选") as s:
        d = devices(probe=False)
        if not d.get("有硬件"):
            raise H.HookError(f"没有任何采集端点：{d['结论']}")
        s.evidence(端点数=len(d["设备"]), 首选=d["首选"]["名"],
                   fingerprint=H.fingerprint(len(d["设备"]), d["首选"]["序号"]))
    with g.step("实测收声", expect="逐台试采，给出有声/无声实测（不靠猜）") as s:
        dp = devices(probe=True, seconds=0.6)
        live = [r["名"] for r in dp["设备"] if r.get("实测") == "有声"]
        s.evidence(能收声的端点=live, 结论=dp["结论"], fingerprint=H.fingerprint(live))
    with g.step("落盘部署", expect="写入 state/mic.json 可在重启后沿用") as s:
        got = pick(force=True)
        if not got.get("ok"):
            raise H.HookError(f"部署失败：{got.get('reason')}")
        s.evidence(设备=got["设备"]["名"], 记忆=_mem(), fingerprint=H.fingerprint(got["设备"]["序号"]))
    a = g.finish()
    out = {"ok": True, "部署": got["设备"], "能收声的端点": live, "结论": dp["结论"],
           "建议": dp.get("建议", []), "钩子": {"通过": a["通过"], "步数": a["步数"]},
           "口径": "免密钥离线听写；无麦克风时如实告知并保留打字与音频文件两条路"}
    try:
        from core import solidify as S
        S.solidify("mic_io", {"部署": out["部署"], "能收声的端点": live, "结论": dp["结论"]},
                   note="本机麦克风能力（设备协商 + 离线听写）部署")
    except Exception as e:
        _swallow(__file__, e)
    return out


def status(*, probe: bool = False) -> dict:
    """能力状态：给面板如实展示（不夸大、不假装）。"""
    global _CACHE
    if probe or _CACHE["d"] is None:
        _CACHE = {"t": time.time(), "d": devices(probe=probe)}
    d = _CACHE["d"]
    mem = _mem()
    live = [r for r in d["设备"] if r.get("实测") == "有声"]
    return {"已部署": bool(mem.get("序号") is not None), "记忆": mem,
            "有采集端点": d["有硬件"], "能收声的端点": [r["名"] for r in live],
            "结论": d["结论"], "设备": d["设备"], "建议": d.get("建议", []),
            "免密钥识别": True, "识别引擎": "Windows SAPI（离线，无需密钥）",
            "耳朵可用": bool(live) if probe else None}


if __name__ == "__main__":                                     # pragma: no cover
    print(json.dumps(status(probe=True), ensure_ascii=False, indent=2))
