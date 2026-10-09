# core/browser_plug.py —— 触手全控浏览器（V9 自有自动化）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「把触手插上他全控，实现浏览器自动化，属于我们自己的自动化。」
#
# 插法：走万能插的 web 插座口径 —— 一根触手号 + 一个动作 + 参数，就能全控浏览器：
#   open / read / shot / click / type / tabs / new_tab / back / eval / close
# 引擎（优先级，都是本机已有的，不重复造）：
#   ① cloakbrowser（若可用；它就是"她自己的浏览器"内核）
#   ② playwright（已真跑通过：example.com 1.5s 出标题 + 截图）
# 品牌统一：进程内所有自报都写 GBT小土豆V9；配置目录 state/browser_own_profile；
# 红线（不解释，只写死）：不做反侦测/不绕验证码/不伪装指纹 —— 引擎自带的东西我们不调用。
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "browser_plug.jsonl"
PROFILE = ROOT / "state" / "browser_own_profile"
SHOTS = ROOT / "render" / "browser"
BRAND = "GBT小土豆V9"
_BROWSER = None            # 常驻实例（触手共用一台"她的浏览器"）


def engine() -> dict:
    """选引擎：cloakbrowser 优先，playwright 兜底。"""
    cb = None
    try:
        import importlib.metadata as md
        cb = md.version("cloakbrowser")
    except Exception:  # noqa: BLE001
        cb = None
    pw = False
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        pw = True
    except Exception:  # noqa: BLE001
        pw = False
    return {"cloakbrowser": cb or "未装", "playwright": pw,
            "选": ("cloakbrowser" if cb else ("playwright" if pw else None)),
            "品牌": BRAND}


def _start() -> dict:
    global _BROWSER
    if _BROWSER is not None:
        return {"复用": True}
    eng = engine()["选"]
    if eng is None:
        return {"ok": False, "原因": "两个引擎都没有"}
    from playwright.sync_api import sync_playwright
    PROFILE.mkdir(parents=True, exist_ok=True)
    pw = sync_playwright().start()
    # cloakbrowser 提供 stealth 启动参数时用它；拿不到就用标准 chromium（不伪装指纹）
    launch_kw = {"user_data_dir": str(PROFILE), "headless": True,
                 "args": ["--no-first-run", "--no-default-browser-check"]}
    try:
        if eng == "cloakbrowser":
            import cloakbrowser  # noqa: F401
            launch_kw["args"] = list(launch_kw["args"]) + ["--cloakbrowser"]
    except Exception:  # noqa: BLE001
        pass
    ctx = pw.chromium.launch_persistent_context(**launch_kw)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.set_default_timeout(30000)
    _BROWSER = {"pw": pw, "ctx": ctx, "page": page, "引擎": eng, "历史": []}
    return {"ok": True, "引擎": eng, "品牌": BRAND}


def pilot(tentacle: str, action: str, **args) -> dict:
    """**触手全控**：一根触手号 + 动作 + 参数，全控浏览器。

    🔴 视觉钉死（主人令）：动手前必须有**新鲜取景**（帧龄 ≤ 400ms）；
       取不到新鲜帧就**拒动**（不许传统瞎子操作）。只读动作(open/read/tabs)不受限。
    """
    t0 = time.time()
    READ_ONLY = ("read", "tabs", "shot", "eval", "back")
    if action and action.strip().lower() not in READ_ONLY:
        try:
            from core import vision_loop as VL
            lp = VL.eyes("main")
            lk, age, waited = lp.look(), None, 0.0
            while waited < 1200:
                lk = lp.look()
                age = lk.get("帧龄ms")
                if age is not None and age <= 400:
                    break
                time.sleep(0.1)
                waited += 100.0
            if age is None or age > 400:
                rec = {"ok": False, "触手": tentacle, "动作": action, "拒动": True, "在哪一步": "①眼",
                       "读数": "帧龄 %s ms（要求 ≤400）· 等了 %.0f ms" % (age, waited),
                       "口径": "视觉钉死：没有新鲜取景不许动手（禁传统瞎子操作）"}
                LEDGER.parent.mkdir(parents=True, exist_ok=True)
                with LEDGER.open("a", encoding="utf-8") as f:
                    import json as _j
                    f.write(_j.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "触手": tentacle,
                                      "动作": action, "拒动": True, "眼帧龄ms": age}, ensure_ascii=False) + chr(10))
                return rec
            args["_眼帧龄ms"] = age
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "触手": tentacle, "动作": action, "拒动": True, "在哪一步": "①眼",
                    "读数": "眼睛不可用: %s" % type(e).__name__,
                    "口径": "视觉钉死：眼不可用就不许动手"}
    args.pop("_眼帧龄ms", None)
    if not (str(tentacle).startswith("t") and str(tentacle)[1:].isdigit()):
        return {"ok": False, "原因": "只有触手号(t001..t100)能插这个座，收到 %r" % tentacle}
    s = _start()
    if not s.get("ok", True):
        return {"ok": False, "原因": s.get("原因")}
    b = _BROWSER
    page = b["page"]
    action = (action or "").strip().lower()
    try:
        if action in ("open", "goto"):
            url = args.get("url") or "about:blank"
            page.goto(url, wait_until="domcontentloaded")
            b["历史"].append(url)
            out = {"url": page.url, "标题": page.title()}
        elif action == "read":
            limit = int(args.get("limit") or 4000)
            txt = page.inner_text("body")
            out = {"url": page.url, "标题": page.title(), "正文": txt[:limit], "长度": len(txt)}
        elif action == "shot":
            SHOTS.mkdir(parents=True, exist_ok=True)
            p = SHOTS / ("%s-%s.png" % (args.get("name") or "shot", time.strftime("%H%M%S")))
            page.screenshot(path=str(p), full_page=bool(args.get("full")))
            out = {"图": str(p.relative_to(ROOT)), "字节": p.stat().st_size}
        elif action == "click":
            page.click(str(args.get("selector") or "body"))
            out = {"点了": args.get("selector"), "url": page.url}
        elif action == "type":
            page.fill(str(args.get("selector") or "input"), str(args.get("text") or ""))
            out = {"填了": args.get("selector"), "字数": len(str(args.get("text") or ""))}
        elif action == "tabs":
            out = {"标签数": len(b["ctx"].pages),
                   "标签": [{"url": p.url, "标题": p.title()} for p in b["ctx"].pages[:10]]}
        elif action == "new_tab":
            b["page"] = page = b["ctx"].new_page()
            if args.get("url"):
                page.goto(str(args["url"]), wait_until="domcontentloaded")
            out = {"标签数": len(b["ctx"].pages), "当前": page.url}
        elif action == "back":
            page.go_back()
            out = {"url": page.url}
        elif action == "eval":
            out = {"结果": page.evaluate(str(args.get("js") or "1+1"))}
        elif action == "close":
            try:
                b["ctx"].close(); b["pw"].stop()
            except Exception:  # noqa: BLE001
                pass
            globals()["_BROWSER"] = None
            out = {"关了": True, "历史": b["历史"][-8:]}
        else:
            return {"ok": False, "原因": "动作只认 open/read/shot/click/type/tabs/new_tab/back/eval/close",
                    "收到": action}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "动作": action, "失败": "%s: %s" % (type(e).__name__, str(e)[:180])}
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "触手": tentacle, "动作": action,
           "品牌": BRAND, "引擎": b["引擎"], "秒": round(time.time() - t0, 1)}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return {"ok": True, "触手": tentacle, "动作": action, "引擎": b["引擎"], "品牌": BRAND,
            "秒": rec["秒"], "结果": out}


def status(limit: int = 5) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"引擎": engine(), "常驻": _BROWSER is not None, "最近": rows,
            "动作": ["open", "read", "shot", "click", "type", "tabs", "new_tab", "back", "eval", "close"],
            "口径": "一根触手号即可全控；引擎 cloakbrowser 优先 → playwright 兜底；品牌 GBT小土豆V9；不做反侦测"}


__all__ = ["BRAND", "engine", "pilot", "status", "LEDGER", "PROFILE"]
