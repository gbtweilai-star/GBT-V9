import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# tools/verify_stop_policy.py —— 停机闸验收（只允许两种理由停）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core import stop_policy as SP   # noqa: E402

FAIL = []


def check(name, ok, reading):
    print(("  ✅ " if ok else "  ❌ ") + name + " —— " + str(reading))
    if not ok:
        FAIL.append(name)


print("== 停机闸验收 ==")
check("非法理由一律禁止停下", all(not SP.judge(k)["放行"] for k in ("轮次结束", "先汇报一下", "等你有空", "歇一会")),
      "轮次结束/先汇报/等你有空/歇一会 → 全禁")
check("部署完成但无证据 ⇒ 不算", SP.judge("部署完成")["放行"] is False, "无证据")
check("部署完成带证据 ⇒ 放行", SP.judge("部署完成", evidence="交付闸 23/23 · 提交 dcfdebb1")["放行"] is True,
      "有证据")
check("非身份/非六类的『需要用户操作』⇒ 禁止",
      SP.judge("需要用户操作", detail="云端 API key 没配")["放行"] is False, "云端 key")
check("身份证账户登入 ⇒ 放行", SP.judge("需要用户操作", detail="主人身份证账户登入")["放行"] is True, "身份登入")
check("六类危险授权 ⇒ 放行",
      all(SP.judge("需要用户操作", detail=d + "前要授权")["放行"] for d in ("转账", "支付", "删除")),
      "转账/支付/删除")
r = SP.judge("轮次结束")
check("禁止停下时必须给出下一件", bool(r.get("下一件")), (r.get("下一件") or {}).get("事"))
m = SP.must_continue()
check("在办非空 ⇒ 不许停（读数可查）", m["能停"] == (m["在办数"] == 0),
      "能停 %s · 在办 %s" % (m["能停"], m["在办数"]))
check("落账", bool(SP.status(3).get("最近判定") or SP.status(3).get("最近")), "state/stop_policy.jsonl")

print()
if FAIL:
    print("结论：❌ 未通过 %d 项 —— %s" % (len(FAIL), "、".join(FAIL)))
    raise SystemExit(1)
print("结论：✅ 停机闸通过（只有 部署完成(带证据) / 真需用户操作(身份或六类) 放行；其余禁止并给下一件）")
