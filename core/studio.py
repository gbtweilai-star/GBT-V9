# core/studio.py —— 创作链（专业版）：旁白 · 配乐 · 字幕 · 片头尾卡 · 成片（全本地零付费）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 验收基线（2026-10-09 实测）：1080x1920 · 30fps · h264+aac · 烧入字幕（一次一条）
# 已踩过的坑（都写在代码里）：
#   ① ffprobe 在文件刚落地时会读不到 format ⇒ KeyError（500）→ 加重试与兜底；
#   ② 字幕按 | 切会把整段挤成一条 ⇒ 必须按中文句号/问号/叹号切；
#   ③ 时间轴要用 setpts=N/FRAME_RATE/TB（PTS-STARTPTS 会让循环画面把字幕钉在第一句）；
#   ④ f-string 里嵌同类引号会被解释器拒 ⇒ 一律用 % 格式化。
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "state" / "studio"
VOICE_TW = "zh-TW-HsiaoChenNeural"        # 台湾腔女声（主人指定优先）
W, H, FPS = 1080, 1920, 30
FONT_CANDIDATES = ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf")


def _run(cmd: list, timeout: int = 900) -> tuple:
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout or b"") + (r.stderr or b"")


def probe_duration(path) -> float:
    """稳妥取时长：① ffprobe 刚写完的文件可能没有 format 字段 ⇒ 重试（实测踩过，会 500）。"""
    for _ in range(4):
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "json", str(path)], capture_output=True)
        try:
            return float(json.loads(r.stdout.decode("utf-8", "replace"))["format"]["duration"])
        except Exception:  # noqa: BLE001
            time.sleep(0.6)
    return 0.0


def tools_ready() -> dict:
    ok = {}
    code, _ = _run(["ffmpeg", "-version"], 30)
    ok["ffmpeg"] = code == 0
    code, _ = _run([sys.executable, "-m", "edge_tts", "--help"], 40)
    ok["edge_tts"] = code == 0
    try:
        import PIL  # noqa: F401
        ok["Pillow"] = True
    except Exception:  # noqa: BLE001
        ok["Pillow"] = False
    ok["全免费"] = True
    return ok


def narrate(text: str, out: Path | None = None, voice: str = VOICE_TW) -> dict:
    """① 旁白（edge-tts，台湾腔默认）。写完后稳取时长；取不到就重合成一次。"""
    STATE.mkdir(parents=True, exist_ok=True)
    out = Path(out or (STATE / "narration.mp3"))
    for attempt in range(2):
        _run([sys.executable, "-m", "edge_tts", "--voice", voice, "--text", text,
              "--write-media", str(out)], 240)
        if out.is_file() and out.stat().st_size > 1024:
            d = probe_duration(out)
            if d > 0:
                return {"ok": True, "文件": str(out.relative_to(ROOT)), "字节": out.stat().st_size,
                        "音色": voice, "时长": round(d, 2), "第几次": attempt + 1}
    return {"ok": False, "文件": str(out), "reason": "edge-tts 没产出可读音频"}


def music(duration: float = 24.0, out: Path | None = None, sr: int = 44100) -> dict:
    """② 配乐：本地算法（Am-F-C-G + 泛音包络 + 轻鼓点 + 低音铺底），零版权零费用。"""
    STATE.mkdir(parents=True, exist_ok=True)
    out = Path(out or (STATE / "music.wav"))
    chords = [(220.00, 261.63, 329.63), (174.61, 220.00, 261.63),
              (261.63, 329.63, 392.00), (196.00, 246.94, 293.66)]
    t = np.arange(int(sr * max(3.0, duration))) / sr
    buf = np.zeros_like(t)
    bars = 8
    bar = len(t) / sr / bars
    for i in range(bars):
        f = chords[i % 4]
        seg = (t >= i * bar) & (t < (i + 1) * bar)
        ts = t[seg] - i * bar
        env = np.minimum(1.0, ts / 0.35) * np.exp(-ts * 0.9)
        for k, fr in enumerate(f):
            buf[seg] += (0.15 / (k + 1)) * np.sin(2 * np.pi * fr * ts) * env
            buf[seg] += (0.05 / (k + 1)) * np.sin(2 * np.pi * fr * 2 * ts) * env
        buf[seg] += 0.06 * np.sin(2 * np.pi * (f[0] / 2) * ts) * env      # 低音铺底
    for i in range(int(len(t) / sr)):
        if i % 2 == 0:
            s = slice(i * sr, min(len(buf), i * sr + int(0.06 * sr)))
            buf[s] += 0.11 * np.exp(-np.arange(s.stop - s.start) / (0.012 * sr))
    buf = buf / max(1e-9, float(np.abs(buf).max())) * 0.55
    fade = int(1.2 * sr)
    buf[:fade] *= np.linspace(0, 1, fade)
    buf[-fade:] *= np.linspace(1, 0, fade)
    pcm = (np.stack([buf, buf * 0.96], axis=1) * 32767).astype(np.int16)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return {"ok": out.is_file(), "文件": str(out.relative_to(ROOT)),
            "字节": out.stat().st_size if out.is_file() else 0, "时长": round(len(t) / sr, 2),
            "说明": "本地算法生成（和弦进行+泛音+鼓点+低音），无版权风险"}


def cues(text: str, duration: float, out: Path | None = None) -> dict:
    """③ 字幕/口型轴：**按句切**（。！？；|换行），按字数分配时间窗。"""
    STATE.mkdir(parents=True, exist_ok=True)
    out = Path(out or (STATE / "subs.srt"))
    parts = [c.strip() for c in re.split(r"[|\n。！？!?；;]+", str(text)) if c.strip()]
    if not parts:
        return {"ok": False, "reason": "空文本"}
    # 太长的句再按逗号切，保证"一次一条、不糊屏"
    fine = []
    for c in parts:
        if len(c) <= 18:
            fine.append(c)
        else:
            fine += [x.strip() for x in re.split(r"[，,、]", c) if x.strip()]
    total = sum(len(c) for c in fine)
    t0 = 0.3
    rows = []
    with out.open("w", encoding="utf-8") as f:
        for i, c in enumerate(fine, 1):
            span = duration * (len(c) / total)
            a, b = t0, min(duration - 0.15, t0 + span - 0.08)
            fmt = lambda x: "%02d:%02d:%02d,%03d" % (int(x // 3600), int(x % 3600 // 60), int(x % 60), int((x % 1) * 1000))
            f.write("%d\n%s --> %s\n%s\n\n" % (i, fmt(a), fmt(b), c))
            rows.append({"条": i, "起": round(a, 2), "止": round(b, 2), "文": c})
            t0 = b + 0.08
    # ★同时生成**带 PlayRes 的 ASS**（专业做法）：force_style 的字号按脚本分辨率算，
    #   而 libass 默认 384x288 ⇒ 30pt 在 1080 宽下会变成巨字、被挤成多行（实测踩过）。
    ass = out.with_suffix(".ass")
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\n"
            "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
            " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline,"
            " Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: GBT,Microsoft YaHei,44,&H00FFFFFF,&H00FFFFFF,&HC0000000,&H5A000000,-1,0,0,0,100,100,"
            "0,0,1,3,1,2,96,96,180,1\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n" % (W, H))
    def _at(x):
        return "%d:%02d:%02d.%02d" % (int(x // 3600), int(x % 3600 // 60), int(x % 60), int((x % 1) * 100))
    with ass.open("w", encoding="utf-8") as f:
        f.write(head)
        for r in rows:
            f.write("Dialogue: 0,%s,%s,GBT,,0,0,0,,%s\n" % (_at(r["起"]), _at(r["止"]), r["文"]))
    return {"ok": True, "文件": str(out.relative_to(ROOT)), "ass": str(ass.relative_to(ROOT)),
            "条数": len(rows), "行": rows}


def _font(size: int):
    from PIL import ImageFont
    for p in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(p, size)
        except Exception:  # noqa: BLE001 as _e_swallow
            _swallow(__file__, _e_swallow)
            continue
    return ImageFont.load_default()


def card(kind: str, title: str, sub: str = "", out: Path | None = None, seconds: float = 2.6) -> dict:
    """片头/片尾卡（PIL 生成 → ffmpeg 转成短片）—— 专业版式的骨架。"""
    from PIL import Image, ImageDraw
    STATE.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (W, H), (11, 14, 20))
    dr = ImageDraw.Draw(img)
    for y in range(H):                                    # 竖向渐变
        k = y / H
        dr.line([(0, y), (W, y)], fill=(int(11 + 16 * k), int(14 + 18 * k), int(20 + 26 * k)))
    # 版式：金线 → 标题 → 副题 → 品牌，**行距拉开**（第一版全挤在 0.30~0.45 直接叠字）
    dr.rectangle([int(W * 0.18), int(H * 0.335), int(W * 0.82), int(H * 0.3375)], fill=(243, 205, 60))
    ft_t, ft_s, ft_b = _font(88), _font(38), _font(28)
    dr.text((W / 2, H * 0.415), title, font=ft_t, fill=(240, 245, 250), anchor="mm")
    if sub:
        dr.text((W / 2, H * 0.480), sub, font=ft_s, fill=(150, 165, 185), anchor="mm")
    dr.text((W / 2, H * 0.545), "GBT 小土豆 V9 · 全本地制作", font=ft_b, fill=(140, 158, 180), anchor="mm")
    dr.text((W / 2, H * 0.94), kind, font=_font(26), fill=(110, 125, 145), anchor="mm")
    png = STATE / ("card_%s.png" % kind)
    img.save(png)
    out = Path(out or (STATE / ("card_%s.mp4" % kind)))
    code, log = _run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", "%.2f" % seconds,
                      "-i", str(png), "-vf", "scale=%d:%d,fps=%d" % (W, H, FPS),
                      "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out)], 240)
    return {"ok": out.is_file(), "文件": str(out.relative_to(ROOT)), "秒": seconds,
            "字节": out.stat().st_size if out.is_file() else 0}


def normalize(paths: list, outdir: Path | None = None) -> list:
    """把所有片段统一成 W×H@FPS 同规格 —— **不统一 concat 会直接截断**（实测：5 秒就断了）。"""
    outdir = Path(outdir or (STATE / "norm")); outdir.mkdir(parents=True, exist_ok=True)
    out = []
    for i, p in enumerate(paths):
        p = Path(p)
        if not p.is_file() or probe_duration(p) <= 0:
            continue
        q = outdir / ("%02d_%s.mp4" % (i, p.stem))
        vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
              "pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=0x0B0E14,fps=%d,format=yuv420p" % (W, H, W, H, FPS))
        _run(["ffmpeg", "-y", "-v", "error", "-i", str(p), "-vf", vf,
              "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-an", str(q)], 400)
        if q.is_file():
            out.append(q)
    return out


def compose(clips: list, vo: Path, bgm: Path, subs: Path, out: Path,
            intro: Path | None = None, outro: Path | None = None) -> dict:
    """④ 成片：片头 + 画面 + 片尾 → 1080x1920@30fps + 旁白 + 配乐 + 字幕（一次一条）。"""
    if not Path(vo).is_file():
        return {"ok": False, "reason": "缺旁白"}
    style = ("FontName=Microsoft YaHei,FontSize=30,PrimaryColour=&H00FFFFFF&,"
             "OutlineColour=&HC0000000&,BackColour=&H50000000&,BorderStyle=1,Outline=3,Shadow=1,"
             "Alignment=2,MarginV=150,MarginL=90,MarginR=90")
    sub_esc = str(subs).replace("\\", "/").replace(":", "\\:")
    d = probe_duration(vo)
    # 三类素材各自归一化到同一规格（不统一 concat 会截断——实测踩过）
    n_intro = normalize([intro]) if intro else []
    n_outro = normalize([outro]) if outro else []
    n_body = normalize([Path(c) for c in clips if Path(c).is_file()])
    if not n_body:
        return {"ok": False, "reason": "缺画面"}
    lead = probe_duration(n_intro[0]) if n_intro else 0.0
    # ① 内容段循环铺到旁白长度，并**只在这里**烧字幕（专业做法：卡上不压字幕）
    body_lines, tot = [], 0.0
    while tot < d + 0.5:
        for p in n_body:
            body_lines.append("file '" + p.as_posix() + "'")
            tot += probe_duration(p) or 3.0
            if tot >= d + 0.5:
                break
    body_seq = STATE / "concat_body.txt"
    body_seq.write_text("\n".join(body_lines), encoding="utf-8")
    body = STATE / "body_subbed.mp4"
    # 优先用带 PlayRes 的 ASS（字号才准）；没有才退回 SRT+force_style
    ass_path = Path(subs).with_suffix(".ass")
    if ass_path.is_file():
        sub_filter = "subtitles='%s'" % str(ass_path).replace("\\", "/").replace(":", "\\:")
    else:
        sub_filter = "subtitles='%s':force_style='%s'" % (sub_esc, style)
    _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(body_seq),
          "-vf", sub_filter, "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", "-an",
          str(body)], 900)
    if not body.is_file():
        return {"ok": False, "reason": "内容段烧字幕失败"}
    # ② 终拼：片头卡 + 内容(已烧字幕) + 片尾卡
    final_lines = (["file '" + p.as_posix() + "'" for p in n_intro]
                   + ["file '" + body.as_posix() + "'"]
                   + ["file '" + p.as_posix() + "'" for p in n_outro])
    final_seq = STATE / "concat_final.txt"
    final_seq.write_text("\n".join(final_lines), encoding="utf-8")
    tail = probe_duration(n_outro[0]) if n_outro else 0.0
    fc = ("[0:v]setsar=1,fps=%d,setpts=N/FRAME_RATE/TB[v];"
          "[1:a]volume=1.25,adelay=%d|%d[a1];"
          "[2:a]volume=0.14,afade=t=in:st=0:d=0.8,afade=t=out:st=%.2f:d=1.5[a2];"
          "[a1][a2]amix=inputs=2:duration=first:dropout_transition=2[a]"
          % (FPS, int(lead * 1000), int(lead * 1000), max(0.0, lead + d - 1.5)))
    code, log = _run(["ffmpeg", "-y", "-fflags", "+genpts", "-f", "concat", "-safe", "0",
                      "-i", str(final_seq), "-i", str(vo), "-i", str(bgm), "-filter_complex", fc,
                      "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "medium",
                      "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                      "-t", "%.2f" % (lead + d + tail), str(out)], 1800)
    return {"ok": Path(out).is_file() and Path(out).stat().st_size > 10000 and code == 0,
            "文件": str(Path(out).relative_to(ROOT)),
            "字节": Path(out).stat().st_size if Path(out).is_file() else 0,
            "时长": round(probe_duration(out), 2) if Path(out).is_file() else 0.0,
            "内容段数": len(body_lines), "退出码": code, "分辨率": "%dx%d@%d" % (W, H, FPS),
            "日志尾部": log.decode("utf-8", "replace")[-200:] if code else ""}


def plan(text: str, clips: list, out_name: str = "短片.mp4", title: str = "我是小土豆",
         sub: str = "本地数字人 · 一条链出片") -> dict:
    """一条链跑完：旁白→配乐→字幕→片头尾→成片（这就是"她会用"的入口）。"""
    STATE.mkdir(parents=True, exist_ok=True)
    vo = narrate(text)
    if not vo["ok"]:
        return {"ok": False, "卡在": "旁白", "详情": vo}
    bg = music(vo["时长"] + 3.0)
    sc = cues(text, vo["时长"])
    intro = card("片头", title, sub, seconds=2.6)
    outro = card("片尾", "谢谢观看", "GBT 小土豆 V9", seconds=2.0)
    mv = compose(clips, STATE / "narration.mp3", STATE / "music.wav", STATE / "subs.srt",
                 ROOT / "render" / out_name, intro=STATE / "card_片头.mp4",
                 outro=STATE / "card_片尾.mp4")
    return {"ok": mv["ok"], "旁白": vo, "配乐": {k: bg[k] for k in ("文件", "字节", "时长")},
            "字幕": {"条数": sc.get("条数"), "文件": sc.get("文件")},
            "片头": intro, "片尾": outro, "成片": mv,
            "口径": "全本地：Blender 渲画面 · edge-tts 出声 · 算法出乐 · PIL 出卡 · ffmpeg 合成"}


__all__ = ["VOICE_TW", "W", "H", "FPS", "tools_ready", "narrate", "music", "cues",
           "card", "normalize", "compose", "plan", "probe_duration"]
