# tools/verify_detail_habit.py —— 「细节化」习惯验收器
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import detail_habit as DH   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 「细节化」习惯验收 ==")
h = DH.habit()
check("六件套齐", len(h["六件套"]) == 6, h["六件套"])
check("模糊词表非空", len(h["模糊词"]) >= 10, "%d 个" % len(h["模糊词"]))
r1 = DH.check_text("应该可以了，差不多就发布吧")
check("模糊词闸能拦", (not r1["通过"]) and len(r1["命中"]) >= 2, r1["命中"])
r2 = DH.check_text("亮度 38.7→113.2，响度 -22.5→-14.1 LUFS")
check("带数值的话放行", r2["通过"], "数值 %d 个" % r2["数值个数"])
bad = DH.check_step({"步骤": "出片", "目标": "出一支片", "动作": "跑了 studio.plan"})
check("缺件能被点名", (not bad["通过"]) and set(bad["缺六件套"]) >= {"输入", "产出", "判据", "证据"},
      "缺六件套 %s / 缺回读 %s" % (bad["缺六件套"], bad["缺回读"]))
good = DH.check_step({"步骤": "出片", "目标": "剧本→多镜头成片", "输入": "剧本+动作片",
                      "动作": "film_studio.run_pipeline", "产出": ["render/多镜头成片.mp4"],
                      "判据": "h264+aac·1080x1920·镜数≥3", "证据": "trace=b61c1bf293e9",
                      "命令": "python tools/run_film_pipeline.py", "退出码": "0",
                      "读数": "4 镜 · 174.1 秒"})
check("完整步放行", good["通过"], "产出 %d 件" % good["粒度超限"] if False else "通过")
many = DH.check_step({"步骤": "一把梭", "目标": "啥都干", "输入": "x", "动作": "y",
                      "产出": ["a", "b", "c", "d"], "判据": "z", "证据": "e",
                      "命令": "c", "退出码": "0", "读数": "r"})
check("产出超限能拦（粒度规矩）", not many["通过"], "产出 %d 件 > 上限 %d" % (many["产出件数"], DH.MAX_ARTIFACTS))
a = DH.audit()
check("台账有真作业记录", (a["条数"] or 0) >= 1, "条数 %s · 达标率 %s" % (a["条数"], a["达标率"]))
if a["条数"]:
    check("真实作业达标率 = 1.0", a["达标率"] == 1.0, a["达标率"])
else:
    check("真实作业达标率 = 1.0", False, "台账为空（先跑一次影视线）")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 「细节化」习惯通过（六件套 · 模糊词闸 · 回读闭环 · 粒度规矩 · 真作业达标率）")
