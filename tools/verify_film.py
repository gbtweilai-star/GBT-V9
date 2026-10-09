# tools/verify_film.py —— 影视生产线验收器（查 DAG/技能/成片规格，真读数）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import film_studio as F   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 影视生产线验收 ==")
fl = F.build_flow("示例。第二句。", "标题")
skills = [n["skill"] for n in fl["nodes"]]
check("DAG 六节点（含后期）", len(fl["nodes"]) == 6, skills)
check("技能全部可注册", [s.name for s in F._mk_skills()] == skills, [s.name for s in F._mk_skills()])
plan = F.shot_plan("第一句要给冲突。第二句给悬念。第三句给画面。拍不出来不要紧。")
check("分镜 ≥3 镜", plan["镜数"] >= 3, "%d 镜 · 机位 %s" % (plan["镜数"], [s["机位"] for s in plan["shots"]]))
sc = F.score("测试", 12.0)
check("分段配乐生成", sc["ok"], "%s · %s · %d 字节" % (sc["文件"], "/".join(sc["结构"]), sc["字节"]))

run = ROOT / "state" / "film_run.json"
if run.is_file():
    d = json.loads(run.read_text(encoding="utf-8"))
    film = d.get("成片") or {}
    src = ROOT / str(film.get("文件", ""))
    check("工作流跑通", bool(d.get("ok")), "trace=%s · %s 秒" % (d.get("trace"), d.get("总耗时秒")))
    check("成片存在", src.is_file(), "%s · %d KB" % (film.get("文件"), (film.get("字节") or 0) // 1024))
    if src.is_file():
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_name,width,height:format=duration", "-of", "json", str(src)],
                           capture_output=True)
        info = json.loads(r.stdout.decode("utf-8", "replace"))
        codes = sorted(s.get("codec_name") for s in info.get("streams", []))
        v = next((s for s in info.get("streams", []) if s.get("codec_name") == "h264"), {})
        check("成片流规格", "h264" in codes and "aac" in codes, codes)
        check("成片竖版 1080x1920", (v.get("width"), v.get("height")) == (1080, 1920),
              (v.get("width"), v.get("height")))
        check("成片时长 >5 秒", float(info["format"]["duration"]) > 5,
              "%.2f 秒" % float(info["format"]["duration"]))
    check("镜数 ≥3", (film.get("镜数") or 0) >= 3, film.get("镜数"))
else:
    check("工作流结果文件", False, "还没跑过（state/film_run.json 不存在）")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 影视生产线通过（DAG/技能/分镜/配乐/成片规格）")
