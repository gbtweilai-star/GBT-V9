# tools/verify_capability_chain.py —— 能力链验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 背景（2026-10-08 真机病因）：串联能力只在 workflows/engine.py 的 $ref DAG 里，而
#   ① 引擎没有 HTTP 入口（唯一提到入口的代码被错放进 workflows/store.py，从自己 import 自己）；
#   ② 注入的 CapRegistry 与引擎要求的 SkillRegistry 形状不一致（validate 查 .skills、执行调 .call）；
#   ③ 没有把“上一步输出”喂给“下一步请求体”的机制（每个能力都得靠调用方猜入参键名）。
# 本验收器把这三条变成可判定读数：红了就是没通。
#
# 用法：python tools/verify_capability_chain.py    退出码 0=全过 / 1=有断言不过
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
    print("== 能力链验收 @", ROOT, "==")
    from audit.ledger_factory import make_ledger
    from skills.caps.adapter import as_skill_registry
    from skills.caps.base import Availability, CapResult, Capability
    from skills.caps.registry import build_caps_registry
    from workflows.engine import WorkflowEngine

    led = make_ledger()

    # ── 1. 适配器：53 项能力长成 SkillRegistry 形状 ──
    reg = as_skill_registry(build_caps_registry(ledger=led), ledger=led)
    check("适配器暴露 .skills（引擎按它校验）", len(reg.skills) >= 50,
          ".skills=%d" % len(reg.skills))
    check("适配器保留 .caps（面板按它列表）", len(reg.caps) >= 50,
          ".caps=%d" % len(reg.caps))
    check("适配器可目录化（catalog 按 .skills 读）",
          hasattr(reg, "skills") and hasattr(reg, "get"), "ok")

    # ── 2. 测试替身：证明“上一步输出 → 下一步请求体”精确贯通 ──
    seen: list = []

    class EchoCap(Capability):
        id = "verify.echo@1"
        version = "verify-1.0"
        equivalence = "full"
        description = "验收用回显：把收到的请求原样放进 output.echo"

        def probe(self):
            return Availability(True, "ok")

        def run(self, request=None):
            seen.append(request)
            return CapResult(ok=True, output={"echo": request})

    reg.register(EchoCap())
    eng = WorkflowEngine(reg, ledger=led)
    flow = {"id": "verify-chain", "nodes": [
        {"id": "s0", "type": "skill", "skill": "verify.echo@1",
         "inputs": {"a": 1, "b": "x"}},
        {"id": "s1", "type": "skill", "skill": "verify.echo@1",
         "inputs": {}, "pipe": "s0"},
    ], "edges": [["s0", "s1"]]}
    errs = eng.validate(flow)
    check("链通过 DAG 校验", not errs, "errors=%s" % (errs or "无"))
    out = eng.run(flow)
    second = seen[1] if len(seen) > 1 else None
    check("上一步输出精确喂进下一步", second == {"echo": {"a": 1, "b": "x"}},
          "第二步收到的请求=%s" % (second,))
    check("链整体 ok 且两步都有结果", bool(out.get("ok")) and len(out.get("steps", [])) == 2,
          "ok=%s steps=%d" % (out.get("ok"), len(out.get("steps", []))))

    # ── 3. 负例：链的护栏是真在挡 ──
    cyc = {"id": "c", "nodes": [{"id": "a", "type": "skill", "skill": "verify.echo@1"},
           {"id": "b", "type": "skill", "skill": "verify.echo@1"}],
           "edges": [["a", "b"], ["b", "a"]]}
    check("环被挡", any("环" in e for e in eng.validate(cyc)),
          "%s" % eng.validate(cyc)[:1])
    bad = {"id": "b", "nodes": [{"id": "a", "type": "skill", "skill": "verify.echo@1",
           "inputs": {"v": "$ref:b.output"}},
           {"id": "b", "type": "skill", "skill": "verify.echo@1"}],
           "edges": [["a", "b"]]}
    check("非上游 $ref 被挡", any("非上游" in e for e in eng.validate(bad)),
          "%s" % eng.validate(bad)[:1])
    unk = {"id": "u", "nodes": [{"id": "a", "type": "skill", "skill": "no.such.cap@9"}],
           "edges": []}
    check("未注册能力被挡", any("未注册" in e for e in eng.validate(unk)),
          "%s" % eng.validate(unk)[:1])
    leak = {"id": "l", "nodes": [{"id": "a", "type": "skill",
            "skill": "verify.echo@1"}], "edges": [],
            "note": "api_key: sk-abcdefghijklmnop1234"}
    check("明文凭据被挡", any("明文凭据" in e for e in eng.validate(leak)),
          "%s" % eng.validate(leak)[:1])

    # ── 4. HTTP 路由（挂在同一只 /api/panel router 上）──
    import panel.panel_api as pa
    from panel import workflows_api as wf
    pa.router.ledger = led
    pa.router.registry = reg

    cat = wf.wf_catalog()["data"] or {}
    check("GET /workflows/catalog 可用", cat.get("nodes") is not None,
          "节点=%d" % len(cat.get("nodes") or []))

    r = wf.wf_run_inline({"steps": [{"skill": "verify.echo@1", "inputs": {"k": "v"}},
                          {"skill": "verify.echo@1"}]})
    d = r["data"] or {}
    check("POST /workflows/run（线性 steps）可用", r["error"] is None and d.get("ok"),
          "ok=%s trace=%s" % (d.get("ok"), d.get("trace_id")))
    trace = d.get("trace_id")
    got = wf.wf_run_get(trace)["data"] if trace else None
    check("GET /workflow-runs/{id} 能回读这次链", bool(got),
          "steps=%d" % (len(got.get("steps", [])) if got else 0))

    s = wf.wf_save("verify-flow", {"name": "验收链", "definition": flow})["data"] or {}
    check("PUT /workflows/{fid} 存得下（乐观锁）", s.get("version") == 1,
          "version=%s" % s.get("version"))
    # 冲突：store 层抛 VersionConflict，路由层按原设计转 409（此处直调路由函数，接到的就是 HTTPException）
    conflict, code = None, None
    try:
        wf.wf_save("verify-flow", {"name": "验收链", "definition": flow, "version": 99})
    except Exception as exc:
        conflict = type(exc).__name__
        code = getattr(exc, "status_code", None)
    check("版本冲突不静默覆盖（VersionConflict → 409）",
          code == 409 or conflict == "VersionConflict",
          "%s status=%s" % (conflict, code))
    got2 = wf.wf_get("verify-flow")["data"] or {}
    defn = got2.get("definition") or {}
    check("GET /workflows/{fid} 取得回（含 definition）", bool(defn.get("nodes")),
          "nodes=%d version=%s" % (len(defn.get("nodes", [])), got2.get("version")))
    ran = wf.wf_run("verify-flow", {})["data"] or {}
    check("POST /workflows/{fid}/run 跑得动", bool(ran.get("ok")),
          "steps=%d" % len(ran.get("steps", [])))
    check("GET /workflows 列得出", any(x["flow_id"] == "verify-flow"
          for x in (wf.wf_list()["data"] or [])), "ok")
    wf.wf_delete("verify-flow")

    # ── 5. 真能力链（不是替身）──
    ids = [i for i in reg.skills if i.startswith("brain.")][:2] or list(reg.skills)[:2]
    real = wf.wf_run_inline({"steps": [{"skill": ids[0]}, {"skill": ids[1]}]})
    rd = real["data"] or {}
    check("两条真能力串起来跑通", real["error"] is None and rd.get("ok"),
          "%s -> %s，step ok=%s" % (ids[0], ids[1],
                                     [x.get("ok") for x in rd.get("steps", [])]))

    print("\n结论：" + ("✅ 能力链全过" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
