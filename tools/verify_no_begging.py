# tools/verify_no_begging.py —— 禁求助闸验收（框架内不许求用户帮忙）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import no_begging as NB   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 禁求助闸验收 ==")
r = NB.scan()
check("红（阻塞等人 input()）= 0", r["红数"] == 0, "红 %d" % r["红数"])
check("黄（求助文案）≤ 2（只剩规则自身）", r["黄数"] <= 2, "黄 %d" % r["黄数"])
check("唯一例外写死：主人自己的身份证/账户登入",
      any("身份" in p for p in NB.ALLOW), NB.ALLOW[0][:40])
check("规则自身/否定句/反例样本被放行（不误报）",
      NB._allowed('不需要主人动手') and NB._allowed('# 请你去点一下（反例）'), "否定句/注释放行")
check("落账", NB.status(3).get("最近") is not None, "state/no_begging_audit.jsonl")
try:
    from core import stop_policy as SP
    check("与停机闸口径一致（身份登入=授权类，非求助）",
          SP.judge("需要用户操作", detail="主人身份证账户登入")["放行"] is True, "身份登入放行")
except Exception as e:  # noqa: BLE001
    check("与停机闸口径一致", False, type(e).__name__)

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 禁求助闸通过（红 0 · 黄≤2 · 身份登入是唯一例外 · 规则不误报自己）")
