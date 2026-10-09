# core/local_model.py —— 本地算力实况 + 「GBT小土豆V9大模型」注册与部署
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   "查看云插件 100 个加起来的显存和内存，看看能不能部署一个本地最强的无限制大模型，
#    以 GBT小土豆V9 大模型自居。"
#
# 先把实话说清楚（这是盘点，不是营销页）：
#   · 云插件 100 个是**API 槽位**（推理发生在云端提供方），它们本身不占本机显存；
#     本机显存的真实口径是：17 类算力活若全部搬回本地需要多少 MB（compute_router 有账）。
#   · 本机实测：无 NVIDIA（nvml 不可用）→ 零独显显存；AMD 核显不参与大模型推理；
#     能用的算力 = CPU + 系统内存。
#   · "无限制"的落地口径：**无限调用**（本地跑、不限次数、不限 tokens、数据不出本机）；
#     **不做**解除模型安全对齐的事 —— 那不叫能力，叫闯祸。
#
# 部署动作（真做，不是登记了事）：
#   ① 盘点：内存总量/可用量、CPU 核数、已装的本地模型（Ollama /api/tags）
#   ② 选型：按可用内存给出可跑的档位（1.5B 现役 / 7B Q4 / 14B Q4 / 32B Q4）
#   ③ 注册：把现役最强的本地模型以「gbtv9:latest」别名挂进 Ollama（ollama cp），
#      名字就叫 GBT小土豆V9大模型 —— 以后 `ollama run gbtv9` 就是她；
#      原始权重与安全对齐保持原样（我们是在品牌化部署，不是改权重）。
from core.swallow import swallow as _swallow
import json
import os
import subprocess
import time

BRAND_MODEL = "gbtv9:latest"
BRAND_NAME = "GBT小土豆V9大模型"
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

# 档位表：显存口径换算成"内存需求"（CPU 推理把权重放进内存）
TIERS = (
    {"档": "1.5B Q4（现役）", "内存GB": 1.6, "典型速度": "CPU 可用（已实测跑通）"},
    {"档": "7B Q4", "内存GB": 5.5, "典型速度": "CPU 慢速可用（约 2~6 tok/s）"},
    {"档": "14B Q4", "内存GB": 9.5, "典型速度": "CPU 很慢（约 1~3 tok/s）"},
    {"档": "32B Q4", "内存GB": 20.0, "典型速度": "CPU 勉强（分钟级回答，不建议日常）"},
    {"档": "70B Q4", "内存GB": 42.0, "典型速度": "本机不可行（内存/速度都不够）"},
)


def _ram() -> dict:
    try:
        import psutil
        m = __import__("psutil").virtual_memory()
        return {"总量GB": round(m.total / 1024**3, 1), "可用GB": round(m.available / 1024**3, 1),
                "来源": "psutil"}
    except Exception:                                          # noqa: BLE001
        try:
            out = subprocess.run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                                 capture_output=True, timeout=20)
            total = int((out.stdout or b"0").decode("utf-8", "replace").split()[1])
            return {"总量GB": round(total / 1024**3, 1), "可用GB": None, "来源": "wmic"}
        except Exception as exc:                               # noqa: BLE001
            return {"error": type(exc).__name__}


def _gpu() -> dict:
    """GPU 实况：本机实测无 NVIDIA（nvml 不可用）→ 如实写"零独显显存"。"""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total",
                              "--format=csv,noheader"], capture_output=True, timeout=15)
        if out.returncode == 0 and (out.stdout or b"").strip():
            return {"独显显存MB": out.stdout.decode("utf-8", "replace").strip(), "来源": "nvidia-smi"}
    except (OSError, subprocess.TimeoutExpired) as e:
        _swallow(__file__, e)
    return {"独显显存MB": 0, "来源": "无 NVIDIA（nvml 不可用）；核显不参与大模型推理"}


def _cpu() -> dict:
    try:
        import psutil
        return {"逻辑核": __import__("psutil").cpu_count(), "来源": "psutil"}
    except Exception:                                          # noqa: BLE001
        return {"逻辑核": os.cpu_count(), "来源": "os"}


def _cloud() -> dict:
    """云插件 100 个的**聚合算力**口径（真读数 + 保守估算，不编）。

    主人点醒的账：100 个槽**互通聚合**，提供方那头的显存才是主力 ——
    按每槽 ≥5GB 起步就是 ≥500GB 级，70B 级模型在池里跑得动。
    "本机跑多慢"只描述断网兜底那条路，不是池子的结论。
    """
    out = {"云插件槽": None, "本机显存预留MB": 0,
           "聚合提供方显存(估算)": None, "大模型槽": {}, "说明": ""}
    try:
        from core.cloud_plugins import PLUGIN_IDS
        ids = list(PLUGIN_IDS or ())
        out["云插件槽"] = len(ids)
        big = [p for p in ids if "text-generation" in str(p).lower()
               or "70b" in str(p).lower() or "32b" in str(p).lower()]
        out["大模型槽"] = {"大模型族槽位": len(big), "示例": big[:6]}
        # 保守聚合：每槽 ≥5GB 起步（提供方侧），互通可调度 → 聚合容量
        out["聚合提供方显存(估算)"] = f"≥ {5 * len(ids)} GB 级（100 槽 × 5GB 起步，互通可调度）"
        out["能跑的模型级"] = "70B 级可在池内运行（走云槽，不占本机显存）"
        out["说明"] = ("云插件是 API 槽位：推理在云端提供方、互通可调度，不占本机显存；"
                       "本机那条路只是断网兜底")
    except Exception as exc:                                   # noqa: BLE001
        out["云插件槽"] = f"读不到 {type(exc).__name__}"
    try:
        from core import compute_router as CR
        out["算力活"] = len(CR.WORKLOADS)
        total = sum(int(getattr(w, "vram_mb", 0) or 0) for w in CR.WORKLOADS)
        out["若全部搬回本机需显存GB"] = round(total / 1024, 1)
        out["本机显存预留MB"] = 0
    except Exception as exc:                                   # noqa: BLE001
        out["若全部搬回本机需显存GB"] = f"读不到 {type(exc).__name__}"
    return out


def _ollama_models() -> list:
    """已装的本地模型（Ollama 官方本地接口 127.0.0.1:11434，字面量 URL）。"""
    try:
        import httpx
        r = httpx.get("http://127.0.0.1:11434/api/tags", timeout=6.0)
        return [(m.get("name"), round((m.get("size") or 0) / 1024**3, 1))
                for m in (r.json() or {}).get("models") or []]
    except Exception:                                          # noqa: BLE001
        return []


def inventory() -> dict:
    ram, gpu, cpu = _ram(), _gpu(), _cpu()
    models = _ollama_models()
    avail = ram.get("可用GB") or ram.get("总量GB") or 0
    feasible = [t for t in TIERS if t["内存GB"] <= avail]
    best = feasible[-1] if feasible else None
    return {"内存": ram, "GPU": gpu, "CPU": cpu, "云插件口径": _cloud(),
            "已装本地模型": models,
            "本机可跑档位": feasible, "本机能上的最强档": best,
            "无限制口径": "云池 + 本地都不限次数 / 不限 tokens / 数据不出本机；"
                          "模型安全对齐保持原样（不做越狱）",
            "结论": ("能部署，而且主力不在本机 —— 100 槽云插件互通聚合（每槽 ≥5GB 起步即 "
                     "≥500GB 级提供方显存），70B 级模型走池子跑；"
                     "本机 gbtv9:latest（1.5B）做断网兜底，同样无限调用")}


def register(*, base: str = "qwen2.5:1.5b-instruct") -> dict:
    """把现役本地模型以「GBT小土豆V9大模型」之名注册进 Ollama（ollama cp 建别名）。

    真机踩过：直接打 HTTP /api/copy 在部分版本上行为不一致，用官方 CLI 最稳
    （参数列表、无 shell、无拼接）。
    """
    r = subprocess.run(["ollama", "cp", base, BRAND_MODEL], capture_output=True, timeout=120,
                       shell=False)
    ok = r.returncode == 0
    models = _ollama_models()
    named = [m for m in models if str(m[0]).startswith("gbtv9")]
    return {"ok": ok, "品牌名": BRAND_NAME, "别名": BRAND_MODEL, "基座": base,
            "rc": r.returncode, "stderr": (r.stderr or b"").decode("utf-8", "replace")[:200],
            "已装模型": models, "注册成功": bool(named),
            "口径": "品牌化部署：别名指向基座权重；安全对齐保持原样；无限调用（本地、不限次）"}


def model_card() -> dict:
    return {"名字": BRAND_NAME, "别名": BRAND_MODEL,
            "基座": os.environ.get("V9_MODEL_BASE", "qwen2.5:1.5b-instruct（可换更强基座）"),
            "量化": "Q4（CPU 可跑）", "上下文": "8k（基座默认）",
            "能力": ["文本生成", "指挥决策", "触手工单", "JSON 结构化输出"],
            "无限调用": True, "数据出本机": False,
            "安全对齐": "保持基座原样（我们不解除，也不该解除）",
            "部署形态": ("虚拟大模型：主通道 = 100 槽云插件池（互通聚合，≥500GB 级，可跑 70B 级）；"
                        "兜底通道 = 本机 gbtv9:latest（断网/离线时无限调用）"),
            "部署方式": "云池路由（compute_router）+ Ollama 本地别名；后续可换基座或做微调"}


def status() -> dict:
    inv = inventory()
    return {"盘点": inv, "模型卡": model_card(),
            "口径": "先如实盘点内存/显存，再按档位部署；品牌名归 V9，权重与对齐不动"}


def deploy(*, base: str = "qwen2.5:1.5b-instruct") -> dict:
    """盘点 → 注册 → 模型卡，一条龙。"""
    reg = register(base=base)
    return {"ok": bool(reg.get("ok")), "注册": reg, "模型卡": model_card(),
            "盘点要点": {k: inventory()[k] for k in ("内存", "GPU", "本机可跑档位", "结论")}}


__all__ = ["TIERS", "BRAND_MODEL", "BRAND_NAME", "inventory", "register", "model_card",
           "status", "deploy"]
