import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_lipsync.py —— 口型对齐机检（台湾腔女声 → 口型轨，覆盖全片/无长静默）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, subprocess, sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import voice_kit as VK, lipsync as LS   # noqa: E402

TEXT = sys.argv[1] if len(sys.argv) > 1 else "八秒之内，主角必须立住；她用自己的模型当脑，用自己的手把活干完。"
OUT = ROOT / "render" / "voice"
FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


def dur(f):
    p = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", str(f)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(json.loads(p.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return 0.0


print("== 口型对齐机检 ==")
OUT.mkdir(parents=True, exist_ok=True)
r = VK.say(TEXT, profile="生产")
wav = OUT / r["文件"]
d = dur(wav) if wav.is_file() else 0.0
check("① 台湾腔女声出音（生产档）", r["ok"] and "HsiaoChen" in str(r["音色"]), "%s · %.2fs · %s" % (r["音色"], d, wav.name))
tl = LS.timeline(TEXT, d)
axis = (tl or {}).get("轴") or []
check("② 口型轨生成（≥3 段）", len(axis) >= 3, "%d 段 · 字数 %s · 帧数 %s" % (len(axis), (tl or {}).get("字数"), (tl or {}).get("帧数")))
last_end = float(axis[-1].get("止") or 0) if axis else 0.0
check("③ 轨覆盖全片（末段止≈时长）", axis and abs(last_end - d) <= max(0.6, d * 0.15), "末段止 %.2fs vs 时长 %.2fs" % (last_end, d))
gaps = []
for i in range(1, len(axis)):
    g = float(axis[i].get("起") or 0) - float(axis[i - 1].get("止") or 0)
    if g > 0.5:
        gaps.append(round(g, 2))
check("④ 无长静默（>0.5s 的空档=0）", not gaps, "空档 %s" % (gaps or "无"))
kinds = sorted({a.get("口型") for a in axis})
check("⑤ 口型多样（≥3 类）", len(kinds) >= 3, "口型类 %s" % kinds)
track = OUT / "talk-last.viseme.json"
track.write_text(json.dumps({"文本": TEXT, "时长": d, "音色": r["音色"], "轨": tl}, ensure_ascii=False, indent=1), encoding="utf-8")
check("⑥ 口型轨落盘", track.is_file() and track.stat().st_size > 200, "%s %s B" % (track.name, track.stat().st_size if track.is_file() else 0))
print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 口型对齐通过（台湾腔女声 · 轨覆盖全片 · 无长静默 · 口型多样 · 落盘）")
