# tools/produce_episode.py —— 出单集（对齐短剧样板 + 配乐合格线）：单集时长/竖屏/stems 三轨/响度 -14 LUFS
# dev: 自由的风 · 本署名不可删除、勿篡改归属
# 全程免费工具链：ffmpeg/ffprobe + edge-tts；不依赖付费云
import argparse, json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "render" / "film"
LED = ROOT / "state" / "episode_runs.jsonl"


def sh(args, timeout=3600):
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def log(rec):
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def probe(f):
    rc, out, _ = sh(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(f)])
    d = json.loads(out or "{}")
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    a = sum(1 for s in d.get("streams", []) if s.get("codec_type") == "audio")
    return {"宽": v.get("width"), "高": v.get("height"),
            "时长": round(float(d.get("format", {}).get("duration") or 0), 2), "音轨数": a}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="样板对齐-S01E01")
    ap.add_argument("--target-seconds", type=int, default=90)
    ap.add_argument("--clips", type=int, default=3)
    ap.add_argument("--narration", default="八秒之内，主角必须立住。她把架构拆成模块，逐条跑通闭环，再把证据交到你手上。开发者，自由的风。")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    steps = []
    t0 = time.time()

    clips = sorted((ROOT / "render" / "post").glob("*-调色.mp4"), key=lambda p: p.stat().st_size, reverse=True)[: a.clips]
    if not clips:
        print("没有素材")
        return 2
    steps.append({"步": "① 选素材", "素材": [c.name for c in clips]})

    # ② 旁白
    mp3 = OUT / (a.name + "-旁白.mp3")
    rc, o, e = sh(["python", "-m", "edge_tts", "--voice", "zh-TW-HsiaoChenNeural", "--text", a.narration,
                   "--write-media", str(mp3)], timeout=300)
    steps.append({"步": "② 旁白(edge-tts)", "ok": mp3.is_file()})

    # ④ 母版：素材循环到目标时长（不截片）
    hard = OUT / (a.name + "-无配乐母版.mp4")
    listfile = OUT / (a.name + "-concat.txt")
    listfile.write_text(chr(10).join("file '" + str(c).replace(chr(92), "/") + "'" for c in clips), encoding="utf-8")
    loop_times = max(1, int(a.target_seconds / max(1.0, sum(probe(c)["时长"] for c in clips))) + 1)
    rc, o, e = sh(["ffmpeg", "-y", "-stream_loop", str(loop_times), "-f", "concat", "-safe", "0", "-i", str(listfile),
                   "-t", str(a.target_seconds), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-an", str(hard)])
    steps.append({"步": "④ 无配乐母版(循环到目标时长)", "ok": hard.is_file(), "读数": probe(hard) if hard.is_file() else {}})

    # ③ 字幕（按成片时长铺满）
    srt = OUT / (a.name + "-字幕.srt")
    dur = probe(hard)["时长"] if hard.is_file() else float(a.target_seconds)
    segs = [x.strip() for x in a.narration.replace("。", "。|").split("|") if x.strip()] or [a.narration]
    per = max(1.0, dur / max(1, len(segs)))
    lines, t = [], 0.0
    fmt = lambda s: "%02d:%02d:%02d,%03d" % (int(s // 3600), int(s % 3600 // 60), int(s % 60), int((s % 1) * 1000))
    for i, seg in enumerate(segs, 1):
        lines += [str(i), "%s --> %s" % (fmt(t), fmt(min(dur, t + per))), seg, ""]
        t += per
    srt.write_text(chr(10).join(lines), encoding="utf-8")
    steps.append({"步": "③ SRT", "时长": dur})

    # ⑤ stems 三轨
    sd = OUT / (a.name + "-stems")
    sd.mkdir(parents=True, exist_ok=True)
    dx, mx, fx = sd / "dx.m4a", sd / "mx.m4a", sd / "fx.m4a"
    sh(["ffmpeg", "-y", "-i", str(mp3), "-af", "apad", "-t", str(dur), "-c:a", "aac", "-b:a", "160k", str(dx)])
    mxwav = OUT / (a.name + "-配乐床.wav")
    sh(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=196:duration=" + str(dur + 1),
        "-f", "lavfi", "-i", "sine=frequency=294:duration=" + str(dur + 1), "-f", "lavfi", "-i", "sine=frequency=392:duration=" + str(dur + 1),
        "-filter_complex", "amix=inputs=3,volume=0.18,afade=t=in:d=1.5,afade=t=out:st=" + str(max(0.0, dur - 2)) + ":d=2",
        "-c:a", "pcm_s16le", "-t", str(dur), str(mxwav)])
    sh(["ffmpeg", "-y", "-i", str(mxwav), "-c:a", "aac", "-b:a", "160k", str(mx)])
    sh(["ffmpeg", "-y", "-i", str(hard), "-vn", "-f", "lavfi", "-i", "anoisesrc=d=" + str(dur) + ":a=0.02",
        "-shortest", "-c:a", "aac", "-b:a", "128k", str(fx)])
    steps.append({"步": "⑤ stems 三轨", "dx": dx.name, "mx": mx.name, "fx": fx.name, "mx口径": "程序生成配乐床（诚实标注，非人工作曲）"})

    # ⑥ 混流 + 烧字幕 + 响度归一到 -14 LUFS + 不截片
    final = OUT / (a.name + ".mp4")
    vf = "subtitles='" + str(srt).replace(chr(92), "/").replace(":", chr(92) + ":") + "'"
    cmd = ["ffmpeg", "-y", "-i", str(hard), "-i", str(dx), "-i", str(mx), "-i", str(fx),
           "-filter_complex", "[1:a][2:a][3:a]amix=inputs=3:duration=longest:normalize=0[mix];[mix]loudnorm=I=-14:TP=-1:LRA=11[outa]",
           "-map", "0:v:0", "-map", "[outa]", "-vf", vf, "-t", str(dur),
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-b:a", "192k", str(final)]
    rc, o, e = sh(cmd)
    steps.append({"步": "⑥ 混流+响度-14+烧字幕", "ok": final.is_file(), "ffmpeg尾": (e or "")[-160:]})

    # ⑦ 验收
    r = probe(final) if final.is_file() else {}
    checks = {
        "① MP4": final.is_file(),
        "② 竖屏 1080x1920": (r.get("宽") == 1080 and r.get("高") == 1920),
        "③ 单集 60-180 秒": 60 <= (r.get("时长") or 0) <= 180,
        "④ 有音轨": (r.get("音轨数") or 0) >= 1,
        "⑤ 有字幕": srt.is_file(),
        "⑥ 母版另存": hard.is_file(),
        "⑦ stems 三轨": dx.is_file() and mx.is_file() and fx.is_file(),
    }
    ok_all = all(checks.values())
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "片名": a.name, "步骤": steps, "成片读数": r,
           "判据": checks, "合格": ok_all, "秒": round(time.time() - t0, 1)}
    log(rec)
    (OUT / (a.name + ".report.json")).write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("=== 出单集 ===")
    for s in steps:
        print("  " + s["步"] + " " + json.dumps({k: v for k, v in s.items() if k != "步"}, ensure_ascii=False)[:130])
    print("=== 判据 ===")
    for k, v in checks.items():
        print(("  ✅ " if v else "  ❌ ") + k)
    print("  成片:", json.dumps(r, ensure_ascii=False), "| 用时 %.1fs | 合格=%s" % (rec["秒"], ok_all))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
