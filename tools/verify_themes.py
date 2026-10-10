import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_themes.py —— 港式僵尸三主题验收器
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import film_themes as FT   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 港式僵尸 · 三主题验收 ==")
ts = FT.themes()
check("主题数 = 3", len(ts) == 3, [t["名"] for t in ts])
check("三主题调色各不相同", len({t["调色"] for t in ts}) == 3, [t["调色"] for t in ts])
check("灯光各不相同（主光色）", len({t["主光色"] for t in ts}) == 3, [t["主光色"] for t in ts])
check("节奏有快慢差异", len({t["每镜秒"] for t in ts}) >= 2, [t["每镜秒"] for t in ts])

h = FT.history(50)
# 按**主题取最新一行**（中途重跑会留多行；只要每主题最新那支达标即可）
_latest: dict = {}
for _r in h["行"]:
    _latest[_r.get("主题")] = _r
check("台账覆盖三主题", len(_latest) >= 3, "主题 %s（台账共 %s 行）" % (list(_latest), h["条数"]))
ok_all, rows = True, []
for r in _latest.values():
    f = ROOT / (r.get("成片") or "")
    if not f.is_file():
        ok_all = False
        print("      ❌ 缺成片:", r.get("主题"), r.get("成片"))
        continue
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_name,width,height:format=duration", "-of", "json", str(f)],
                       capture_output=True)
    info = json.loads(p.stdout.decode("utf-8", "replace"))
    codes = sorted(s.get("codec_name") for s in info.get("streams", []))
    v = next((s for s in info.get("streams", []) if s.get("codec_name") == "h264"), {})
    dur = float(info["format"]["duration"])
    loud = r.get("响度LUFS")
    red = r.get("质检红灯") or []
    good = ("h264" in codes and "aac" in codes and (v.get("width"), v.get("height")) == (1080, 1920)
            and dur > 5 and (loud is None or abs(float(loud) + 14) <= 1.5) and not red)
    ok_all = ok_all and good and bool(r.get("ok"))
    rows.append((r["主题"], good, "%s · %.2fs · %s · %s LUFS · 红灯%s · %s镜"
                 % (codes, dur, (v.get("width"), v.get("height")), loud, red, r.get("镜数"))))
for name, good, reading in rows:
    check("成片达标 " + name, good, reading)
check("三支全成功", ok_all and len(rows) == 3, "%d 支（按主题去重后）" % len(rows))

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 港式僵尸三主题通过（三套灯光/调色/节奏 · 三支成片规格与质检全达标）")
