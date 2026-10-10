import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_studio.py —— 创作链验收器（跑一条微缩端到端，出真读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import studio as SD   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)
    return ok


print("== 创作链验收（AI 短视频 / 音乐 / 字幕 / 成片）==")
t = SD.tools_ready()
check("工具 ffmpeg", t["ffmpeg"], t)
check("工具 edge-tts", t["edge_tts"], t["edge_tts"])
check("工具 Pillow", t["Pillow"], t["Pillow"])

TEXT = "你好，我是小土豆。这条链是本地跑的。"
vo = SD.narrate(TEXT, ROOT / "state/studio/verify_vo.mp3")
check("旁白生成", vo["ok"], "%s · %s 字节 · %.2f 秒" % (vo["文件"], vo["字节"], vo["时长"]))

bg = SD.music(max(3.0, vo["时长"] + 1), ROOT / "state/studio/verify_bgm.wav")
check("配乐生成", bg["ok"], "%s · %d 字节" % (bg["文件"], bg["字节"]))

cu = SD.cues(TEXT, vo["时长"], ROOT / "state/studio/verify.srt")
check("字幕时间轴", cu["ok"], "%d 条 · 首条 %s" % (cu["条数"], cu["行"][0]["文"] if cu.get("行") else "-"))

clips = [ROOT / "render/anim_v/idle.mp4", ROOT / "render/anim_v/talk.mp4"]
clips = [c for c in clips if c.is_file()]
if not check("有可用画面片", bool(clips), [c.name for c in clips]):
    print("（没画面片就无法验成片）")
else:
    out = ROOT / "state/studio/verify.mp4"
    mv = SD.compose(clips, ROOT / "state/studio/verify_vo.mp3", ROOT / "state/studio/verify_bgm.wav",
                    ROOT / "state/studio/verify.srt", out)
    check("成片合成", mv["ok"], "%s · %d KB · %.2f 秒 · 内容 %s 段 · %s"
          % (mv["文件"], mv["字节"] // 1024, mv["时长"],
             mv.get("内容段数", mv.get("画面段数", "-")), mv.get("分辨率", "-")))
    if out.is_file():
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height",
                            "-of", "json", str(out)], capture_output=True)
        st = json.loads(r.stdout.decode())["streams"]
        codes = sorted(s["codec_name"] for s in st)
        check("成品含 h264 视频流", "h264" in codes, codes)
        check("成品含 aac 音频流", "aac" in codes, codes)
        v = next((s for s in st if s["codec_name"] == "h264"), {})
        check("竖版画面 1080x1920", (v.get("width"), v.get("height")) == (1080, 1920),
              (v.get("width"), v.get("height")))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 创作链全通（旁白·配乐·字幕·成片），全本地零付费")
