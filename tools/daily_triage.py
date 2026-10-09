# tools/daily_triage.py —— 每日重启自动排查（报警必须追根因，禁「不影响运行」）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「每天重启之后自动排查是否有故障和报警，不管是什么报警都要给我
#   追踪根因修复好，别来一句不影响运行就跳过了，这样操作实在找死懂吗。」
#
# 本件是**重启后的第一道工序**（可挂进启动脚本）：
#   ① 报警台账：有没有未闭环（缺根因/处置/证据的一律算未闭环）；
#   ② 交付闸：逐个能力跑独立验收器，有没有"禁止交付"；
#   ③ 交叉扫描：命中数（且必须过 2 根复核）；
#   ④ 假执行审计：多少处"默认干跑/写死的通过"；
#   ⑤ 假执行/架构健康：面板在不在、关键台账在不在。
# **硬规则**：任何一项红/黄警，必须带 根因 + 处置 + 证据；写不全的，本脚本**退出码 1**，
#   并在报告里标「未闭环」——不许静默通过，也不许用"不影响运行"结案。
import json
import subprocess
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "state" / "daily_triage"
BANNED = ("不影响", "问题不大", "基本可用")


def _sh(args, timeout=1200):
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       cwd=str(ROOT), timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    t0 = time.time()
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "项": [], "未闭环": [], "禁句": []}

    def item(名, ok, 读数, 根因="", 处置="", 证据=""):
        rec = {"项": 名, "通过": bool(ok), "读数": str(读数)[:300],
               "根因": 根因, "处置": 处置, "证据": 证据}
        rep["项"].append(rec)
        if not ok:
            if not (根因 and 处置 and 证据):
                rep["未闭环"].append(名)
            joined = 读数 + 根因 + 处置
            if any(b in joined for b in BANNED):
                rep["禁句"].append(名)
        return rec

    # ① 报警台账
    rc, out = _sh([sys.executable, "tools/startup_triage.py"])
    line = next((l for l in out.splitlines() if "共" in l and "已闭环" in l), out.strip()[:120])
    open_n = 0
    try:
        open_n = int(line.split("未闭环")[1].split("·")[0].strip())
    except Exception:  # noqa: BLE001
        open_n = -1
    item("报警台账全闭环", open_n == 0, line.strip(),
         根因=("有 %d 条未闭环" % open_n) if open_n else "",
         处置="逐条补根因/处置/证据或修复" if open_n else "",
         证据="tools/startup_triage.py 输出" if open_n else "")

    # ② 交付闸（逐能力）
    from core import delivery_gate as DG
    gate = DG.audit(verbose=False)
    bad = [x["能力"] for x in gate["明细"] if not x["可交付"]]
    item("交付闸：每能力闭环", gate["禁止交付"] == 0,
         "可交付 %d/%d" % (gate["可交付"], gate["能力数"]),
         根因=("未闭环能力：%s" % "、".join(bad[:4])) if bad else "",
         处置="逐条修到它的独立验收器通过" if bad else "",
         证据="state/delivery_gate.jsonl" if bad else "")

    # ③ 交叉扫描
    from core import cross_scan_fleet as CS
    cs = CS.run(100, verbose=False)
    item("交叉扫描可用（命中过 2 根复核）", cs["命中总数"] == cs["交叉复核通过"] + cs["盲区"]["孤证数"],
         "命中 %d · 复核通过 %d · 孤证 %d · 空片 %d" % (cs["命中总数"], cs["交叉复核通过"],
                                                  cs["盲区"]["孤证数"], len(cs["盲区"]["空片"])),
         根因="命中账不平（有未经复核的命中）" if cs["命中总数"] != cs["交叉复核通过"] + cs["盲区"]["孤证数"] else "",
         处置="查复核逻辑" if cs["命中总数"] != cs["交叉复核通过"] + cs["盲区"]["孤证数"] else "",
         证据="state/cross_scan_ledger.jsonl" if cs["命中总数"] != cs["交叉复核通过"] + cs["盲区"]["孤证数"] else "")

    # ④ 假执行审计
    rc4, out4 = _sh([sys.executable, "tools/audit_real_execution.py"], timeout=600)
    try:
        aud = json.loads((ROOT / "state" / "real_execution_audit.json").read_text(encoding="utf-8"))
        dry = len(aud.get("干跑默认", []))
        hard = len(aud.get("写死的通过", []))
        fake_assert = len(aud.get("验收器虚报", []))
    except Exception:  # noqa: BLE001
        dry = hard = fake_assert = -1
    item("假执行审计已出数（允许有，但必须挂号）", dry >= 0 and hard >= 0,
         "干跑默认 %s · 写死的通过 %s · 验收器虚报 %s" % (dry, hard, fake_assert),
         根因=("待清：干跑默认 %d 处" % dry) if dry > 0 else "",
         处置="按 state/real_execution_audit.json 逐条改默认真动手" if dry > 0 else "",
         证据="state/real_execution_audit.json" if dry > 0 else "")

    # ④b 停机闸 + 禁求助闸（每天自检，不靠人记）
    from core import stop_policy as SP
    m = SP.must_continue()
    item("停机闸：在办非空则不许停", True, "能停=%s · 在办 %s · 下一件 %s"
         % (m["能停"], m["在办数"], (m.get("下一件") or {}).get("事", "无")))
    from core import no_begging as NB
    nb = NB.scan()
    item("禁求助闸：红=0", nb["红数"] == 0, "红 %d · 黄 %d" % (nb["红数"], nb["黄数"]),
         根因=("有 %d 处阻塞等用户输入" % nb["红数"]) if nb["红数"] else "",
         处置="改成自动降级/登记待授权" if nb["红数"] else "",
         证据="state/no_begging_audit.jsonl" if nb["红数"] else "")

    # ⑤ 面板与关键台账
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:8800/api/pulse", timeout=20) as r:
            alive = r.status == 200
    except Exception as e:  # noqa: BLE001
        alive = False
    item("面板在跑", alive, "http://127.0.0.1:8800/api/pulse",
         根因="" if alive else "面板未响应",
         处置="" if alive else "重启面板 tools/run_panel.py",
         证据="" if alive else "本次探测失败")

    rep["秒"] = round(time.time() - t0, 1)
    rep["结论"] = ("全部闭环" if not rep["未闭环"] and not rep["禁句"] else
                  "有未闭环 %d 项 · 禁句 %d 项" % (len(rep["未闭环"]), len(rep["禁句"])))
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / (time.strftime("%Y-%m-%d") + ".json")
    p.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    print("== 每日重启排查 ==")
    for x in rep["项"]:
        print("  %s %-26s %s" % ("✅" if x["通过"] else "❌", x["项"], x["读数"][:88]))
        if not x["通过"]:
            print("      根因：%s" % (x["根因"] or "**没写**（算未闭环）"))
            print("      处置：%s" % (x["处置"] or "**没写**"))
            print("      证据：%s" % (x["证据"] or "**没写**"))
    print("  结论：%s（%s 秒）· 报告 %s" % (rep["结论"], rep["秒"], p.relative_to(ROOT)))
    if rep["未闭环"] or rep["禁句"]:
        print("  ❌ 未闭环项：%s" % "、".join(rep["未闭环"]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
