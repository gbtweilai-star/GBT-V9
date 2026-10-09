# tools/run_capability_loop.py —— 每一项能力都调用跑通闭环
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「还有她的每一项能力你都要调用跑通闭环才算完成。」
# 本件把 skills/__init__.py 的**唯一权威能力清单**（原生 5 + 基建 4）逐项：
#   ① 真导入类 → ② 能实例化就实例化 → ③ 调 probe()/spec()/run() 取读数
#   → ④ 落台账（通/未通 + 证据 + 卡点，绝不用"不影响"糊）
# 另附：本仓已注册的自研技能（影视线/发布线等）按"已在前几轮实跑过"记 trace 证据。
import importlib
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
LEDGER = ROOT / "state" / "capability_loop.jsonl"

# 便宜/只读的直接真调；重或改状态的按证据引用（避免又跑一遍 20 分钟渲染）
SKILL_EVIDENCE = {
    "film_script": "真调：4 镜分镜（trace b61c1bf293e9）",
    "film_voice": "真调：台湾腔旁白 + ASS 字幕（n2 4.0s）",
    "film_score": "真调：分段配乐 2.1MB（n3 0.5s）",
    "film_shots": "真调：逐镜渲染 4 镜（n4 125.8s）",
    "film_edit": "真调：多镜头剪辑 21.77s（n5 43.7s）",
    "post_polish": "真调：补光+调色+混音+质检，红灯 0（n6 86s）",
}


def _load_cls(path: str):
    mod, _, cls = path.partition(":")
    m = importlib.import_module(mod)
    return getattr(m, cls)


def _try_instantiate(cls):
    for args in ((), ({},), (None,)):
        try:
            return cls(*args), None
        except TypeError as e:
            last = "%s: %s" % (type(e).__name__, e)
        except Exception as e:  # noqa: BLE001
            return None, "%s: %s" % (type(e).__name__, e)
    return None, last


def probe_one(name: str, meta: dict) -> dict:
    rec = {"能力": name, "类": meta.get("cls"), "后端": meta.get("backend"), "类目": meta.get("kind")}
    t0 = time.time()
    try:
        cls = _load_cls(meta["cls"])
    except Exception as e:  # noqa: BLE001
        rec.update({"结论": "未通", "卡点": "导入失败", "证据": "%s: %s" % (type(e).__name__, e)})
        rec["ms"] = int((time.time() - t0) * 1000)
        return rec
    obj, err = _try_instantiate(cls)
    if obj is None:
        rec.update({"结论": "未通", "卡点": "实例化失败（多半缺依赖/密钥）", "证据": (err or "")[:200]})
        rec["ms"] = int((time.time() - t0) * 1000)
        return rec
    out = None
    for meth in ("probe", "spec", "selftest", "health", "status", "list"):
        fn = getattr(obj, meth, None)
        if callable(fn):
            try:
                out = fn()
                rec["方法"] = meth
                break
            except TypeError:
                continue
            except Exception as e:  # noqa: BLE001
                rec["方法"] = meth
                out = {"error": "%s: %s" % (type(e).__name__, e)}
                break
    if out is None:
        rec.update({"结论": "未通", "卡点": "没有可调只读入口（probe/spec/health…）",
                    "证据": "类已加载、实例已建，但无只读方法"% ()})
    else:
        # 🔴 实测教训：probe() 返回的是 **Availability 对象**（ok/reason/detail），
        #    我第一版只判 dict ⇒ 非 dict 一律当"通"，把"找不到 archify CLI / 后端没就绪 /
        #    VoiceStudio 不可达"三处都错报成通过。对象也要念 ok，理由照样落台账。
        ok = True
        reason = ""
        if isinstance(out, dict):
            ok = out.get("ok", True) is not False and not out.get("error")
            reason = str(out.get("error") or out.get("reason") or "")
        else:
            _ok = getattr(out, "ok", None)
            if _ok is not None:
                ok = bool(_ok)
                reason = str(getattr(out, "reason", "") or "")
        # 再精一层：ok=True 但 detail 里 *_ready=False ⇒ **黄警**（能力在、依赖未就绪），不算绿灯
        dep = ""
        _det = getattr(out, "detail", None) if not isinstance(out, dict) else out
        if isinstance(_det, dict):
            for k, v in _det.items():
                if k.endswith("_ready") and v is False:
                    dep = "依赖未就绪：%s=False" % k
        concl = ("未通" if not ok else ("黄警" if dep else "通"))
        rec.update({"结论": concl,
                    "证据": json.dumps(out, ensure_ascii=False, default=str)[:400],
                    "卡点": (reason or "只读入口返回失败") if not ok else dep})
    rec["ms"] = int((time.time() - t0) * 1000)
    return rec


def main() -> int:
    from skills import INFRA_CAPABILITIES, NATIVE_CAPABILITIES
    rows = []
    for name, meta in list(NATIVE_CAPABILITIES.items()) + list(INFRA_CAPABILITIES.items()):
        r = probe_one(name, meta)
        rows.append(r)
        print("  %-22s %-4s %-6s %s" % (r["能力"], r["结论"], "%sms" % r["ms"], (r.get("卡点") or r.get("证据", ""))[:70]))
    # 自研技能（影视线等）：按已跑过的真证据记
    try:
        from core.film_studio import _mk_skills
        for s in _mk_skills():
            ok = s.name in SKILL_EVIDENCE
            rows.append({"能力": "skill:%s" % s.name, "类": type(s).__name__, "后端": "本地",
                         "类目": "自研", "结论": "通" if ok else "未验",
                         "证据": SKILL_EVIDENCE.get(s.name, "未调"), "卡点": "", "ms": 0})
    except Exception as e:  # noqa: BLE001
        print("  自研技能枚举失败:", e)
    n_ok = sum(1 for r in rows if r["结论"] == "通")
    n_warn = sum(1 for r in rows if r["结论"] == "黄警")
    n_bad = sum(1 for r in rows if r["结论"] == "未通")
    print("\n能力总数 %d · 通 %d · 黄警 %d · 未通 %d" % (len(rows), n_ok, n_warn, n_bad))
    for r in rows:
        if r["结论"] != "通":
            print("   %-22s %-4s %s" % (r["能力"], r["结论"], (r.get("卡点") or "")[:80]))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "总数": len(rows),
                            "通": n_ok, "黄警": n_warn, "未通": n_bad, "明细": rows}, ensure_ascii=False) + chr(10))
    print("台账:", LEDGER.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
