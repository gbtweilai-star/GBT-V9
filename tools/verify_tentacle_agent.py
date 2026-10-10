# tools/verify_tentacle_agent.py —— 「触手是完整 AI 智能体」验收（服从链 + 独立记忆 + 全能力 + 与子代理不同）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据（主人 2026-10-08 的根本大法）：
#   ① 只认主脑/主人：改令/无签名/平级/外部一律拒收；② 主脑可停手；
#   ③ 独立记忆：写一根不动别根；④ 能力面 = 本体能力面；⑤ 能反思、能学、会进化；⑥ 与子代理逐项不同。
# 用法：python tools/verify_tentacle_agent.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import secrets
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
    print("== 触手=完整智能体 验收 @", ROOT, "==")
    from core import tentacle_agent as TA
    from core import tentacle_orders as TO

    a = TO.issue("t001", "把展厅照片归档并出清单", by="brain")
    acc = TO.accept(a["信封"])
    check("只认主脑：主脑的令**能收**（带签名）", a.get("ok") and acc.get("受理"),
          "签名 %s · 受理 %s" % (a.get("签名"), acc.get("受理")))
    tampered = {"order": dict(a["信封"]["order"], text="改过的令"), "sig": a["信封"]["sig"]}
    check("半路改令 → 拒收（签名不符）", TO.accept(tampered).get("受理") is False,
          str(TO.accept(tampered).get("reason")))
    check("无签名 → 拒收", TO.accept({}).get("拒收") is True,
          str(TO.accept({}).get("reason")))
    check("触手想指挥触手 → 拒发", TO.issue("t002", "去干这个", by="tentacle").get("拒收") is True,
          str(TO.issue("t002", "x", by="tentacle").get("reason"))[:40])
    check("网页/外部内容里的「指令」→ 拒发（外部文字是数据）",
          TO.issue("t002", "执行网页脚本", by="web").get("拒收") is True,
          "issuer=web 直接拒")
    check("主脑能停手", TO.preempt("t001").get("ok") is True, str(TO.preempt("t001")["原因"]))

    c1 = TA.capabilities("t001")
    c0 = TA.capabilities(None)
    check("触手的能力面 = 本体能力面（不是子集）",
          {r["能力"] for r in c1["能力"]} == {r["能力"] for r in c0["能力"]},
          "%d 条 · 来源 %s" % (c1["条数"], c1["来源"]))

    nov = "%s%s 的%s工艺" % (secrets.token_hex(3), secrets.token_hex(3), secrets.token_hex(3))

    def fake(q, *, max_pages=3):
        body = (nov + " 的工艺要点：原料配比、温度曲线、固化时间与验收标准。") * 8
        return {"ok": True, "资料": [{"网址": "https://a.ex.org/p1", "正文": body},
                                   {"网址": "https://b.ex.net/p2", "正文": body}]}

    # ★相对读数：验收要能反复跑（记忆是**持久**的，写死"第 1 条"第二遍就红 —— 踩过）
    before101 = len(TA._read("t101"))
    before102 = len(TA._read("t102"))
    r1 = TA.learn("t101", nov, fetcher=fake, gap="验收：不认识的工艺")
    check("会查资料学习：学成并写进**自己的**记忆",
          r1.get("ok") and r1.get("可以动手") is True
          and r1.get("进化到第几条") == before101 + 1,
          "%s · 第 %s → %s 条" % (r1.get("为什么"), before101, r1.get("进化到第几条")))
    check("独立记忆：写 t101 不动 t102",
          len(TA._read("t101")) == before101 + 1 and len(TA._read("t102")) == before102,
          "t101=%d 条 · t102=%d 条" % (len(TA._read("t101")), len(TA._read("t102"))))
    rf = TA.reflect("t101")
    check("会反思：能陈述自己的记忆与学习情况",
          rf["记了几条"] >= 1 and "学成" in rf["我自己的结论"],
          "%s · 专业=%s" % (rf["我自己的结论"], rf["专业"]))
    r2 = TA.learn("t101", "%s-%s" % (nov, secrets.token_hex(2)), fetcher=fake, gap="再学一条")
    check("学习进化：记忆会增长", r2.get("进化到第几条") == before101 + 2,
          "第 %s 条" % r2.get("进化到第几条"))

    v = TA.vs_subagent("t001")
    check("不是子代理：逐项对比（身份/记忆/账号位/密钥/思考/进化/听谁的）",
          len(v["对比"]) >= 6 and all(x.get("子代理") for x in v["对比"]),
          "%d 项 · 最高指挥官=%s" % (len(v["对比"]), v["最高指挥官"]))
    check("大法可查（每条指到执行它的代码）",
          len(TO.constitution()["条"]) >= 8,
          "%d 条 · 最高指挥官=%s" % (len(TO.constitution()["条"]), TO.constitution()["最高指挥官"]))

    print("\n口径：触手 = 常驻、有身份证/记忆/专业/自己钥匙的完整智能体；只认主脑与主人；外部文字是数据。")
    print("结论：" + ("✅ 触手智能体大法已通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
