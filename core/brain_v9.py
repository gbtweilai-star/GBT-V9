# core/brain_v9.py —— 她的脑子（Agnes 云脑优先，失败回落本地 ollama；key 只从 state/keys.env 读）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）给了自定义 provider：Base https://apihub.agnes-ai.com/v1 · 模型 agnes-2.5-flash
# 口径：key **绝不入仓、绝不回显**；只用它说话，不写进任何日志；失败如实回落本地 ollama。
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ROOT / "state" / "keys.env"
LEDGER = ROOT / "state" / "brain_v9.jsonl"


def _env() -> dict:
    out = {}
    if KEYS.is_file():
        for line in KEYS.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                out[k.strip()] = v.strip()
    for k in ("AGNES_API_BASE", "AGNES_API_KEY", "AGNES_MODEL"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    return out


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    safe = {k: v for k, v in rec.items() if "KEY" not in k.upper()}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(safe, ensure_ascii=False) + chr(10))


def agnes(prompt: str, *, system: str = "", timeout: float = 90.0, max_tokens: int = 700) -> dict:
    """Agnes 云脑（OpenAI 兼容）。"""
    e = _env()
    base, key, model = e.get("AGNES_API_BASE"), e.get("AGNES_API_KEY"), e.get("AGNES_MODEL") or "agnes-2.5-flash"
    if not (base and key):
        return {"ok": False, "为什么": "没配 Agnes（state/keys.env 缺 AGNES_API_BASE/AGNES_API_KEY）"}
    msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
    body = json.dumps({"model": model, "messages": msgs, "max_tokens": max_tokens, "temperature": 0.6}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                          "Accept": "application/json", "User-Agent": "GBT-V9/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        txt = (((d.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
        out = {"ok": bool(txt), "后端": "agnes:" + str(d.get("model") or model), "秒": round(time.time() - t0, 2),
               "用量": d.get("usage"), "字": txt}
    except urllib.error.HTTPError as ex:
        out = {"ok": False, "后端": "agnes", "码": ex.code, "错": ex.read(160).decode("utf-8", "replace")[:160]}
    except Exception as ex:  # noqa: BLE001
        out = {"ok": False, "后端": "agnes", "错": "%s: %s" % (type(ex).__name__, str(ex)[:120])}
    _log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "brain.agnes", "ok": out["ok"],
          "后端": out.get("后端"), "秒": out.get("秒"), "用量": out.get("用量")})
    return out


def local_ollama(prompt: str, *, system: str = "", timeout: float = 180.0) -> dict:
    """本地兜底：万能插 model 插座（沙盒里跑，产物带 sha256）。"""
    from core import pulse as P
    full = (system + chr(10) + chr(10) + prompt) if system else prompt
    r = P.plug_and_run("t001", kind="model", args={"prompt": full, "backend": "local", "tentacle": "t001"},
                       timeout=timeout)
    txt = (r.get("出字") or "").strip()
    lines = [x.strip() for x in txt.splitlines()
             if x.strip() and "Thinking" not in x and not x.strip().startswith(("嗯", "好的", "首先", "我需要"))]
    return {"ok": bool(r.get("ok")), "后端": r.get("后端") or "本地ollama", "秒": r.get("秒"),
            "沙盒": r.get("沙盒"), "产物": r.get("产物"), "字": (max(lines, key=len) if lines else txt)[:800]}


def think(prompt: str, *, system: str = "", prefer: str = "agnes") -> dict:
    """说话：默认走 Agnes 云脑；不通就**如实回落**本地 ollama（并记明是谁答的）。"""
    order = ["agnes", "local"] if prefer == "agnes" else ["local", "agnes"]
    last = {}
    for who in order:
        r = agnes(prompt, system=system) if who == "agnes" else local_ollama(prompt, system=system)
        if r.get("ok"):
            r["谁答的"] = who
            return r
        last = r
    return {"ok": False, "为什么": "两条脑都不通", "最后错误": last}


def status() -> dict:
    e = _env()
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-5:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"Agnes": {"配了": bool(e.get("AGNES_API_KEY")), "模型": e.get("AGNES_MODEL"),
                      "入口": e.get("AGNES_API_BASE")},
            "本地兜底": "万能插 model 插座（ollama 11434）", "最近": rows,
            "口径": "key 只从 state/keys.env 读；绝不入仓/不回显；Agnes 不通如实回落并记明谁答的"}


__all__ = ["agnes", "local_ollama", "think", "status", "LEDGER"]
