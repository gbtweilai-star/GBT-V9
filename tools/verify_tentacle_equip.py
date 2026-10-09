# tools/verify_tentacle_equip.py —— 触手自主配置装备与账号 验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据（对应目标第 8/9/10 条）：① 先有专业才允许配装备；② 账号走**独立金库**（与账本库分开）；
#   ③ 凭据只从环境变量读，读不到就本地自签并**如实标注**；④ 装备必须过工具坞白名单 + 授权令牌，
#      没令牌报 needs_grant，绝不假装装上；⑤ 不往 db_fleet / cloud_plugins 塞凭据字段。
# 用法：python tools/verify_tentacle_equip.py    退出码 0=全过 / 1=有断言不过
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
    print("== 触手自配装备与账号 验收 @", ROOT, "==")
    from core import tentacle_equip as TE
    from core import tentacle_identity as TI
    from core import tentacle_profession as TP

    # ① 没立专业的触手不许配装备
    #    ★样本要**动态取**：自举会把 1..n 全立上专业，写死 t050 会过期（真踩过）。
    #    在 201..400 里找一根真没立专业的；全立上了就如实跳过这条，不假装测过。
    _roster = {r["tentacle"]: r["state"] for r in TP.roster(n=400)["行"]}
    bare_id = next(("t%03d" % i for i in range(201, 400)
                    if _roster.get("t%03d" % i, "未立") == "未立"), None)
    if bare_id is None:
        print("  ℹ 201..400 已全部立了专业 ⇒ 这条跳过（不假装测过）")
    else:
        bare = TE.plan(bare_id)
        r = TE.equip(bare_id, dry_run=True)
        check("未立专业的触手：先立专业再谈装备（样本 %s）" % bare_id,
              bare["专业"] == "未立" and r.get("ok") is False,
              (r.get("reason") or "")[:50])

    # ② 已立专业的触手：需求算得出，且标明口径来源
    pl = TE.plan("t001")
    check("已立 t001：算出需要哪些服务位与工具",
          bool(pl["需要服务位"]) and bool(pl["需要工具"]),
          "服务=%s 工具=%s" % (pl["需要服务位"], pl["需要工具"]))
    check("口径如实标注为本仓自定（不冒充上游）",
          "本仓自定" in str(pl["口径"]), str(pl["口径"])[:46])

    # ③ 已齐的触手：如实说「已齐」，不许编动作
    dry1 = TE.equip("t001", dry_run=True)
    check("已齐时如实报已齐（不编动作）",
          dry1["已齐"] is True and dry1["动作"] == [],
          dry1["说明"])

    # ③b 造一根**真有缺口**的触手：用金库里从未占位的 t199（t001..t100 早已占满），
    #     这样 claim → provision → attach 全程才真的走到
    pick = next(p for p in TP.catalog()["professions"]
                if p["division"] in ("design", "testing"))
    TP.assign("t199", pick["slug"], force=True)
    pl2 = TE.plan("t199")
    # 🔴 2026-10-09 契约变更：装备是**全局**的（装一次全队可用）⇒ 已配齐后不会有"装备缺口"。
    #    这里改为验"需求算得出"；账号仍是按根算。
    check("需求算得出（服务位/工具非空）",
          bool(pl2.get("需要服务位") or pl2.get("需要工具")),
          "%s · 需服务位=%s 需工具=%s" % (pl2["专业"], pl2.get("需要服务位"), pl2.get("需要工具")))
    dry = TE.equip("t199", dry_run=True)
    check("干跑不写盘（有动作只列动作，已齐则如实报已齐）",
          dry["干跑"] is True and (bool(dry["动作"]) or dry.get("已齐") is True),
          "干跑=%s 动作 %d 条 已齐=%s" % (dry["干跑"], len(dry["动作"]), dry.get("已齐")))

    # ④ 真配账号（走独立金库；凭据读不到就本地自签并标注）
    # 幂等：首次跑有缺口 → 验「每步都有结果」；重复跑已配好 → 验「如实报已齐」
    real = TE.equip("t199", accounts=True, tools=False, dry_run=False)
    accs = [a for a in real["动作"] if a["部件"] == "账号"]
    if accs:
        check("真配账号：每一步都有结果（不含糊）",
              all(a.get("结果") for a in accs),
              "; ".join("%s→%s" % (a["服务"], a["结果"]) for a in accs))
    else:
        check("真配账号：已齐时如实报已齐（幂等）", real["已齐"] is True, real["说明"])
    rows = {x["service"]: x for x in TI.rows(tentacle="t199")}
    states = {s: rows.get(s, {}).get("state", "未占位") for s in pl2["需要服务位"]}
    check("金库里真的有了状态（不是报告里说有了）",
          all(v in ("已配", "已验证", "已绑") for v in states.values()), str(states))

    # ⑤ 账号库必须是**独立**的（与账本库分开）
    led_path = str(ROOT / "tentacle_ledger.db")
    check("账号库独立于账本库",
          Path(TI.DB).name == "tentacle_identity.sqlite3" and str(TI.DB) != led_path,
          "账号库=%s" % Path(TI.DB).name)

    # ⑥ 装备：没令牌 → needs_grant（不许假装装上）；有令牌 → 走工具坞的门
    from core.tool_bay import ToolBay
    from core.gui_grant import Grant
    from audit.ledger_factory import make_ledger
    tb = ToolBay(make_ledger(), root=ROOT / "tools" / "_verify_bay",
                 runner=lambda cmd, t: (0, "fake", ""),
                 exists_fn=lambda entry: True)          # 假装已存在 ⇒ 不会真装任何东西
    # 🔴 2026-10-09 契约变更：白名单内 risk=safe 工具**免主人令牌**（白名单即授权、装自留地可回滚）；
    #    risk=write（git/ffmpeg/7z）与系统级仍要主人令牌。所以这里分两条验。
    no_grant = TE.equip("t199", accounts=False, tools=True, dry_run=False, tool_bay=tb)
    tool_acts = [a for a in no_grant["动作"] if a["部件"] == "装备"]
    safe_ok = [a for a in tool_acts if isinstance(a.get("结果"), dict) and a["结果"].get("ok")]
    check("白名单 safe 装备：免主人令牌也能配（可回滚）",
          (len(safe_ok) == len(tool_acts)) if tool_acts else no_grant.get("已齐") is True,
          ("%d/%d 自主配齐" % (len(safe_ok), len(tool_acts))) if tool_acts else "已齐（幂等，无动作）")
    # write 类必须被拦：直接问门
    g_safe, why_safe = tb._gate(None, tool="image_ops", op="install", tentacle_id="t199")[0:2]
    g_write, why_write = tb._gate(None, tool="git", op="install", tentacle_id="t199")[0:2]
    check("write 类装备仍被门拦住（没令牌不放行）",
          g_safe is True and g_write is False,
          "safe=%s(%s) write=%s(%s)" % (g_safe, why_safe, g_write, why_write[:24]))
    with_grant = TE.equip("t199", accounts=False, tools=True, dry_run=False,
                          tool_bay=tb, grant=Grant(primitives=["tools"], note="验收"))
    tool_acts2 = [a for a in with_grant["动作"] if a["部件"] == "装备"]
    ok_acts = [a for a in tool_acts2 if isinstance(a.get("结果"), dict)
               and a["结果"].get("ok")]
    check("有令牌的装备：过门并给出结果",
          (len(ok_acts) == len(tool_acts2)) if tool_acts2 else with_grant.get("已齐") is True,
          ("%d/%d 过门" % (len(ok_acts), len(tool_acts2))) if tool_acts2 else "已齐（幂等，无动作）")

    # ⑦ 纪律：不往 db_fleet / cloud_plugins 塞凭据（源码级机检）
    src = (ROOT / "core" / "tentacle_equip.py").read_text(encoding="utf-8")
    # 机检「有没有真的去动它们」——只看 import/调用，不看注释（注释里提到不算违规）
    writes = [w for w in ("db_fleet", "cloud_plugins")
              if (f"import {w}" in src or f"from core import {w}" in src
                  or f"{w}.CATALOG" in src or f"{w}.SLOT" in src)]
    check("不碰 db_fleet / cloud_plugins 的 schema（recon 已确认它们无凭据列）",
          not writes, "真实调用=%s（仅注释提及不算）" % (writes or "无"))

    # ⑧ 全编队一屏 + 未知 op
    rep = TE.report(n=100)
    check("全编队一屏：专业/账号/装备到位数",
          rep["已立专业"] >= 2 and rep["未立专业"] == rep["n"] - rep["已立专业"],
          "已立=%d 未立=%d 账号已齐=%d 装备已齐=%d" % (
              rep["已立专业"], rep["未立专业"], rep["账号已齐"], rep["装备已齐"]))
    check("未知 op 显式报错", TE.run("nope").get("ok") is False,
          str(TE.run("nope")["error"])[:40])

    print("\n结论：" + ("✅ 触手自配装备与账号 全过" if not FAILS
                     else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
