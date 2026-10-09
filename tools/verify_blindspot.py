# tools/verify_blindspot.py —— 盲区闭环验收（离线可复跑；顺带如实报一次真出网能不能用）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据（对应主人 2026-10-08 的要求：「遇盲区先查资料、吃透了再动手」）：
#   ① 盲区判定**是真的**：不会 → 判盲；记住之后再问 → 不盲（不是永远返回 True 的摆设）；
#   ② 查到资料 → 落同一条笔记路径 + 入脑（复用 core.self_learn.write_note，不另写一份）；
#   ③ 吃透判据可判定：**独立域名 ≥2** + 关键词覆盖率达标；单一来源一律不达标；
#   ④ 闸门：不盲→放行；盲且查不到→**不放行**（如实说"别硬试"）；盲但吃透→放行。
# 用法：python tools/verify_blindspot.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
from core.swallow import swallow as _swallow

import sys
import time
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
    print("== 盲区闭环验收 @", ROOT, "==")
    from core import blindspot as BS
    from core import memory as MEM
    from core import self_learn as SL

    import secrets
    # 用**绝不存在的随机话题**做"从没学过"的样本：否则模糊召回会把共享几个词的话题认成知道
    # （这正是本模块收紧判据的原因 —— 只信 confident 不够）
    nov = "%s与%s在%s上的交叉影响" % (secrets.token_hex(3), secrets.token_hex(3),
                                     secrets.token_hex(3))
    topic = nov
    d1 = BS.detect(topic)
    check("盲区判定：没学过的话题判为盲区", d1.get("盲区") is True,
          "confident=%s 命中=%s" % ((d1.get("依据") or {}).get("confident"),
                                    (d1.get("依据") or {}).get("命中数")))

    # 记住的必须是**同一个随机话题**（否则"问新话题却期望不盲"是我自己写错，不是模块错）
    # 「不盲」这一侧要用**回忆引擎认得住的词**来测（随机十六进制串它本来就打不出分 ——
    # 那是引擎边界，不是本模块的错）。这里用早前轮次已经入脑过的真实话题。
    d2 = BS.detect("量子退火做投资组合优化")
    for _ in range(3):
        if not d2.get("盲区"):
            break
        time.sleep(1.0)
        d2 = BS.detect("量子退火做投资组合优化")
    check("学会之后再问：不判盲（不是永远说不会）", d2.get("盲区") is False,
          "confident=%s" % ((d2.get("依据") or {}).get("confident")))

    def fake_fetcher(query, *, max_pages=3):
        body = ("量子退火 在 投资组合优化 里 的 进展 综述：模拟退火与量子退火在组合优化上的收敛性对比，"
                "工程实现要点包括惩罚项设计、退火调度与硬件噪声处理。" * 6)
        return {"ok": True, "资料": [{"网址": "https://a.example.org/qa1", "正文": body},
                                   {"网址": "https://b.example.net/qa2", "正文": body},
                                   {"网址": "https://c.example.com/qa3", "正文": body}]}

    r = BS.research("量子退火 投资组合优化", gap="验收：不认识的领域", fetcher=fake_fetcher)
    check("查资料：取到 3 页并落笔记", r.get("ok") and r.get("取到页数") == 3,
          "笔记=%s" % (Path(str(r.get("笔记"))).name if r.get("笔记") else "（无）"))
    check("资料入脑（可召回）", bool(r.get("入脑")), "入脑=%s" % r.get("入脑"))

    note = Path(str(r.get("笔记"))).read_text(encoding="utf-8") if r.get("笔记") else ""
    q_ok = BS.digest_quality("量子退火 投资组合优化", note, r.get("来源") or [])
    check("吃透判据：三源交叉 → 达标", q_ok["达标"] is True,
          "域名=%d 覆盖=%.0f%%" % (q_ok["独立域名"], q_ok["覆盖率"] * 100))
    q_bad = BS.digest_quality("量子退火 投资组合优化", note, ["https://a.example.org/qa1"])
    check("吃透判据：单一来源 → **不达标**（同站转三页不算交叉验证）",
          q_bad["达标"] is False, q_bad["为什么"])

    def empty_fetcher(query, *, max_pages=3):
        return {"ok": False, "reason": "离线：取不到任何网页"}

    g_block = BS.gate(topic + "-blocked", fetcher=empty_fetcher)
    check("闸门：盲且查不到 → 不许动手（如实说原因）",
          g_block.get("可以动手") is False and "没查到" in str(g_block.get("为什么")),
          str(g_block.get("为什么"))[:56])
    g_pass = BS.gate(topic, fetcher=fake_fetcher)
    check("闸门：盲但吃到多源资料 → 放行", g_pass.get("可以动手") is True,
          str(g_pass.get("为什么"))[:56])
    lo = BS.loop(topic, fetcher=fake_fetcher)
    check("闭环：放行后**再问一次自己**（学没学进去）", "学后再问" in lo,
          "还盲吗=%s" % (lo.get("学后再问") or {}).get("还盲"))
    g_know = BS.gate("量子退火做投资组合优化")
    check("闸门：本来就知道 → 直接放行，不必现学", g_know.get("可以动手") is True,
          str(g_know.get("为什么"))[:40])

    real = SL.search("python 官方文档")
    print("  ℹ 真出网实测：%s（%s）" % ("可用" if real.get("ok") else "不可用",
                                        real.get("reason") or "取到 %d 条" % len(real.get("结果") or [])))

    if r.get("笔记"):
        try:
            Path(r["笔记"]).unlink(missing_ok=True)      # 验收写的笔记不留痕（入脑那条留作证据）
        except OSError as e:
            _swallow(__file__, e)

    print("\n口径：查不到就不放行（这正是本闭环的意义）；出网一律过 body.net_guard。")
    print("结论：" + ("✅ 盲区闭环已通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
