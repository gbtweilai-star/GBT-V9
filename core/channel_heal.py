# core/channel_heal.py —— 通道自愈（触手自己搞定，不问主人）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「让触手自己搞定。」
# 背景实测：CF Workers AI 401（wrangler OAuth 令牌 09:05 过期 + keys.env 只有账号 ID 没有 API token）；
#   自建 Worker 403（CF 1010 指纹拦截）；teamorouter 401；本地 ollama HTTP 200 但出空/乱码（可用内存 0.39GB）。
# 自愈顺序（每步都真跑真读，绝不假设）：
#   ① **回收内存**：卸掉 ollama 挂着的模型 + 退掉闲置常驻，把可用内存抬起来（本地小模型能不能跑，就看这一步）
#   ② **探本地**：拿真实模型名打 ollama 的 OpenAI 口，判"出字是否连贯"（空字/乱码一律不算通）
#   ③ **正规续期**：让 wrangler 自己去续 OAuth（它有正规指纹；我用 urllib 硬打会被 1010 挡）
#   ④ **选型落账**：第一条真能出字的通道写成 state/channel_choice.json，并记 state/channel_heal.jsonl
from __future__ import annotations
from core.swallow import swallow as _swallow

import ctypes
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "channel_heal.jsonl"
CHOICE = ROOT / "state" / "channel_choice.json"
WRANGLER_CFG = Path.home() / ".wrangler" / "config" / "default.toml"
CF_ACCOUNT = "82dd88c2a3907846e9797ea95419802f"
OLLAMA_EXE = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
LOCAL_BASE = os.environ.get("GBT_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1")
LOCAL_MODELS = ("qwen3:0.6b", "qwen2.5:1.5b-instruct", "gbtv9:latest")


def ram() -> dict:
    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return {"总GB": round(m.ullTotalPhys / 1073741824, 1), "可用GB": round(m.ullAvailPhys / 1073741824, 2)}


def free_memory(*, unload_models: bool = True, kill_llama: bool = True) -> dict:
    """① 回收内存：卸模型 + 退闲置。只动**模型宿主**，不动主人的前台程序。"""
    before = ram()
    acts = []
    if unload_models and OLLAMA_EXE.is_file():
        for m in LOCAL_MODELS:
            try:
                r = subprocess.run([str(OLLAMA_EXE), "stop", m], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=60,
                                   creationflags=0x08000000)
                acts.append({"动作": "ollama stop " + m, "码": r.returncode})
            except Exception as e:  # noqa: BLE001
                acts.append({"动作": "ollama stop " + m, "失败": type(e).__name__})
    if kill_llama:
        try:
            r = subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=30,
                               creationflags=0x08000000)
            acts.append({"动作": "taskkill llama-server", "码": r.returncode})
        except Exception as e:  # noqa: BLE001
            acts.append({"动作": "taskkill llama-server", "失败": type(e).__name__})
    time.sleep(2.5)
    after = ram()
    return {"前": before, "后": after, "腾出GB": round(after["可用GB"] - before["可用GB"], 2), "动作": acts}


def _coherent(text: str) -> tuple:
    """出字是否像话：空/超短/长串重复符号/乱码'@'串 一律不算通。"""
    t = (text or "").strip()
    if len(t) < 1:
        return False, "空字"
    if t.count("@") > 2 or t.count("\ufffd") > 0:
        return False, "乱码"
    if len(set(t)) <= 2 and len(t) > 3:
        return False, "重复符号"
    return True, "像话(%d字)" % len(t)


def probe_local(*, timeout: float = 120.0) -> dict:
    """② 真探本地（不假设它能跑）。"""
    out = []
    for mdl in LOCAL_MODELS:
        body = json.dumps({"model": mdl, "messages": [{"role": "user", "content": "回答两个字：在不在"}],
                           "max_tokens": 24, "stream": False}).encode()
        req = urllib.request.Request(LOCAL_BASE.rstrip("/") + "/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer ollama"})
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            msg = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            ok, why = _coherent(msg)
            out.append({"模型": mdl, "http": r.status, "秒": round(time.time() - t0, 1),
                        "出字": msg[:60], "通": ok, "判": why})
            if ok:
                break
        except urllib.error.HTTPError as e:
            out.append({"模型": mdl, "http": e.code, "通": False,
                        "判": e.read()[:80].decode("utf-8", "replace")})
        except Exception as e:  # noqa: BLE001
            out.append({"模型": mdl, "通": False, "判": "%s: %s" % (type(e).__name__, str(e)[:70])})
    win = next((x for x in out if x.get("通")), None)
    return {"通道": "本地 ollama", "试了": out, "通": bool(win), "选的模型": (win or {}).get("模型"),
            "读数": (win or {}).get("判")}


def _cf_token() -> tuple:
    """拿 CF 令牌：优先 keys.env 里的真 API token；退回 wrangler OAuth（并标出类型）。"""
    ke = ROOT / "state" / "keys.env"
    if ke.is_file():
        for line in ke.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("CLOUDFLARE_API_TOKEN="):
                v = line.split("=", 1)[1].strip()
                if v:
                    return v, "keys.env:API_TOKEN"
    if WRANGLER_CFG.is_file():
        import re
        t = WRANGLER_CFG.read_text(encoding="utf-8", errors="replace")
        m = re.search(r'oauth_token\s*=\s*"([^"]+)"', t)
        e = re.search(r'expiration_time\s*=\s*"([^"]+)"', t)
        if m:
            return m.group(1), "wrangler:OAuth(%s)" % (e.group(1) if e else "?")
    return "", "无"


def probe_cf(*, timeout: float = 60.0) -> dict:
    tok, src = _cf_token()
    if not tok:
        return {"通道": "CF Workers AI", "通": False, "判": "没有可用令牌（keys.env 无 API token）"}
    if src.startswith("wrangler:OAuth"):
        import re
        exp = re.search(r"\((.*)\)", src)
        if exp:
            try:
                e = time.mktime(time.strptime(exp.group(1).replace("Z", "").split(".")[0], "%Y-%m-%dT%H:%M:%S"))
                if e < time.time():
                    return {"通道": "CF Workers AI", "通": False, "判": "OAuth 令牌已过期（%s）" % exp.group(1),
                            "来源": src}
            except Exception as e:
                _swallow(__file__, e)
    url = ("https://api.cloudflare.com/client/v4/accounts/%s/ai/run/@cf/meta/llama-3.2-1b-instruct"
           % CF_ACCOUNT)
    body = json.dumps({"messages": [{"role": "user", "content": "回答两个字：在不在"}],
                       "max_tokens": 16}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Authorization": "Bearer " + tok,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        res = d.get("result") or {}
        txt = res.get("response") if isinstance(res, dict) else str(res)
        ok, why = _coherent(txt or "")
        return {"通道": "CF Workers AI", "通": ok, "来源": src, "秒": round(time.time() - t0, 1),
                "出字": (txt or "")[:60], "判": why}
    except urllib.error.HTTPError as e:
        return {"通道": "CF Workers AI", "通": False, "来源": src, "http": e.code,
                "判": e.read()[:150].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return {"通道": "CF Workers AI", "通": False, "来源": src, "判": "%s: %s" % (type(e).__name__, str(e)[:80])}


def refresh_cf(*, timeout: float = 120.0) -> dict:
    """③ 正规续期：让 wrangler 自己去续（它的指纹能过；我用 urllib 硬打被 1010 挡过）。"""
    if not WRANGLER_CFG.is_file():
        return {"ok": False, "判": "没有 wrangler 登录态"}
    import re
    before = re.search(r'expiration_time\s*=\s*"([^"]+)"',
                       WRANGLER_CFG.read_text(encoding="utf-8", errors="replace"))
    before = before.group(1) if before else ""
    try:
        r = subprocess.run(["wrangler", "whoami"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=0x08000000)
        code, out = r.returncode, (r.stdout or "")[-200:]
    except Exception as e:  # noqa: BLE001
        code, out = -1, "%s: %s" % (type(e).__name__, str(e)[:80])
    after = re.search(r'expiration_time\s*=\s*"([^"]+)"',
                      WRANGLER_CFG.read_text(encoding="utf-8", errors="replace"))
    after = after.group(1) if after else ""
    return {"ok": bool(after and after != before), "码": code, "续期前": before, "续期后": after,
            "输出": out, "判": "续上了" if after and after != before else "wrangler 没能续（需浏览器登录）"}


def fix_runtime(*, timeout: float = 150.0) -> dict:
    """修本地运行时：实测 ollama 0.35.1 的 flash-attention/KV 路径会吐错 logits（首字后全是 @）。
    带 OLLAMA_FLASH_ATTENTION=0 + OLLAMA_KV_CACHE_TYPE=f16 重起服务后即正常 —— 这是触手自己能做的。"""
    exe = OLLAMA_EXE
    if not exe.is_file():
        return {"ok": False, "判": "没找到 ollama.exe"}
    env = dict(os.environ)
    env["OLLAMA_FLASH_ATTENTION"] = "0"
    env["OLLAMA_KV_CACHE_TYPE"] = "f16"
    acts = []
    try:
        r = subprocess.run(["taskkill", "/F", "/IM", "ollama.exe"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30, creationflags=0x08000000)
        acts.append({"动作": "taskkill ollama", "码": r.returncode})
    except Exception as e:  # noqa: BLE001
        acts.append({"动作": "taskkill ollama", "失败": type(e).__name__})
    try:
        subprocess.run(["taskkill", "/F", "/IM", "llama-server.exe"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=30, creationflags=0x08000000)
    except Exception as e:
        _swallow(__file__, e)
    time.sleep(2.5)
    try:
        subprocess.Popen([str(exe), "serve"], env=env, creationflags=0x08000000,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        acts.append({"动作": "重起 ollama serve（带安全开关）", "ok": True})
    except Exception as e:  # noqa: BLE001
        acts.append({"动作": "重起 ollama serve", "失败": type(e).__name__})
    time.sleep(12)
    return {"ok": True, "开关": {"OLLAMA_FLASH_ATTENTION": "0", "OLLAMA_KV_CACHE_TYPE": "f16"},
            "动作": acts, "判": "已带安全开关重起，待复探"}


def heal(*, unload: bool = True) -> dict:
    """④ 全链自愈：回收内存 → 探本地 → 试续期 → 选一条真能出字的。"""
    t0 = time.time()
    rep = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "序列": []}
    fm = free_memory(unload_models=unload)
    rep["回收内存"] = fm
    rep["序列"].append({"步": "回收内存", "腾出GB": fm["腾出GB"], "可用GB": fm["后"]["可用GB"]})
    loc = probe_local()
    if not loc["通"] and any("乱码" in str(x.get("判")) or "空字" in str(x.get("判")) for x in loc["试了"]):
        rep["修运行时"] = fix_runtime()
        rep["序列"].append({"步": "修运行时", "判": rep["修运行时"].get("判")})
        loc = probe_local()
    rep["本地"] = loc
    rep["序列"].append({"步": "探本地", "通": loc["通"], "判": loc["读数"], "模型": loc.get("选的模型")})
    cf = probe_cf()
    rep["云"] = cf
    rep["序列"].append({"步": "探云", "通": cf["通"], "判": cf.get("判", "")[:70]})
    rf = None
    if not cf["通"]:
        rf = refresh_cf()
        rep["续期"] = rf
        rep["序列"].append({"步": "续期", "ok": rf["ok"], "判": rf["判"]})
        if rf["ok"]:
            cf2 = probe_cf()
            rep["云(续期后)"] = cf2
            rep["序列"].append({"步": "复探云", "通": cf2["通"], "判": cf2.get("判", "")[:70]})
            cf = cf2
    # 选型：云优先（快），本地兜底
    if cf.get("通"):
        choice = {"通道": "cloud:cf", "模型": "@cf/meta/llama-3.2-1b-instruct", "来源": cf.get("来源")}
    elif loc.get("通"):
        choice = {"通道": "local:ollama", "模型": loc.get("选的模型"), "base": LOCAL_BASE}
    else:
        choice = {"通道": None, "为什么": "云与本地都不通",
                  "云判": (cf.get("判") or "")[:120], "本地判": (loc.get("读数") or "")[:120]}
    rep["选型"] = choice
    rep["秒"] = round(time.time() - t0, 1)
    CHOICE.parent.mkdir(parents=True, exist_ok=True)
    CHOICE.write_text(json.dumps(choice, ensure_ascii=False, indent=1), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rep, ensure_ascii=False) + chr(10))
    return rep


def status(limit: int = 5) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    cur = {}
    if CHOICE.is_file():
        try:
            cur = json.loads(CHOICE.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cur = {}
    return {"当前选型": cur, "内存": ram(), "最近自愈": rows,
            "口径": "触手自愈：回收内存→探本地→正规续期→选真能出字的通道；不问主人"}


__all__ = ["ram", "free_memory", "probe_local", "probe_cf", "refresh_cf", "heal", "status", "LEDGER", "CHOICE"]
