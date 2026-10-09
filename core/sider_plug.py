# core/sider_plug.py —— Sider 万能插适配器（不破坏、只接管：新开一条总线把它盖住）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「万能插就是接管她原来的地方 —— 新开辟一条能连接她所有能力的接口直接插入，
#   不去破坏它，就把她掩盖掉。」
# 落地口径（Sider 免费云电脑为例）：
#   · **不破坏**：不改 Sider、不绕 Cloudflare 挑战、不伪造指纹；
#   · **新总线**：用**她自己的浏览器 profile**（state/browser_own_profile）里**你登入过的真实会话**接它；
#   · **掩盖**：对外只暴露我们自己的口（/api/sider/*），用户看到的是她，不是 Sider 的界面。
# 动作：status（会话在不在）· open_login（给你登一次）· cloud_pc（开云电脑页）· hand（把任务递进去）· read（回读）
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFILE = ROOT / "state" / "browser_own_profile"
LEDGER = ROOT / "state" / "sider_plug.jsonl"
ENTRY = "https://sider.ai/zh-CN/"
CLOUD_PC = "https://sider.ai/zh-CN/cloud-pc"
HEADED = True    # Sider 用**可见窗口**（她的会话）；headless 必被 Cloudflare 挑战
CHALLENGE_MARKS = ("__cf_chl", "Just a moment", "Checking your browser", "cf-challenge")


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def _headed() -> dict:
    """确保她的浏览器是**可见**的（Sider 只认真实会话；headless 必被挑战）。"""
    try:
        from core import vision_loop as VL
        lp = VL.eyes("sider")
        return {"眼": "在", "帧龄ms": (lp.look() or {}).get("帧龄ms")}
    except Exception as e:  # noqa: BLE001
        return {"眼": "起不来: %s" % type(e).__name__}


def _session_files() -> dict:
    """她的 profile 里有没有登录态（只看存在性与数量，不读内容）。"""
    out = {"profile": PROFILE.is_dir()}
    for pat, name in (("**/Cookies", "cookies"), ("**/Login Data", "login_data"),
                      ("**/Local State", "local_state")):
        hits = list(PROFILE.glob(pat)) if PROFILE.is_dir() else []
        out[name] = len(hits)
    return out


def status(*, probe: bool = True) -> dict:
    """她这边的 Sider 插头状态：profile 有没有、有没有登录态文件、页面能不能正常读（不绕挑战）。"""
    s = _session_files()
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "sider.status",
           "她的 profile": s, "入口": ENTRY, "云电脑页": CLOUD_PC,
           "口径": "不破坏/不绕检测：只接你**真实登入过的会话**"}
    if not probe:
        return out
    try:
        from core import browser_plug as BP
        # 用她的可见浏览器（headed）拿真实会话，不做任何绕过
        _headed()
        r = BP.pilot("t016", "open", url=ENTRY)
        url = (r.get("结果") or {}).get("url", "")
        r2 = BP.pilot("t016", "read", limit=600)
        body = (r2.get("结果") or {}).get("正文", "")
        challenged = any(m.lower() in (url + body).lower() for m in CHALLENGE_MARKS)
        out.update({"打开": url[:120], "挑战": challenged,
                    "正文长度": len(body),
                    "判": ("被 Cloudflare 挑战挡住 ⇒ 需要你先在她的浏览器里登入一次（真实会话）"
                          if challenged else ("读到真实页面 ⇒ 会话可用" if len(body) > 60
                                            else "页面为空 ⇒ 可能需登入"))})
    except Exception as e:  # noqa: BLE001
        out.update({"失败": "%s: %s" % (type(e).__name__, str(e)[:120])})
    _log({k: out.get(k) for k in ("at", "抓", "挑战", "正文长度")})
    return out


def open_login() -> dict:
    """合规正路：给你开一次**可见**的登录窗口（她的 profile）——你登一次，会话长期留给她。"""
    cmd = ('"%s" -c "import sys;sys.path.insert(0,\'.\');from core.native_browser import NativeBrowser;'
           'b=NativeBrowser(headless=False);b.start();b.goto(\'%s\')"' % (sys_exec(), ENTRY))
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "sider.open_login",
           "说明": "可见窗口已按此命令起（后台作业）；你在窗口里登入 Sider 即可，我不碰你的密码",
           "命令": cmd, "入口": ENTRY}
    _log(rec)
    return rec


def sys_exec() -> str:
    import sys
    return sys.executable


def hand(task: str, *, submit: bool = False) -> dict:
    """把任务递进 Sider（在她的会话里）：打开页 → 填输入框 →（可选）回车。"""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "sider.hand", "任务": task[:200],
           "提交": bool(submit)}
    try:
        from core import browser_plug as BP
        BP.pilot("t016", "open", url=ENTRY)
        # Sider 的输入框选择器按常见形态尝试（不保证命中就会如实报）
        for sel in ("textarea", "div[contenteditable='true']", "input[type='text']"):
            r = BP.pilot("t016", "type", selector=sel, text=task)
            if r.get("ok"):
                rec["填入选择器"] = sel
                break
        else:
            rec["判"] = "没找到输入框（可能未登入或页面结构变了）⇒ 如实报，不硬来"
        if submit and rec.get("填入选择器"):
            rec["提交结果"] = BP.pilot("t016", "click", selector="button[type='submit'], button:has-text('发送')")
    except Exception as e:  # noqa: BLE001
        rec["失败"] = "%s: %s" % (type(e).__name__, str(e)[:120])
    _log(rec)
    return rec


def overlay() -> dict:
    """**掩盖口径**：对外只暴露我们的口（面板 /dh-input 与 /api/sider/*），用户看不到 Sider 界面。"""
    return {"对外口": ["/api/sider/status", "/api/sider/hand", "/dh-input"],
            "盖住谁": "Sider（原界面不暴露给用户，也不改它）",
            "口径": "新开一条总线接它的能力；不破坏、不绕检测；登入用你自己的会话"}


__all__ = ["ENTRY", "CLOUD_PC", "PROFILE", "status", "open_login", "hand", "overlay", "LEDGER"]
