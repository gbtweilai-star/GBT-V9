# core/local_llm_probe.py —— 本地大模型前置体检（装之前就告诉你：能不能跑、能跑多大、大概多慢）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 为什么要有它：主人问过 AirLLM（"4GB 显存跑 70B"）对现在有没有帮助。
#   答案是"要有前提"，而前提可以**在装任何东西之前**就量出来：
#     ① 后端：AirLLM 靠逐层 .cuda() 搬运 ⇒ 要 CUDA（N 卡）；AMD 卡得走 ROCm/DirectML，Windows 上多半没有；
#     ② host 内存：逐层载入要内存缓冲；
#     ③ 磁盘：模型要先**逐层分解落盘**，4bit 70B 也要几十 GB，405B fp16 是几百 GB。
# 本模块只读探针，**不装任何东西、不改系统**；量不到就说量不到。
from __future__ import annotations
from core.swallow import swallow as _swallow

import os
import shutil
import subprocess
import sys
from pathlib import Path

# 常见规模的"需要多少空间"（字节口径按十进 GB 粗算；fp16 按 2 字节/参数，4bit 按 0.5，8bit 按 1）
MODEL_SIZES = (("7B", 7), ("8B", 8), ("14B", 14), ("32B", 32), ("70B", 70),
               ("235B", 235), ("405B", 405), ("671B", 671), ("2.8T", 2800))
QUANTS = (("fp16", 2.0), ("8bit", 1.0), ("4bit", 0.5))


def _run(cmd: list, *, timeout: float = 25.0) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=True)
        return ((r.stdout or b"") + (r.stderr or b"")).decode("utf-8", "replace")
    except Exception as exc:                                 # noqa: BLE001
        return f"（取不到：{type(exc).__name__}）"


def gpu() -> dict:
    out = _run(["powershell", "-NoProfile", "-Command",
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"])
    return {"原始": out.strip()[:400]}


def backends() -> dict:
    """逐个后端探"能不能用"：CUDA / DirectML / ROCm / Vulkan / llama.cpp。"""
    got = {}
    got["torch"] = _run([sys.executable, "-c",
                         "import torch;print(torch.__version__, torch.cuda.is_available())"]).strip()
    got["torch-directml"] = _run([sys.executable, "-c",
                                  "import torch_directml;print('directml ok', torch_directml.device_count())"]).strip()[:60]
    got["airllm"] = _run([sys.executable, "-c", "import airllm;print('airllm 已装')"]).strip()[:60]
    got["llama.cpp"] = (_run(["where", "llama-cli"]).strip() or _run(["where", "main.exe"]).strip())[:80]
    got["vulkan"] = ("有 vulkan-1.dll" if Path("C:/Windows/System32/vulkan-1.dll").is_file()
                     else "没有 vulkan-1.dll")
    return got


def resources() -> dict:
    total = avail = 0
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
        total, avail = m.ullTotalPhys, m.ullAvailPhys
    except Exception as e:
        _swallow(__file__, e)
    du = shutil.disk_usage(Path(os.environ.get("SystemDrive", "C:") + "\\"))
    return {"内存总数GB": round(total / 1e9, 1), "内存可用GB": round(avail / 1e9, 1),
            "磁盘可用GB": round(du.free / 1e9, 1), "磁盘总GB": round(du.total / 1e9, 1)}


def model_table() -> list:
    """各规模 × 量化需要多少 GB（**总存储**，不是显存；AirLLM 要先把模型分层落盘）。"""
    rows = []
    for name, params in MODEL_SIZES:
        row = {"规模": name, "参数(B)": params}
        for q, mult in QUANTS:
            row[q + "GB"] = round(params * mult, 1)
        rows.append(row)
    return rows


def verdict() -> dict:
    """给结论：这台机器现在能不能跑、能跑多大、卡在哪（逐条给依据）。"""
    res = resources(); be = backends()
    disk = res["磁盘可用GB"]; ram = res["内存可用GB"]
    cuda = "True" in be.get("torch", "")
    dml = "directml ok" in be.get("torch-directml", "")
    cpp = bool(be.get("llama.cpp")) and "（取不到" not in be.get("llama.cpp", "")
    blockers, fits = [], []
    if not cuda:
        blockers.append("没有可用的 CUDA（torch 报 cuda_available=False）⇒ AirLLM 的逐层 .cuda() 搬运起不来")
    if not dml:
        blockers.append("没有 torch-directml ⇒ AMD 卡少一条备选后端")
    if ram < 8:
        blockers.append(f"可用内存只有 {ram} GB（逐层载入要缓冲）")
    if disk < 40:
        blockers.append(f"磁盘可用只有 {disk} GB（4bit 70B 也要 {round(70*0.5,1)} GB 起步，还要几倍做分解）")
    for row in model_table():
        if row["4bitGB"] * 2 <= disk and row["4bitGB"] <= disk * 0.5:
            fits.append(row["规模"] + "(4bit)")
    return {"结论": "现在跑不起来：" + "；".join(blockers) if blockers else "前置条件满足，可试装",
            "卡点": blockers, "磁盘放得下的规模(4bit)": fits,
            "CUDA": cuda, "DirectML": dml, "llama.cpp": cpp,
            "资源": res, "后端": be,
            "建议": ("AMD + Windows 上更现实的是 llama.cpp（Vulkan 后端，4bit GGUF，可 mmap 分层卸载）；"
                     "AirLLM 更适合有 N 卡 + 大盘的机器。两者都不要用于她的实时语音（串行流式，延迟高）。"),
            "口径": "只读体检，未安装任何东西；量不到的一律标（取不到）"}


__all__ = ["gpu", "backends", "resources", "model_table", "verdict", "MODEL_SIZES", "QUANTS"]
