# tools/sweep_all_caps.py —— 逐能力全量重跑：① 独立运行（每个验收器单跑）② 协作运行（能力串起来真跑）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
import json, subprocess, sys, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
LED = ROOT / "state" / "capability_sweep.jsonl"


def log(rec):
    LED.parent.mkdir(parents=True, exist_ok=True)
    with LED.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def run_verifier(path, timeout=150):
    t0 = time.time()
    try:
        p = subprocess.run([sys.executable, path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=timeout)
        out = (p.stdout or "")
        concl = [l.strip() for l in out.splitlines() if l.strip().startswith("结论")]
        return {"ok": p.returncode == 0, "码": p.returncode, "秒": round(time.time() - t0, 1),
                "结论": (concl[-1][:90] if concl else out.strip().splitlines()[-1][:90] if out.strip() else "")}
    except subprocess.TimeoutExpired:
        return {"ok": False, "码": None, "秒": timeout, "结论": "超时（%ss）" % timeout}
    except Exception as e:
        return {"ok": False, "码": None, "秒": round(time.time() - t0, 1), "结论": type(e).__name__}


def main():
    sys.path.insert(0, str(ROOT))
    from core import delivery_gate as DG
    print("== ① 独立运行：逐能力单跑 ==")
    rows, ok_n = [], 0
    for name, cat, verifier in DG.CAPABILITIES:
        p = ROOT / verifier
        if not p.is_file():
            rows.append({"能力": name, "验收器": verifier, "ok": False, "结论": "验收器缺失"})
            print("  ❌ %-34s 验收器缺失" % name[:34])
            continue
        r = run_verifier(str(p))
        rows.append({"能力": name, "验收器": verifier, **r})
        ok_n += 1 if r["ok"] else 0
        print("  %s %-34s %ss %s" % ("✅" if r["ok"] else "❌", name[:34], r["秒"], r["结论"][:56]))
    print()
    print("== ② 协作运行：能力串起来真跑（编队 → 绑定 → 扫描 → 万能插 → 脑子 → 交付闸）==")
    chain = []
    try:
        from core import fleet_live as FL
        s = FL.status()
        chain.append(("编队", "统计 %s" % json.dumps(s["统计"], ensure_ascii=False)))
        from core import file_binding as FB
        c = FB.coverage()
        chain.append(("绑定", "覆盖 %.2f%%（%d/%d）" % (c["覆盖率"], c["已绑定"], c["绑定集"])))
        from core import cross_scan_fleet as CS
        fs = [f for f in (ROOT / "core").glob("*.py")][:60]
        CS.shards(fs, 100)
        chain.append(("穿透扫描", "分片 OK（%d 文件）" % len(fs)))
        from core import pulse as P
        r = P.plug_and_run("t011", kind="process", action="run", args={"cmd": ["python", "--version"]}, timeout=60)
        chain.append(("万能插 process", "ok=%s 码=%s %s" % (r.get("ok"), r.get("码"), (r.get("stdout") or "").strip()[:16])))
        r2 = P.plug_and_run("t012", kind="process", action="run", args={"cmd": ["whoami"]}, timeout=30)
        chain.append(("万能插 拒动", "拒动=%s" % r2.get("拒动")))
        from core import brain_v9 as B
        b = B.think("回两个字：在")
        chain.append(("脑子", "谁答的=%s 秒=%s" % (b.get("谁答的"), b.get("秒"))))
        from core import modular_deploy as MD
        cl = MD.closure_rate()
        chain.append(("模块闭环", "%s%%（%s/%s）" % (cl["闭环率"], cl["已闭环"], cl["模块数"])))
    except Exception as e:
        chain.append(("协作链路异常", "%s: %s" % (type(e).__name__, str(e)[:70])))
    for k, v in chain:
        print("  · %-16s %s" % (k, str(v)[:90]))
    log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "sweep", "独立": rows, "协作": chain})
    print()
    print("独立运行：%d/%d 通过 ｜ 协作运行：%d 环" % (ok_n, len(rows), len(chain)))


if __name__ == "__main__":
    main()
