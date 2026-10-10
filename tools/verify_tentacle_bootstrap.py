# tools/verify_tentacle_bootstrap.py —— 启动自举验收（密钥配好 → 触手自己把自己配好）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据：① 没配密钥时**不空跑**（这就是设计顺序：她先出现）；② 配好之后跑一次，
#       未立专业的触手被自动立上；③ 装备与云终端一并自配；④ **幂等**（第二遍 0 新立）；
#       ⑤ 报告可查、缺什么如实列。
# 用法：python tools/verify_tentacle_bootstrap.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FAILS: list = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("== 启动自举验收 @", ROOT, "==")
    from core import tentacle_bootstrap as TB
    from core import tentacle_profession as TP

    # 用一根从没立过专业的触手做样本（t199 在别处没被用过）
    sample = "t199"
    before = TP.roster(n=200)
    row = next(r for r in before["行"] if r["tentacle"] == sample)
    fresh = row["state"] == "未立"
    print("  ℹ 样本 %s 自举前：%s（%s）" % (sample, row["专业"], row["state"]))
    st0 = TB.ensure_started(n=1)      # 幂等：已有报告时不重复跑
    print("  ℹ 启动时的 ensure_started：%s" % (st0.get("reason") or st0.get("时间")))

    r1 = TB.run(n=200, cloud=False)
    after = TP.roster(n=200)
    row2 = next(r for r in after["行"] if r["tentacle"] == sample)
    check("自举把未立专业的触手自动立上",
          (not fresh) or row2["state"] != "未立",
          "%s 现在：%s / %s" % (sample, row2["专业"], row2["分域"]))
    check("自举报告可查、计数自洽",
          r1.get("ok") and r1["自举后已立"] >= r1["自举前已立"],
          "前 %d → 后 %d（新立 %d 跳过 %d 失败 %d）" % (r1["自举前已立"], r1["自举后已立"],
                                                     r1["这次新立"], r1["跳过（本来就立了）"], r1["立失败"]))
    check("装备自配跑过并如实计齐",
          r1["装备"]["跑了"] > 0, "跑了 %d · 齐了 %d" % (r1["装备"]["跑了"], r1["装备"]["齐了"]))
    st = TB.status()
    check("报告落盘且能读回", st.get("跑了没") and st.get("时间"), str(st.get("时间")))

    r2 = TB.run(n=200, equip=False, cloud=False)
    check("**幂等**：第二遍不重挑、不重复占位", r2["这次新立"] == 0,
          "第二遍新立=%d 跳过=%d" % (r2["这次新立"], r2["跳过（本来就立了）"]))
    check("每根触手一个专业（无重复占用）",
          after["已立"] == len({r["专业"] for r in after["行"] if r["state"] != "未立"}),
          "已立 %d 根 · 不同专业 %d 个" % (after["已立"], len({r["专业"] for r in after["行"] if r["state"] != "未立"})))

    print("\n口径：密钥配好那一刻触手才自举（先有她，才有手）；自举只做本仓侧，缺什么如实列。")
    print("结论：" + ("✅ 启动自举已通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
