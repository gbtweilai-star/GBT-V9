# tools/produce_film.py —— 端到端出片（免费硬核工具链：ffmpeg/ffprobe + edge-tts + SRT）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令：三大原生工作流要打磨到「能生产一部电影」；我亲自跑通实战闭环并固化（防重启丢失）。
# 全链免费：ffmpeg/ffprobe 8.x + edge-tts（免费 TTS）+ SRT 字幕；不依赖任何付费云。
# 步骤（每步落账，可断点续跑）：
#   ① 选素材 → ② 旁白 TTS → ③ 生成 SRT → ④ **concat 出无配乐母版** → ⑤ 混旁白+烧字幕出成片 → ⑥ ffprobe 验收
import argparse, json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "render" / "film"
LED = ROOT / "state" / "film_runs.jsonl"


def sh(args, timeout=1800):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def log(rec):
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def probe(f):
    rc, out, _ = sh(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(f)])
    if rc != 0:
        return {}
    d = json.loads(out or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
    return {"宽": v.get("width"), "高": v.get("height"), "时长": round(float(d.get("format", {}).get("duration") or 0), 2),
            "有音轨": bool(a), "字幕轨": sum(1 for s in d.get("streams", []) if s.get("codec_type") == "subtitle"),
            "码率": d.get("format", {}).get("bit_rate"), "字节": d.get("format", {}).get("size")}


def pick_clips(limit):
    clips = sorted([p for p in (ROOT / "render" / "post").glob("*.mp4") if "调色-混音" not in p.name],
                   key=lambda p: p.stat().st_size, reverse=True)[:limit]
    if not clips:
        clips = sorted((ROOT / "render" / "post").glob("*.mp4"), key=lambda p: p.stat().st_size, reverse=True)[:limit]
    return clips


def make_narration(text, out_mp3):
    rc, o, e = sh(["python", "-m", "edge_tts", "--voice", "zh-TW-HsiaoChenNeural", "--text", text,
                   "--write-media", str(out_mp3)], timeout=300)
    return rc == 0 and out_mp3.is_file(), (o + e)[-200:]


def make_srt(text, seconds, out_srt):
    parts = [x.strip() for x in text.replace("。", "。|").replace("，", "，|").split("|") if x.strip()] or [text]
    per = max(1.0, seconds / max(1, len(parts)))
    lines = []
    t = 0.0
    for i, seg in enumerate(parts, 1):
        a, b = t, min(seconds, t + per)
        fmt = lambda s: "%02d:%02d:%02d,%03d" % (int(s // 3600), int(s % 3600 // 60), int(s % 60), int((s % 1) * 1000))
        lines += [str(i), "%s --> %s" % (fmt(a), fmt(b)), seg, ""]
        t = b
    out_srt.write_text(chr(10).join(lines), encoding="utf-8")
    return out_srt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="实战闭环-短片")
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--target-seconds", type=int, default=90)
    ap.add_argument("--aspect", default="vertical")
    ap.add_argument("--narration", default="GBT小土豆V9 数字人，已就位。她把架构拆成模块，逐条跑通闭环。开发者，自由的风。")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    steps = []
    clips = pick_clips(a.clips)
    steps.append({"步": "① 选素材", "素材": [c.name for c in clips]})
    if not clips:
        print("没有素材可拼（render/post 为空）"); return 2

    # ② 旁白
    mp3 = OUT / (a.name + "-旁白.mp3")
    ok, msg = make_narration(a.narration, mp3)
    steps.append({"步": "② 旁白 TTS", "ok": ok, "文件": mp3.name, "尾": msg[-80:]})

    # ③ SRT
    hard = OUT / (a.name + "-无配乐母版.mp4")
    srt = OUT / (a.name + "-字幕.srt")
    total_guess = sum(probe(c).get("时长") or 0 for c in clips)
    make_srt(a.narration, total_guess or 60.0, srt)
    steps.append({"步": "③ SRT 字幕", "文件": srt.name, "素材总时长": round(total_guess, 1)})

    # ④ concat → 无配乐母版（-c copy，快且无损）
    listfile = OUT / (a.name + "-concat.txt")
    listfile.write_text(chr(10).join("file '" + str(c).replace(chr(92), "/") + "'" for c in clips), encoding="utf-8")
    rc, o, e = sh(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(hard)])
    steps.append({"步": "④ 无配乐母版（母版另存·G5D）", "ok": rc == 0 and hard.is_file(), "文件": hard.name,
                  "读数": probe(hard)})

    # ⑤ 混旁白 + 烧字幕 → 成片
    final = OUT / (a.name + ".mp4")
    dur = probe(hard).get("时长") or 60.0
    # ★ 修：ffmpeg 滤镜里 Windows 路径要写成 C\:/a/b.srt（单冒号转义 + 正斜杠）
    _p = str(srt).replace(chr(92), '/').replace(':', chr(92) + ':')
    vf = "subtitles='" + _p + "'"
    cmd = ["ffmpeg", "-y", "-i", str(hard)]
    if ok:
        cmd += ["-i", str(mp3)]
    cmd += ["-vf", vf, "-t", str(dur)]
    if ok:
        cmd += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "160k", str(final)]
    rc, o, e = sh(cmd, timeout=3600)
    steps.append({"步": "⑤ 成片（混旁白+烧字幕）", "ok": rc == 0 and final.is_file(), "文件": final.name,
                  "ffmpeg尾": (e or "")[-300:]})

    # ⑥ ffprobe 验收（六条硬指标）
    r = probe(final) if final.is_file() else {}
    checks = {
        "① 是 MP4": final.suffix == ".mp4" and final.is_file(),
        "② 竖屏 1080x1920 或横屏 1920x1080": ((r.get("宽") == 1080 and r.get("高") == 1920) or (r.get("宽") == 1920 and r.get("高") == 1080)),
        "③ 单集时长落在 60-180 秒（样板口径）": 60 <= (r.get("时长") or 0) <= 180,
        "④ 有音轨": bool(r.get("有音轨")),
        "⑤ 有字幕文件": srt.is_file(),
        "⑥ 无配乐母版另存": hard.is_file(),
    }
    ok_all = all(checks.values())
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "片名": a.name, "步骤": steps, "成片读数": r,
           "六条硬指标": checks, "合格": ok_all, "秒": round(time.time() - t0, 1)}
    log(rec)
    (OUT / (a.name + ".report.json")).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=== 出片实战闭环 ===")
    for s in steps:
        print("  " + s["步"] + " → " + json.dumps({k: v for k, v in s.items() if k != "步"}, ensure_ascii=False)[:150])
    print("=== ffprobe 六条硬指标 ===")
    for k, v in checks.items():
        print(("  ✅ " if v else "  ❌ ") + k)
    print("  成片读数:", json.dumps(r, ensure_ascii=False))
    print("  用时: %.1fs  合格: %s" % (rec["秒"], ok_all))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
