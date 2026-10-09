# tools/verify_no_ask.py —— 「不开口闸」验收器
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import ux_doctrine as UX   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 「不开口闸」验收（严禁要求用户做事 · 触手默认人类操作）==")
r = UX.no_ask_rule()
check("规则在", bool(r["规则"]), r["规则"][:40])
check("触手默认表 ≥5 条", len(r["触手默认"]) >= 5, "%d 条" % len(r["触手默认"]))

bad = ["请你去 Tripo 点一下导出", "麻烦你把 GLB 拖进下载夹", "你自己先配置一下密钥",
       "请提供你的 API key", "你需要手动上传一下"]
ok_all = True
for t in bad:
    x = UX.check_ask(t)
    if x["通过"]:
        ok_all = False
        print("       ❌ 漏拦:", t)
check("五句「要用户做事」全拦", ok_all, "拦下 %d/%d" % (sum(1 for t in bad if not UX.check_ask(t)["通过"]), len(bad)))

good = ["请先登录你的账号并授权设备码", "扫码登录后我自动接管",
        "我已导出并收进仓：成片 21.77 秒，读数见台账"]
ok2 = all(UX.check_ask(t)["通过"] for t in good)
check("身份登录例外放行 + 已有读数的话放行", ok2, "3/3")

# 触手默认：不许把"本来要人点"的动作写成要人做
td = {x["场景"] for x in r["触手默认"]}
need = {"网页导出/下载", "网页上传/发布", "软件首次配置/启用插件"}
check("关键场景已默认触手", need <= td, sorted(need & td))
check("发布类只保留一次授权", any("授权" in x["要人做什么"] for x in r["触手默认"]),
      [x["要人做什么"] for x in r["触手默认"]])

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 不开口闸通过（严禁要用户做事 · 身份登录例外 · 触手默认人类操作）")
