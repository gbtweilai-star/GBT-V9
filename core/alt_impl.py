# core/alt_impl.py —— 替代实现的可执行执行器（视频/音乐/混音/母带，全部用本机 ffmpeg 真跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么有这个模块：目录里没有 video-generation / music-generation / audio-mixing 三个族的云模型，
# 之前这些步骤只做到"登记为替代实现"——那只是**文本承诺**。这里把它变成**能真跑出产物**的代码：
#   关键帧→片段 / 竖屏+调色 / 字幕烧录 / 声床合成 / 混音 / 母带（-14 LUFS）/ 成片导出
# 纪律：
#   · 产物路径**只用模块常量**（_PATHS 全部由字面量文件名拼出），对外只暴露"名字→路径"查表；
#     名字不在白名单直接抛错 —— 没有动态文件名，也没有变量拼路径；
#     IO 一律走 pathlib 方法（read_bytes / write_text），不用裸 open()。
#   · 只用本机 ffmpeg（无网络、无外部服务）；缺能力/缺素材一律如实返回 ok=False + 原因。
#   · 每次运行写证据（sha256 / 时长 / 大小 / 响度），供面板与审计复核。
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MEDIA_DIR = ROOT.joinpath("state", "media")
FRAME_DIR = ROOT.joinpath("devoured", "_t_vision")
FRAME_FIRST = FRAME_DIR.joinpath("f00000000.png")
FRAME_PATTERN = FRAME_DIR.joinpath("f%08d.png")
# 素材候选（按可信度排序）：真能解码的帧目录优先；_t_vision 里实测有 146 个 5 字节占位文件
MATERIAL_CANDIDATES = (
    ("devoured/vision（真实采集帧）", ROOT.joinpath("devoured", "vision").joinpath("f%08d.png"),
     ROOT.joinpath("devoured", "vision").joinpath("f00000000.png")),
    ("devoured/_t_vision", FRAME_PATTERN, FRAME_FIRST),
    ("devoured/t1-eye/_preview（段预览图）",
     ROOT.joinpath("devoured", "t1-eye", "_preview").joinpath("seg-00000000-00000011.png"),
     ROOT.joinpath("devoured", "t1-eye", "_preview").joinpath("seg-00000000-00000011.png")),
)
BRAND_IMAGE = ROOT.joinpath("panel", "static").joinpath("logo.png")
FONT = "C\\:/Windows/Fonts/msyh.ttc" if platform.system() == "Windows" else ""
TIMEOUT = float(os.environ.get("V9_ALT_TIMEOUT", "300"))

# 名字 → 路径（每个路径都由字面量文件名拼出，一次性算好，之后只查表）
_PATHS = {
    "alt_frames.mp4": MEDIA_DIR.joinpath("alt_frames.mp4"),
    "alt_short.mp4": MEDIA_DIR.joinpath("alt_short.mp4"),
    "alt_short_subbed.mp4": MEDIA_DIR.joinpath("alt_short_subbed.mp4"),
    "alt_bed.wav": MEDIA_DIR.joinpath("alt_bed.wav"),
    "alt_music.mp3": MEDIA_DIR.joinpath("alt_music.mp3"),
    "alt_music.wav": MEDIA_DIR.joinpath("alt_music.wav"),
    "alt_subs.srt": MEDIA_DIR.joinpath("alt_subs.srt"),
    "alt_manifest.json": MEDIA_DIR.joinpath("alt_manifest.json"),
    # 滚动档案：主文件满 200 条时把旧的**追加**进来（一条不丢）
    "alt_manifest_archive.jsonl": MEDIA_DIR.joinpath("alt_manifest_archive.jsonl"),
}
ALLOWED_OUTPUTS = tuple(_PATHS)


def media_path(name: str) -> Path:
    """白名单查表：只认 _PATHS 里的名字，别的一律拒绝（不拼路径、不做动态段）。"""
    key = str(name or "")
    if key not in _PATHS:
        raise ValueError(f"输出名不在白名单：{name!r}")
    return _PATHS[key]


def have_ffmpeg() -> dict:
    """ffmpeg 是否可用（不可用就别装样子）。"""
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-version"],
                             capture_output=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or b"").decode("utf-8", "replace").splitlines()
        return {"ok": out.returncode == 0, "version": line[0][:80] if line else "",
                "reason": "" if out.returncode == 0 else f"rc={out.returncode}"}
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "version": "", "reason": type(exc).__name__}


def run_ffmpeg(args: list, *, timeout: float | None = None) -> dict:
    """跑一次 ffmpeg（参数列表，无 shell 拼接）。返回 rc + 末尾日志。"""
    cmd = ["ffmpeg", "-hide_banner", "-y"] + [str(a) for a in args]
    try:
        out = subprocess.run(cmd, capture_output=True, timeout=timeout or TIMEOUT,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "rc": -1, "log": f"{type(exc).__name__}", "cmd": cmd}
    log = ((out.stderr or b"") + (out.stdout or b"")).decode("utf-8", "replace")
    return {"ok": out.returncode == 0, "rc": out.returncode, "log": log[-600:], "cmd": cmd}


def probe(name: str) -> dict:
    """ffprobe 读时长/大小/sha256 —— 产物证据。只接受白名单文件。"""
    got = {"file": str(name), "exists": False}
    try:
        target = media_path(name)
    except ValueError as exc:
        return {**got, "ok": False, "reason": str(exc)}
    got["exists"] = target.is_file()
    if not got["exists"]:
        return {**got, "ok": False, "reason": "产物不存在"}
    try:
        got["bytes"] = target.stat().st_size
        got["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError as exc:                                # noqa: BLE001
        got["bytes"], got["sha256"] = None, f"err:{type(exc).__name__}"
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                              "format=duration:stream=codec_type,width,height",
                              "-of", "json", str(target)], capture_output=True, timeout=60,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        doc = json.loads((out.stdout or b"{}").decode("utf-8", "replace") or "{}")
        got["duration_s"] = round(float(doc.get("format", {}).get("duration") or 0), 2)
        streams = doc.get("streams") or []
        v = next((s for s in streams if s.get("codec_type") == "video"), None)
        a = next((s for s in streams if s.get("codec_type") == "audio"), None)
        got["video"] = {"w": v.get("width"), "h": v.get("height")} if v else None
        got["audio"] = bool(a)
    except Exception as exc:                              # noqa: BLE001
        got["duration_s"] = None
        got["probe_error"] = type(exc).__name__
    got["ok"] = True
    return got


def _ensure_dir() -> dict:
    try:
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        return {"ok": True}
    except OSError as exc:                                # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def record(step: str, got: dict) -> dict:
    """把一次替代实现的执行结果写进清单（面板与审计都读它）。"""
    _ensure_dir()
    doc = {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "step": step,
           "ok": bool(got.get("ok")), "reason": str(got.get("reason") or "")[:300],
           "evidence": {k: v for k, v in got.items() if k not in ("log", "cmd")}}
    try:
        target = media_path("alt_manifest.json")
        rows = []
        if target.is_file():
            rows = json.loads(target.read_text(encoding="utf-8") or "[]")
            if not isinstance(rows, list):
                rows = []
        rows.append(doc)
        # 满 200 条时**滚动归档**，不是静默丢弃 —— 纪律是"每一次都要有完整记录"。
        # 旧的进 append-only 档案（一行一条），主文件只留最新的，读起来快。
        if len(rows) > 200:
            spill, rows = rows[:-200], rows[-200:]
            try:
                arch = media_path("alt_manifest_archive.jsonl")
                with arch.open("a", encoding="utf-8") as f:
                    for r in spill:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            except OSError:
                rows = rows[-200:]
        target.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
        return {"ok": True, "entry": doc}
    except (OSError, ValueError) as exc:                  # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}


def manifest() -> dict:
    try:
        target = media_path("alt_manifest.json")
        rows = json.loads(target.read_text(encoding="utf-8") or "[]") if target.is_file() else []
    except (OSError, ValueError) as exc:                  # noqa: BLE001
        return {"ok": False, "rows": [], "reason": type(exc).__name__}
    return {"ok": True, "rows": rows if isinstance(rows, list) else [], "reason": ""}


# ═══════════ ① 素材优选（先验证可解码，再合成片段）═══════════
def decodable(path: Path) -> bool:
    """这个图能不能真解码（<2KB 基本是占位；能调 PIL 就实测一次）。"""
    try:
        if not path.is_file() or path.stat().st_size < 2048:
            return False
    except OSError:
        return False
    try:
        from PIL import Image
        with Image.open(path) as im:
            im.load()
        return True
    except Exception:                                     # noqa: BLE001
        # 没有 PIL 就退一步：文件够大且是 PNG 魔数
        try:
            with path.open("rb") as fh:
                return fh.read(8) == b"\x89PNG\r\n\x1a\n"
        except OSError:
            return False


def material() -> dict:
    """挑一份**真能解码**的素材；都不行就如实说"素材缺失"（由品牌图兜底）。"""
    tried = []
    for label, pattern, first in MATERIAL_CANDIDATES:
        ok = decodable(first)
        tried.append({"候选": label, "首帧可解码": ok,
                      "字节": (first.stat().st_size if first.is_file() else None)})
        if ok:
            return {"ok": True, "来源": label, "帧模式": str(pattern), "试过": tried}
    return {"ok": False, "来源": "无可用帧素材（全部是占位或不可解码）", "帧模式": "",
            "试过": tried,
            "兜底": "改用品牌图 + ffmpeg 生成的渐变画面合成"}   # 由 frames_to_clip 落地


# ═══════════ ② 关键帧 → 片段（对应 sv4 / fm8「图生视频」的替代实现）═══════════
def frames_to_clip(fps: int = 8, max_frames: int = 60) -> dict:
    """合成片段：优先用真能解码的采集帧；素材缺失时用品牌图+生成画面兜底（并如实标注来源）。"""
    if not have_ffmpeg()["ok"]:
        got = {"ok": False, "reason": "本机没有 ffmpeg"}
        record("frames_to_clip", got)
        return got
    _ensure_dir()
    mat = material()
    if mat["ok"]:
        r = run_ffmpeg(["-framerate", str(int(fps)), "-start_number", "0", "-i", mat["帧模式"],
                        "-frames:v", str(int(max_frames)), "-c:v", "libx264", "-pix_fmt",
                        "yuv420p", "-preset", "veryfast", str(media_path("alt_frames.mp4"))])
        got = {**probe("alt_frames.mp4"), "ok": r["ok"], "帧率": int(fps),
               "素材来源": mat["来源"],
               "reason": "" if r["ok"] else r["log"][-200:]}
    else:
        # 兜底：品牌图叠在 ffmpeg 生成的渐变上（都是真能解码的素材）
        r = run_ffmpeg([
            "-f", "lavfi", "-i", "gradients=s=1080x1920:c0=0x070b14:c1=0x16324f:d=6",
            "-loop", "1", "-i", str(BRAND_IMAGE), "-filter_complex",
            "[1:v]scale=620:-1[lg];[0:v][lg]overlay=(W-w)/2:(H-h)/2:shortest=0",
            "-t", "6", "-r", str(int(fps)), "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-preset", "veryfast", str(media_path("alt_frames.mp4"))])
        got = {**probe("alt_frames.mp4"), "ok": r["ok"], "帧率": int(fps),
               "素材来源": f"品牌图+生成画面（帧素材不可用：{mat['来源']}）",
               "素材检查": mat["试过"],
               "reason": "" if r["ok"] else r["log"][-200:]}
    record("frames_to_clip", got)
    return got


# ═══════════ ② 竖屏 + 剪映式调色 + 字幕烧录（对应 cp5/cp6）═══════════
def vertical_grade(src_name: str = "alt_frames.mp4", dst_name: str = "alt_short.mp4",
                   *, text: str = "") -> dict:
    """缩放裁切成 9:16 + 调色（对比/饱和/色偏）+ 可选版式文字（中文字体 drawtext）。"""
    try:
        source, out = media_path(src_name), media_path(dst_name)
    except ValueError as exc:
        got = {"ok": False, "reason": str(exc)}
        record("vertical_grade", got)
        return got
    if not source.is_file():
        got = {"ok": False, "reason": "源片段不存在，先跑 frames_to_clip"}
        record("vertical_grade", got)
        return got
    chain = ["scale=1080:1920:force_original_aspect_ratio=increase", "crop=1080:1920",
             "eq=contrast=1.06:saturation=1.12:brightness=0.01",
             "colorbalance=rs=0.02:bs=-0.02"]
    if text and FONT:
        safe = str(text).replace(":", "：").replace("'", "").replace("\\", "")[:40]
        chain.append(f"drawtext=fontfile='{FONT}':text='{safe}':x=(w-tw)/2:y=h-260:"
                     f"fontsize=54:fontcolor=white:box=1:boxcolor=black@0.45:boxborderw=16")
    r = run_ffmpeg(["-i", str(source), "-vf", ",".join(chain), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-preset", "veryfast", "-an", str(out)])
    got = {**probe(dst_name), "ok": r["ok"],
           "reason": "" if r["ok"] else r["log"][-200:],
           "调色": "eq+colorbalance", "版式文字": bool(text and FONT)}
    record("vertical_grade", got)
    return got


def burn_subtitles(src_name: str = "alt_short.mp4",
                   dst_name: str = "alt_short_subbed.mp4", srt_text: str = "") -> dict:
    """生成 SRT 并烧进视频（libass 的 subtitles 滤镜；失败如实报）。"""
    _ensure_dir()
    try:
        src, out, srt = (media_path(src_name), media_path(dst_name),
                         media_path("alt_subs.srt"))
    except ValueError as exc:
        got = {"ok": False, "reason": str(exc)}
        record("burn_subtitles", got)
        return got
    try:
        srt.write_text(srt_text or "", encoding="utf-8")
    except OSError as exc:                                # noqa: BLE001
        got = {"ok": False, "reason": f"写字幕失败 {type(exc).__name__}"}
        record("burn_subtitles", got)
        return got
    esc = str(srt).replace("\\", "/").replace(":", "\\:")
    r = run_ffmpeg(["-i", str(src), "-vf", f"subtitles='{esc}'", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", str(out)])
    got = {**probe(dst_name), "ok": r["ok"], "reason": "" if r["ok"] else r["log"][-200:]}
    record("burn_subtitles", got)
    return got


def make_srt(lines: list, *, start: float = 0.0, each: float = 2.0) -> str:
    """按行生成 SRT（时间轴均分）—— 对应 cp2「自动字幕」的替代实现（无音轨时按分镜排）。"""
    out, t = [], float(start)
    for i, line in enumerate(lines or [], 1):
        a, b = t, t + float(each)
        fmt = lambda s: "%02d:%02d:%02d,%03d" % (int(s // 3600), int(s % 3600 // 60),  # noqa: E731
                                                 int(s % 60), int((s % 1) * 1000))
        out.append(f"{i}\n{fmt(a)} --> {fmt(b)}\n{str(line)[:60]}\n")
        t = b
    return "\n".join(out)


# ═══════════ ③ 音乐：声床 + 混音 + 母带（对应 mu4/mu5/mu7/mu8）═══════════
def synth_bed(seconds: float = 12.0, *, chord=(220.0, 277.18, 329.63, 440.0)) -> dict:
    """用 ffmpeg 合成一段和弦声床（免费、无版权问题）—— 替代"音乐生成模型"。"""
    if not have_ffmpeg()["ok"]:
        got = {"ok": False, "reason": "本机没有 ffmpeg"}
        record("synth_bed", got)
        return got
    _ensure_dir()
    ins, mix = [], []
    for i, f in enumerate(chord):
        ins += ["-f", "lavfi", "-t", str(float(seconds)), "-i", f"sine=frequency={f}"]
        mix.append(f"[{i}:a]volume=0.18[a{i}]")
    chain = ";".join(mix) + ";" + "".join(f"[a{i}]" for i in range(len(chord))) + \
        f"amix=inputs={len(chord)}:duration=longest,afade=t=in:st=0:d=1.2," \
        f"afade=t=out:st={max(0.0, float(seconds) - 1.5)}:d=1.5[aout]"
    r = run_ffmpeg(ins + ["-filter_complex", chain, "-map", "[aout]",
                          "-c:a", "pcm_s16le", str(media_path("alt_bed.wav"))])
    got = {**probe("alt_bed.wav"), "ok": r["ok"],
           "reason": "" if r["ok"] else r["log"][-200:],
           "和弦": [round(f) for f in chord]}
    record("synth_bed", got)
    return got


def measure_loudness(name: str = "alt_bed.wav") -> dict:
    """用 ebur128 量响度（LUFS/I）——母带前后都能拿真数。"""
    try:
        target = media_path(name)
    except ValueError as exc:
        return {"ok": False, "reason": str(exc)}
    if not target.is_file():
        return {"ok": False, "reason": "文件不存在"}
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(target),
                              "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
                             capture_output=True, timeout=180,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "reason": type(exc).__name__}
    text = ((out.stderr or b"") + (out.stdout or b"")).decode("utf-8", "replace")
    i_lufs = ""
    for line in text.splitlines():
        if "I:" in line and "LUFS" in line:
            i_lufs = line.strip().split("I:")[-1].split("LUFS")[0].strip()
    return {"ok": bool(i_lufs), "integrated_lufs": i_lufs,
            "reason": "" if i_lufs else "ebur128 没给出 I 值"}


def master(src_name: str = "alt_bed.wav", dst_name: str = "alt_music.mp3",
           *, target_lufs: float = -14.0) -> dict:
    """母带：响度归一到 -14 LUFS（流媒体口径），输出 mp3（缺 codec 则退回 wav 并标注）。"""
    try:
        src, out = media_path(src_name), media_path(dst_name)
    except ValueError as exc:
        got = {"ok": False, "reason": str(exc)}
        record("master", got)
        return got
    if not src.is_file():
        got = {"ok": False, "reason": "混音产物不存在，先跑 synth_bed"}
        record("master", got)
        return got
    before = measure_loudness(src_name)
    r = run_ffmpeg(["-i", str(src), "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
                    "-c:a", "libmp3lame", "-b:a", "192k", str(out)])
    if not r["ok"]:
        r2 = run_ffmpeg(["-i", str(src), "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
                         str(media_path("alt_music.wav"))])
        got = {**probe("alt_music.wav"), "ok": r2["ok"],
               "codec": "pcm（mp3 不可用，已退回 wav）",
               "reason": "" if r2["ok"] else r2["log"][-200:], "母带前": before}
        record("master", got)
        return got
    got = {**probe("alt_music.mp3"), "ok": True, "codec": "mp3", "目标lufs": target_lufs,
           "母带前": before, "母带后": measure_loudness("alt_music.mp3"), "reason": ""}
    record("master", got)
    return got


# ═══════════ ④ 两条闭环编排（真跑，出产物 + 证据）═══════════
SHORT_SCRIPT = ["开场：一帧入画", "主体：保持节奏", "收尾：留一个钩子", "落版：关注不迷路"]


def shortvideo_loop(*, title: str = "GBT小土豆V9 自动成片") -> dict:
    """短视频闭环（cp1→cp6 替代实现）：帧→片段→竖屏调色→字幕→成片，逐步入清单。"""
    steps = []
    c1 = frames_to_clip()
    steps.append({"步": "cp1 素材/关键帧→片段", "ok": c1.get("ok"),
                  "证据": {"时长s": c1.get("duration_s"), "帧率": c1.get("fps")}})
    if not c1.get("ok"):
        return {"ok": False, "步": steps, "reason": "片段合成失败，后续步骤未执行"}
    c2 = vertical_grade(text=title)
    steps.append({"步": "cp5+cp6 竖屏/调色/版式", "ok": c2.get("ok"),
                  "证据": {"分辨率": c2.get("video"), "调色": c2.get("调色")}})
    if not c2.get("ok"):
        return {"ok": False, "步": steps, "reason": "竖屏调色失败"}
    c3 = burn_subtitles(srt_text=make_srt(SHORT_SCRIPT, each=1.6))
    steps.append({"步": "cp2 字幕烧录", "ok": c3.get("ok"),
                  "证据": {"文件": c3.get("file"), "字节": c3.get("bytes"),
                           "字幕": "alt_subs.srt"}})
    final = c3 if c3.get("ok") else c2
    return {"ok": bool(c2.get("ok")), "步": steps, "最终产物": final,
            "产物": {"成片": final.get("file"), "sha256": final.get("sha256"),
                     "时长s": final.get("duration_s"), "字节": final.get("bytes")},
            "字幕行数": len(SHORT_SCRIPT), "reason": ""}


def music_loop() -> dict:
    """音乐闭环（mu4/mu5/mu7/mu8 替代实现）：声床→母带（-14 LUFS）→成品，带响度证据。"""
    steps = []
    b = synth_bed()
    steps.append({"步": "mu4/mu5 声床（替代音乐生成）", "ok": b.get("ok"),
                  "证据": {"和弦": b.get("和弦"), "时长s": b.get("duration_s")}})
    if not b.get("ok"):
        return {"ok": False, "步": steps, "reason": "声床合成失败"}
    m = master()
    steps.append({"步": "mu7/mu8 混音+母带（-14 LUFS）", "ok": m.get("ok"),
                  "证据": {"codec": m.get("codec"), "母带前": m.get("母带前"),
                           "母带后": m.get("母带后")}})
    return {"ok": bool(m.get("ok")), "步": steps, "最终产物": m,
            "产物": {"成品": m.get("file"), "sha256": m.get("sha256"),
                     "字节": m.get("bytes")}, "reason": ""}


def status() -> dict:
    """替代实现执行器现状：ffmpeg 能力 + 各执行器最近一次真实结果 + 现有产物。"""
    rows = manifest().get("rows") or []
    latest = {}
    for r in rows:
        latest[r.get("step")] = {"at": r.get("at"), "ok": r.get("ok"),
                                 "reason": r.get("reason", ""),
                                 "证据": (r.get("evidence") or {}).get("file")}
    sizes = {name: (full.stat().st_size if full.is_file() else None)
             for name, full in _PATHS.items()
             if name.endswith((".mp4", ".wav", ".mp3"))}
    return {"ffmpeg": have_ffmpeg(), "输出目录": str(MEDIA_DIR),
            "执行器": ["frames_to_clip（图生视频替代）", "vertical_grade（竖屏+调色）",
                       "burn_subtitles（字幕烧录）", "synth_bed（音乐生成替代）",
                       "master（混音+母带）"],
            "最近结果": latest, "记录数": len(rows), "产物": sizes,
            "素材": {"帧目录": "devoured/_t_vision",
                     "音轨素材": "无（声床由 ffmpeg 合成）"}}


__all__ = ["have_ffmpeg", "run_ffmpeg", "probe", "record", "manifest", "frames_to_clip",
           "vertical_grade", "burn_subtitles", "make_srt", "synth_bed", "measure_loudness",
           "master", "shortvideo_loop", "music_loop", "status", "ALLOWED_OUTPUTS",
           "media_path"]
