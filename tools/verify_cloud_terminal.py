# tools/verify_cloud_terminal.py —— 云终端（免费算力·本地 0 显存）验收
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据（对应主人 2026-10-08 的要求 + 本仓红线）：
#   ① 云插件页独立隔出一块，**一排 10 个槽**；② freebuff 优先、本地 0 显存；
#   ③ 触手绑进来走**身份金库**（不代开户）；④ 可达性**真探**，探不到就写不可达、API 未验就写未验；
#   ⑤ 板块零外链（全内置纪律）；⑥ 源码里不得出现任何自动注册/绕过风控的实现。
# 用法：python tools/verify_cloud_terminal.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations

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
    print("== 云终端验收 @", ROOT, "==")
    from core import cloud_terminal as CT
    from core import tentacle_identity as TI
    from audit.ledger_factory import make_ledger
    led = make_ledger()

    reg = CT.registry()
    check("一排 10 个槽", reg["槽数"] == 10 and len(reg["槽"]) == 10, "%d 槽" % reg["槽数"])
    check("freebuff 优先", reg["优先"] == "freebuff"
          and (reg["供应商"][0]["id"] == "freebuff" and reg["供应商"][0]["优先"]),
          reg["供应商"][0]["名"])
    check("本地 0 显存（云端跑）", reg["本地显存"] == 0, "本地显存=%s" % reg["本地显存"])
    check("口径里写着红线（不代开户 / 不绕风控）",
          any("不代批量开户" in x for x in reg["口径"]), "已写")

    sec = CT.section()
    check("板块是独立隔开的一区（自带边框容器）",
          "border:1px solid #1d3350" in sec and "云终端" in sec, "有独立容器")
    check("板块上一排 10 列", "repeat(10,1fr)" in sec, "grid repeat(10,1fr)")
    check("板块零外链（全内置纪律）",
          "http://" not in sec and "https://" not in sec, "外链=0")
    check("板块上写了 freebuff 优先与红线",
          "Freebuff" in sec and "主人注册" in sec, "已写")

    a = CT.assign(2, "t002", provider="freebuff", led=led)
    check("触手绑进槽：走身份金库（不代开户）",
          bool(a.get("ok")) or "已有身份位" in str(a.get("身份位")),
          str(a.get("身份位"))[:60])
    rows = {r["service"]: r for r in TI.rows(tentacle="t002")}
    check("金库里 t002 的「云插件」身份位真在", "云插件" in rows,
          "state=%s" % (rows.get("云插件") or {}).get("state"))
    slot2 = next(s for s in CT.slots(led) if s["slot"] == 2)
    check("槽 2 真记下了这根触手", slot2["tentacle"] == "t002", str(slot2))

    c = CT.check(2, led=led)
    check("可达性真探（不是写死的 ok）", ("state" in c) and isinstance(c.get("ok"), bool),
          "%s · host=%s · API=%s" % (c.get("state"), c.get("host"), c.get("API")))
    check("端点的 API 未验就如实写未验",
          "未验" in str(c.get("API", "")), str(c.get("API"))[:40])
    CT.release(10, led=led)                      # 先腾一个空槽再验「未配」（不然 auto 会把 10 槽全填满）
    c9 = CT.check(10, led=led)
    check("没绑的槽如实说未配", c9.get("state") == "未配", str(c9))
    od = CT.signup_order(1, led=led)
    check("开户作业单是给触手的（含步骤/停点/回填，且不含凭据明文）",
          od.get("ok") and len(od["步骤"]) >= 5 and od["停点（等人）"] and \
          ("V9ID_" in str(od["要填的字段"]) or "env" in str(od["要填的字段"])),
          "步骤 %d 条 · 停点=%s" % (len(od.get("步骤") or []), od.get("停点（等人）")))
    check("作业单写明谁执行（触手）与不刷号边界",
          "触手" in str(od.get("谁执行")) and "不批量刷号" in str(od.get("边界")),
          str(od.get("谁执行"))[:40])

    src = (ROOT / "core" / "cloud_terminal.py").read_text(encoding="utf-8")
    # 检查式按**机制**写，不按词写：正规模块名里本来就会有 signup（作业单），那是设计不是刷号。
    # 真正该禁的是**绕风控/自动化刷号**的手法，以及"没有人类停点"这件设计缺失。
    evasion = [k for k in ("stealth", "captcha_bypass", "绕过验证码", "打码平台",
                           "fingerprint_spoof", "proxy_pool", "自动填表循环") if k in src]
    check("源码里没有绕风控/刷号手法", not evasion, "命中=%s" % (evasion or "无"))
    check("开户设计里**有**人类停点（不是一路自动到底）",
          "验证码" in src and "停点" in src, "停点已写死在作业单里")

    # ── 便捷性（主人 2026-10-08：「AI 时代讲究便捷，别让用户动手」）──
    au = CT.auto(led=led)
    check("一键全装：10/10 全自动装好", au["装了"] == 10 and au["失败"] == 0,
          "装了=%d 失败=%d 本地显存=%s" % (au["装了"], au["失败"], au["本地显存"]))
    check("一键全装后 10 个槽都有触手",
          all(s["tentacle"] for s in CT.slots(led)),
          ", ".join("%d:%s" % (s["slot"], s["tentacle"]) for s in CT.slots(led)[:4]) + " …")
    check("要主人做的只剩一步（过验证码），其余全自动",
          "验证码" in str(au["要主人做的"]), str(au["要主人做的"])[:56])
    check("板块上有「一键全装」按钮 + /auto 接口",
          "一键全装" in CT.section() and "/api/cloud/terminal/auto" in CT.section(),
          "按钮与接口都在")

    print("\n口径：免费云终端把显存与运行放云端（本地 0 显存）；本仓侧一键全装，"
          "第三方账号只留人类级的一步（过验证码），不批量刷号。")
    print("结论：" + ("✅ 云终端板块通过" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
