# core/native_browser.py —— 她自己的原生浏览器（V9 自有实例 + 自有配置目录）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「把她自己的原生浏览器内镶嵌，你也没做是吗。」
# 口径（与红线一起写死）：
#   ✅ **自有浏览器**：V9 自己起一个 Chromium 实例，配置目录归她（state/browser_own_profile），
#      不劫持主人的 Chrome、不动主人的登录态；能开页、读正文、截图、点、输入、多标签、关。
#   ❌ **不做反侦测/绕过验证码/伪装指纹**：那是「无视反机器人」，本仓明确不做。
#      本模块只把 user-agent 设成固定且可读的值（诚实声明自己是自动化），不伪装。
import json
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "state" / "browser_own_profile"      # 她自己的浏览器数据目录
SHOTS = ROOT / "render" / "browser"
HONEST_UA = "GBT-V9-native-browser/1.0 (+automation; honest)"


def available() -> dict:
    try:
        import playwright  # noqa: F401
        pw = True
    except Exception:  # noqa: BLE001
        pw = False
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        api = True
    except Exception:  # noqa: BLE001
        api = False
    browsers = None
    try:
        from playwright._impl._driver import compute_driver_executable  # noqa: F401
        browsers = "driver 在"
    except Exception:  # noqa: BLE001
        browsers = None
    return {"playwright": pw, "sync_api": api, "driver": browsers,
            "配置目录": str(PROFILE.relative_to(ROOT)),
            "安装命令": "python -m playwright install chromium"}


class NativeBrowser:
    """她自己的浏览器：启动/开页/读文/截图/点击/输入/标签/关闭。"""

    def __init__(self, *, headless: bool = True, timeout: float = 30.0):
        self.headless = headless
        self.timeout = timeout * 1000
        self._pw = None
        self.ctx = None
        self.page = None
        self.history = []

    def start(self) -> dict:
        from playwright.sync_api import sync_playwright
        PROFILE.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self.ctx = self._pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE), headless=self.headless,
            args=["--no-first-run", "--no-default-browser-check"],
            user_agent=HONEST_UA)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.set_default_timeout(self.timeout)
        return {"ok": True, "她的浏览器": "已启动", "headless": self.headless,
                "配置目录": str(PROFILE.relative_to(ROOT)), "身份声明": HONEST_UA,
                "红线": "不做反侦测/不绕验证码/不伪装指纹"}

    def goto(self, url: str) -> dict:
        if self.page is None:
            self.start()
        t0 = time.time()
        self.page.goto(url, wait_until="domcontentloaded")
        self.history.append(url)
        return {"ok": True, "url": self.page.url, "标题": self.page.title(),
                "秒": round(time.time() - t0, 1)}

    def read(self, *, limit: int = 4000) -> dict:
        if self.page is None:
            return {"ok": False, "原因": "还没开页"}
        txt = self.page.inner_text("body")
        return {"ok": True, "标题": self.page.title(), "url": self.page.url,
                "正文前 %d 字" % limit: txt[:limit], "正文长度": len(txt)}

    def shot(self, name: str = "page") -> dict:
        SHOTS.mkdir(parents=True, exist_ok=True)
        p = SHOTS / ("%s-%s.png" % (name, time.strftime("%H%M%S")))
        self.page.screenshot(path=str(p), full_page=False)
        return {"ok": True, "图": str(p.relative_to(ROOT)), "字节": p.stat().st_size}

    def click(self, selector: str) -> dict:
        # 视觉钉死：动手前必须有新鲜取景（主人令：禁传统瞎子操作）
        from core.senses_gate import require_eye as _re
        _eye = _re()
        if not _eye.get("ok"):
            return {"ok": False, "拒动": True, "在哪一步": "①眼", "读数": _eye}
        self.page.click(selector)
        return {"ok": True, "点了": selector, "现在 url": self.page.url}

    def type(self, selector: str, text: str) -> dict:
        # 视觉钉死：动手前必须有新鲜取景（主人令：禁传统瞎子操作）
        from core.senses_gate import require_eye as _re
        _eye = _re()
        if not _eye.get("ok"):
            return {"ok": False, "拒动": True, "在哪一步": "①眼", "读数": _eye}
        self.page.fill(selector, text)
        return {"ok": True, "填了": selector, "字数": len(text)}

    def tabs(self) -> dict:
        return {"ok": True, "标签数": len(self.ctx.pages),
                "标签": [{"url": p.url, "标题": p.title()} for p in self.ctx.pages[:10]]}

    def new_tab(self, url: str = "about:blank") -> dict:
        self.page = self.ctx.new_page()
        if url and url != "about:blank":
            self.page.goto(url, wait_until="domcontentloaded")
        return {"ok": True, "标签数": len(self.ctx.pages), "当前": self.page.url}

    def close(self) -> dict:
        try:
            if self.ctx:
                self.ctx.close()
            if self._pw:
                self._pw.stop()
        except Exception:  # noqa: BLE001 as _e_swallow
            _swallow(__file__, _e_swallow)
            pass
        return {"ok": True, "历史": self.history[-10:]}


def smoke(url: str = "https://example.com") -> dict:
    """真跑一次：起她自己的浏览器 → 开页 → 读正文 → 截图 → 关。"""
    av = available()
    if not (av["playwright"] and av["sync_api"]):
        return {"ok": False, "原因": "playwright 没装", "安装命令": av["安装命令"], "可用性": av}
    b = NativeBrowser(headless=True)
    try:
        s = b.start()
        g = b.goto(url)
        r = b.read(limit=300)
        sh = b.shot("smoke")
        t = b.tabs()
        return {"ok": True, "启动": s, "开页": g, "读": {"标题": r["标题"], "正文前 120": r.get("正文前 300", "")[:120]},
                "截图": sh, "标签": t}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "失败": "%s: %s" % (type(e).__name__, str(e)[:200]),
                "提示": "若报 Executable doesn't exist ⇒ 跑 python -m playwright install chromium"}
    finally:
        b.close()


def status() -> dict:
    return {"可用性": available(), "配置目录在": PROFILE.is_dir(),
            "截图目录": str(SHOTS.relative_to(ROOT)) if SHOTS.is_dir() else "还没截过",
            "口径": "她自己的浏览器（V9 自有 profile，不劫持主人的 Chrome）；不做反侦测/不绕验证码"}


__all__ = ["NativeBrowser", "available", "smoke", "status", "PROFILE", "HONEST_UA"]
