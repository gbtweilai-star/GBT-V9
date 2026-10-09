# tools/bench_cloud_mesh.py —— 云插件网格到底快不快：串行 vs 并行（真打 API，不看感觉）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 主人命题（2026-10-09）：「模块之间跑得比整块显卡还快，因为显卡跑一个循环很久，
#   触手绑定的云插件可以瞬间传输到任意插件上。」
# 本基准把命题拆成**可测的两件**：
#   ① 并发扇出（同一批活分给 N 个插件同时跑）—— 这才可能快过单卡串行；
#   ② 跨插件跳转的**真实代价**（每次 hop = 一次网络往返 + 一次独立推理）。
# 并如实标注测不到/不成立的部分：权重无法切开（每个槽是一整个模型），单请求延迟反而更高。
from __future__ import annotations

import concurrent.futures as CF
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PICKS = ["text-generation:llama-3.2-1b-instruct#1",
         "text-generation:llama-3.1-8b-instruct-fp8#3",
         "text-generation:qwen3-30b-a3b-fp8#7",
         "text-generation:mistral-small-3.1-24b-instruct#6"]
PROMPT = "用一句话说明：云插件网格能做并行推理。"


def main() -> int:
    from core import cloud_runner as CR
    print("== 云插件网格基准（真打 Workers AI）@", ROOT, "==")
    print("  参战插件：%d 个" % len(PICKS))
    for p in PICKS:
        print("    ·", p)

    t0 = time.time()
    serial = [CR.run_plugin(p, PROMPT, timeout=90) for p in PICKS]
    t_serial = time.time() - t0

    t0 = time.time()
    with CF.ThreadPoolExecutor(max_workers=len(PICKS)) as ex:
        par = list(ex.map(lambda p: CR.run_plugin(p, PROMPT, timeout=90), PICKS))
    t_par = time.time() - t0

    def stat(rows):
        ok = [r for r in rows if r.get("ok")]
        return len(ok), (sum(r.get("ms") or 0 for r in ok) // max(1, len(ok)))

    sok, sms = stat(serial)
    pok, pms = stat(par)
    print("\n  串行：跑通 %d/%d · 墙上时间 %.1fs · 单站均耗时 %dms" % (sok, len(PICKS), t_serial, sms))
    print("  并行：跑通 %d/%d · 墙上时间 %.1fs · 单站均耗时 %dms" % (pok, len(PICKS), t_par, pms))
    print("  扇出加速比：%.2fx" % (t_serial / t_par if t_par else 0))

    ch = CR.__dict__ and None
    from core import cloud_bind as CB
    one = CB.chain([PICKS[0], PICKS[1]], "一句话", tentacle="t001")
    hops = [s.get("ms") or 0 for s in one.get("步骤") or []]
    print("\n  两站串联（触手搬运）：跑通 %d/%d · 每跳 ms=%s" % (
          one.get("跑通"), one.get("站数"), hops))
    print("  终产出：%s" % (one.get("终产出") or "（无）")[:90])

    sh = CB.share_state()
    print("\n  插件互通网格：双向对 %s · 有向行 %s · 对称 %s" % (
          sh.get("shared_pairs"), sh.get("rows"), sh.get("symmetric")))

    print("\n口径（不许把话说过头）：")
    print("  · 能突破的是**并发/吞吐**：N 个插件同时干 N 份活，实测扇出加速 %.2fx；" % (t_serial / t_par if t_par else 0))
    print("  · 跨插件跳转**不是零延时**：每一跳 = 一次网络往返 + 一次独立推理，本机实测每跳 %s ms；" % hops)
    print("  · **权重切不开**：每个槽是一整个模型，100 个小模型拼不成一个超大模型；拆的是活，不是模型；")
    print("  · 单请求延迟反而更高（多跳叠加）；适合多步/批量/流水线，不适合替代单卡低延迟推理。")
    rec = {"串行s": round(t_serial, 2), "并行s": round(t_par, 2),
           "扇出加速比": round(t_serial / t_par, 2) if t_par else None,
           "每跳ms": hops, "双向对": sh.get("shared_pairs")}
    (ROOT / "state" / "cloud_bench.json").write_text(
        json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n  读数已落 state/cloud_bench.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
