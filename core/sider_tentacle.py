# core/sider_tentacle.py —— 触手直插 Sider 工作树（挂她的扩展 service worker，用她自己的权限说话）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「你有触手啊，直接使用触手插进去就好了」+「站在收费站外面」。
# 口径：**不碰网页、不过 Cloudflare、不解密密钥库**。触手走的是 Chrome 调试协议（CDP），
#   挂住她扩展的 **service worker 目标**，在那个**她自己的执行环境**里：
#     ① 读她自己的 chrome.storage（拿会话/设置；不是解 Chrome 的加密库）
#     ② 用她自己的权限 fetch 她的 /v1 API（扩展上下文发请求，CF 不挡）
#     ③ 在她环境里驱动她的功能 ⇒ 这就是"并一条线、把她盖住"
# 前置：她自己的 Chrome 要带 --remote-debugging-port=9222（她重启一次即可，凭据不出她机器）
from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXT_ID = "difoiogjjojoaoomphldepapgpbgkhkb"
LEDGER = ROOT / "state" / "sider_tentacle.jsonl"
API = "https://preview.sider.ai"


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def cdp_up(port: int = 9222, timeout: float = 2.0) -> dict:
    """她自己的 Chrome 有没有开调试口。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/version" % port, timeout=timeout) as r:
            return {"ok": True, "浏览器": json.loads(r.read().decode("utf-8", "replace")).get("Browser"),
                    "端口": port}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "端口": port, "原因": type(e).__name__}


def targets(port: int = 9222) -> list:
    """列出她 Chrome 里所有可挂目标（含扩展 service worker）。"""
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=6) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001
        return []


def find_extension_worker(port: int = 9222) -> dict:
    """找她扩展的 service worker 目标 —— 这就是触手的插点。"""
    ts = targets(port)
    for t in ts:
        u = str(t.get("url") or "")
        if EXT_ID in u and t.get("type") in ("service_worker", "background_page", "page", "worker"):
            return {"找到": True, "类型": t.get("type"), "url": u[:120], "id": t.get("id"),
                    "title": t.get("title")}
    return {"找到": False, "看过目标数": len(ts),
            "扩展相关": [str(t.get("url"))[:90] for t in ts if EXT_ID in str(t.get("url"))][:4]}


def attach_eval(expr: str, *, port: int = 9222) -> dict:
    """**触手插进去**：在她扩展的 service worker 里执行 JS 并取回结果。

    用 playwright 的 ctx.service_workers（扩展的活体线程）—— 这是"站在收费站外面"的正门：
    值在她的执行环境里算出来，我们不碰她的磁盘密钥库，也不碰被 CF 挡的网页。
    """
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % port)
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        cands = []
        for attr in ("service_workers", "background_pages"):
            try:
                cands += list(getattr(ctx, attr) or [])
            except Exception:  # noqa: BLE001
                continue
        hit = next((w for w in cands if EXT_ID in str(getattr(w, "url", ""))), None)
        if hit is None:
            return {"ok": False, "原因": "没挂到她的 service worker",
                    "看过": [str(getattr(w, "url", ""))[:80] for w in cands][:6]}
        val = hit.evaluate(expr)
        return {"ok": True, "插点": str(getattr(hit, "url", ""))[:90],
                "值": json.dumps(val, ensure_ascii=False)[:800] if isinstance(val, (dict, list)) else str(val)[:800]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "原因": "%s: %s" % (type(e).__name__, str(e)[:160])}
    finally:
        try:
            pw.stop()
        except Exception:  # noqa: BLE001
            pass


def status(*, port: int = 9222) -> dict:
    up = cdp_up(port)
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "sider.tentacle",
           "调试口": up, "扩展": EXT_ID, "API": API,
           "口径": "触手挂她的 service worker；不碰网页/不过CF/不解密密钥库"}
    if up.get("ok"):
        out["插点"] = find_extension_worker(port)
    else:
        out["怎么开"] = ('& "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
                        '--remote-debugging-port=9222 --profile-directory="Profile 2"')
    _log({k: out[k] for k in ("at", "抓")} | {"调试口在": up.get("ok")})
    return out


__all__ = ["EXT_ID", "API", "cdp_up", "targets", "find_extension_worker", "attach_eval", "status", "LEDGER"]
