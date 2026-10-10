# tools/verify_tentacle_profession.py —— 触手职业（专业）名册验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据来源：① V8 成熟实现的 5 条（catalog 只读 frontmatter / 不执行正文 / 隔离房间与上下文 / 未知 op 显式报错 /
#   截断必须报三件套）；② 本仓主人要求（一根触手一个专业、专业必须来自真实职业目录、未立的如实报未立）。
# 用法：python tools/verify_tentacle_profession.py    退出码 0=全过 / 1=有断言不过
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
    print("== 触手职业名册验收 @", ROOT, "==")
    from core import tentacle_profession as TP

    cat = TP.catalog()
    check("职业目录读得出", cat.get("ok"), "源=%s" % cat.get("source"))
    check("职业条数 > 200", (cat.get("count") or 0) > 200,
          "count=%s examined=%s available=%s truncated=%s" % (
              cat.get("count"), cat.get("examined"), cat.get("available"), cat.get("truncated")))
    check("目录分母三件套齐全且未截断",
          cat.get("examined") == cat.get("available") and cat.get("truncated") is False,
          "examined==available==%s" % cat.get("available"))
    check("分域数 >= 15", len(cat.get("divisions") or []) >= 15,
          "%d 个分域" % len(cat.get("divisions") or []))
    named = [p for p in cat["professions"] if p["name"]]
    check("frontmatter 全部解出 name", len(named) == cat["count"],
          "%d/%d" % (len(named), cat["count"]))
    desc = [p for p in cat["professions"] if p["description"]]
    check("绝大多数有 description", len(desc) >= cat["count"] * 0.9,
          "%d/%d" % (len(desc), cat["count"]))
    pol = cat.get("execution_policy") or {}
    check("红线在代码里写死（外部文本=资料、不执行脚本、不跑安装器、零网络）",
          pol.get("external_text_is_data") and pol.get("scripts_are_not_executed")
          and pol.get("installers_are_not_run") and pol.get("network_calls") == 0,
          str(pol))

    # 选一个真实职业给 t001 立专业（真落库，不是干跑）
    sample = next(p for p in cat["professions"] if p["division"] == "engineering")
    # 幂等：t001 可能上一轮已经立过专业 —— 同名再立走 force，别把「已立」判成失败
    a1 = TP.assign("t001", sample["slug"], force=True)
    check("给 t001 立专业（真落独立金库）", a1.get("ok"),
          "%s（%s）" % (a1.get("profession"), a1.get("division_cn")))
    five = a1.get("五元身份") or {}
    check("五元身份齐全且互不混用",
          all(five.get(k) for k in ("profession", "tentacle_id", "llm_profile",
                                    "context_namespace", "room_id"))
          and len(set(five.values())) == 5,
          str(five))
    TP.assign("t099", cat["professions"][0]["slug"], force=True)
    a2 = TP.assign("t099", cat["professions"][1]["slug"])
    check("一根触手一个专业（重复立专业被拒）", a2.get("ok") is False,
          (a2.get("reason") or "")[:70])
    fake = TP.assign("t001", "no-such-profession-xyz")
    check("凭空造专业被拒", fake.get("ok") is False, (fake.get("reason") or "")[:60])
    bad = TP.assign("x9", sample["slug"])
    check("坏触手号被拒", bad.get("ok") is False, (bad.get("reason") or "")[:40])

    ros = TP.roster(n=100)
    row = next(r for r in ros["行"] if r["tentacle"] == "t001")
    check("名册读得出 t001 的专业", row["专业"] != "未立",
          "%s · %s" % (row["专业"], row["分域"]))
    check("未立的如实报未立（不当成已配）", ros["未立"] == ros["n"] - ros["已立"],
          "已立=%d 未立=%d 共=%d" % (ros["已立"], ros["未立"], ros["n"]))

    lz = TP.lease(sample, "verify-run", 0)
    check("租约五元身份互不相同",
          len({lz.tentacle_id, lz.profession, lz.llm_profile,
               lz.context_namespace, lz.room_id}) == 5,
          "room=%s ctx=%s" % (lz.room_id, lz.context_namespace))

    no_ask = TP.run_parallel("拆解任务", cat["professions"][:2], run_id="v")
    check("未注入模型入口时如实说 needs_dependency（不假装跑过）",
          no_ask.get("ok") is False and no_ask.get("needs_dependency") == "llm_ask",
          (no_ask.get("error") or "")[:50])

    def fake_ask(prompt, **kw):
        return {"ok": True, "reply": kw.get("system", ""), "model": "mock"}

    par = TP.run_parallel("拆解任务", cat["professions"][:3], run_id="v", ask=fake_ask,
                          max_workers=3)
    check("并发跑通", par.get("ok") and par["summary"]["ok"] == 3,
          "summary=%s" % par.get("summary"))
    check("每根独立房间 + 独立上下文（不共享黑板）",
          par["rooms"] == 3 and par["contexts"] == 3,
          "rooms=%d contexts=%d" % (par["rooms"], par["contexts"]))
    check("未知 op 显式报错", TP.run("nope").get("ok") is False,
          str(TP.run("nope")["error"])[:40])

    st = TP.status()
    check("status 报出职业数/分域/已立专业", (st.get("职业数") or 0) > 200,
          "职业=%s 分域=%s 已立=%s 库=%s" % (st.get("职业数"), st.get("分域数"),
                                              st.get("触手专业已立"),
                                              Path(st.get("库", "")).name))

    print("\n结论：" + ("✅ 触手职业名册全过" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
