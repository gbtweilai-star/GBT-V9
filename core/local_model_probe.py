# core/local_model_probe.py —— 本地模型可跑性探针（"这玩意大不大"用读数回答）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人问（2026-10-09）：「看看这玩意（MiniCPM）大不大？」
# 本件把问题变成**读数**：① 量本机家底（内存/显存/磁盘/ollama 已有模型）
#   ② 拿模型尺寸需求表对表 ③ 逐档判 能跑 / 勉强 / 跑不动，并给理由。
# 口径：参数量→Q4_K_M 内存按 **params×0.55GB + 0.6GB 运行时开销** 粗估（标"估"的就是估）；
#      显存需求按同口径；无独显时全部压到内存+swap，速度会掉到每秒几个 token。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 模型尺寸需求表（估=按参数量估，实=厂商/社区公开读数）
MODELS: tuple = (
    dict(名="MiniCPM5-1B", 参数="1B", Q4内存GB=1.2, 类型="文本", 来源="README（MiniCPM5 系列）", 备="on-device 定位"),
    dict(名="MiniCPM5-2B", 参数="2B", Q4内存GB=1.7, 类型="文本", 来源="README（最新主力·2B级 SOTA）", 备="与 4B 级竞争"),
    dict(名="MiniCPM4-0.5B", 参数="0.5B", Q4内存GB=0.9, 类型="文本", 来源="公开资料（MiniCPM4 系列）", 备="最小档"),
    dict(名="MiniCPM3-4B", 参数="4B", Q4内存GB=3.1, 类型="文本", 来源="公开资料", 备=""),
    dict(名="MiniCPM4-8B", 参数="8B", Q4内存GB=5.6, 类型="文本", 来源="公开资料（Eagle 稀疏注意力）", 备=""),
    dict(名="MiniCPM-V-2.6-8B", 参数="8B", Q4内存GB=6.2, 类型="视觉", 来源="MiniCPM-V 仓库", 备="看图要额外显存"),
    dict(名="MiniCPM-o-2.6-8B", 参数="8B", Q4内存GB=6.5, 类型="全模态", 来源="MiniCPM-o", 备=""),
    dict(名="qwen3:0.6b", 参数="0.6B", Q4内存GB=0.9, 类型="文本", 来源="本机已装（实测 522MB）", 备="已在本机"),
    dict(名="qwen2.5:1.5b-instruct", 参数="1.5B", Q4内存GB=1.5, 类型="文本", 来源="本机已装（实测 986MB）", 备="已在本机"),
    dict(名="qwen3:latest", 参数="8B", Q4内存GB=5.2, 类型="文本", 来源="本机已装（实测 5.2GB）", 备="本仓本地兜底档"),
)


def machine() -> dict:
    """量本机家底（读不到就给 None，不编）。"""
    out = {"内存总GB": None, "内存可用GB": None, "磁盘可用GB": None, "显存GB": None,
           "CPU": None, "CPU核": None, "ollama": None, "已装模型": []}
    try:
        import ctypes

        class MS(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MS(); m.dwLength = ctypes.sizeof(MS)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        out["内存总GB"] = round(m.ullTotalPhys / 1073741824, 1)
        out["内存可用GB"] = round(m.ullAvailPhys / 1073741824, 1)
    except Exception as e:
        _swallow(__file__, e)
    try:
        u = shutil.disk_usage(str(ROOT))
        out["磁盘可用GB"] = round(u.free / 1073741824, 1)
    except Exception as e:
        _swallow(__file__, e)
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
                           capture_output=True, timeout=20)
        s = (r.stdout or b"").decode("utf-8", "replace").strip()
        if s:
            out["显存GB"] = s.split(",")[-1].strip()
            out["GPU"] = s.split(",")[0].strip()
        else:
            out["显存GB"] = "无独显（nvidia-smi 无输出）"
    except Exception:  # noqa: BLE001
        out["显存GB"] = "无独显（nvidia-smi 不可用）"
    try:
        out["CPU核"] = __import__("os").cpu_count()
    except Exception as e:
        _swallow(__file__, e)
    try:
        tl = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, timeout=60)
        procs = []
        for line in (tl.stdout or b"").decode("utf-8", "replace").splitlines():
            # tasklist /fo csv 的内存带千分位逗号（"12,345 K"）⇒ 必须按 "","" 切，不能按逗号切
            s = line.strip().strip(chr(34))
            parts = s.split(chr(34) + "," + chr(34))
            if len(parts) >= 5:
                mem = parts[-1].strip().strip(chr(34)).replace(",", "").replace("K", "").strip()
                try:
                    procs.append({"名": parts[0], "MB": int(float(mem) / 1024)})
                except Exception:
                    continue
        agg = {}
        for p in procs:
            agg[p["名"]] = agg.get(p["名"], 0) + p["MB"]
        out["内存吃客"] = [{"名": k, "MB": v} for k, v in sorted(agg.items(), key=lambda x: -x[1])[:6]]
    except Exception as e:
        _swallow(__file__, e)
    exe = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
    if exe.is_file():
        out["ollama"] = str(exe)
        try:
            r = subprocess.run([str(exe), "list"], capture_output=True, timeout=40)
            for line in (r.stdout or b"").decode("utf-8", "replace").splitlines()[1:]:
                p = line.split()
                if len(p) >= 3:
                    out["已装模型"].append({"名": p[0], "体积": p[2] + (p[3] if len(p) > 3 and p[3] in ("GB", "MB") else "")})
        except Exception as e:
            _swallow(__file__, e)
    return out


def judge(mach: dict | None = None) -> dict:
    """逐档判：能跑 / 勉强 / 跑不动（按可用内存，无独显时不加权）。"""
    m = mach or machine()
    avail = m.get("内存可用GB") or 0.0
    rows = []
    for spec in MODELS:
        need = spec["Q4内存GB"]
        if avail >= need * 1.6:
            verdict, why = "能跑", "可用 %.1fG ≥ 需求 %.1fG 的 1.6 倍" % (avail, need)
        elif avail >= need * 1.05:
            verdict, why = "勉强", "可用 %.1fG 刚过需求 %.1fG（要关掉别的程序，靠 swap 会卡）" % (avail, need)
        else:
            verdict, why = "跑不动", "可用 %.1fG < 需求 %.1fG" % (avail, need)
        rows.append({**spec, "判定": verdict, "理由": why})
    need0 = min(x["Q4内存GB"] for x in MODELS)
    free_up = ("要跑最小档（%.1fG）需再腾出 ≈%.1fG；内存吃客见 家底[内存吃客]（最大通常是 vmmemWSL/WSL 虚拟机）"
               % (need0, max(0.0, need0 * 1.6 - avail)))
    return {"家底": m, "逐档": rows, "腾出建议": free_up,
            "口径": "Q4_K_M 内存 = 参数量×0.55GB + 0.6GB 运行时（估）；无独显时全部压内存/swap，速度大幅下降",
            "结论": ("本机最大可吃：%s" % max((r["名"] for r in rows if r["判定"] == "能跑"),
                                          default="无（连最小档都不够）"))}


def run(op: str = "report", **kw) -> dict:
    if op in ("report", "status", ""):
        return judge()
    return {"ok": False, "error": "unknown operation: %s" % op}


__all__ = ["MODELS", "machine", "judge", "run"]
