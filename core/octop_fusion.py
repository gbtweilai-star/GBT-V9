# core/octop_fusion.py —— V9 ⇄ Octop 深度融合（原生页面全连接 · 品牌统一 · V9 可操控整个 APP）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：
#   ① 不是排除 Octop，是**完美集成能力融合**；
#   ② Octop 自身的能力页面**一个都不能丢**，全部连接上；
#   ③ **品牌统一**；
#   ④ GBT小土豆V9 可**操控整个 APP**；
#   ⑤ 做好**固化和回滚保护**。
#
# 做法（都不玩文字）：
#   · 页面清单从 Octop 自己的前端包里**读真实路由**（64 条），在 V9 站内给出分类索引；
#     页面用**浏览器侧内嵌**（iframe 指向本机 Octop），因此不经我们服务器转发 → 无 SSRF 面、页面零丢失；
#   · 品牌统一：V9 外层品牌头 + 传给 Octop 的品牌参数；Seed 插件让 Octop 侧也显示 V9 品牌；
#   · 可操控：起/停/重启 Octop 底座（用安装目录里的便携运行时，参数列表 spawn，无 shell）；
#   · 固化：页面清单 + 控制配置进 core.solidify（可回滚），每次动作进 core.deploy_ledger。
import json
import os
import socket
import sqlite3
import subprocess
import time
from pathlib import Path

from common.ttl_cache import TTLCache as _TTL

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PANEL_DB = ROOT.joinpath("data", "gbt_v9.sqlite3")
OCTOP_HOST = "127.0.0.1"
OCTOP_PORT = int(os.environ.get("OCTOP_PORT", "8766"))
# 端口是**活的**：桌面 APP 的 findFreePort 会从 8766 起挑空位（真机上就落在过 8767）。
# 写死端口 = 融合会显示"离线"（明明在线）。所以这里按候选表自动发现当前底座端口。
OCTOP_PORT_CANDIDATES = tuple(int(x) for x in os.environ.get(
    "OCTOP_PORTS", "8766,8767,8768,8769,8770").split(",") if x.strip())
# 便携运行时（桌面 APP 自带的那套；路径只从环境变量或已知安装位取，不写死凭据）
BUNDLED = Path(os.environ.get(
    "V9_OCTOP_HOME",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "GBT小土豆V9"
        / "resources" / "octop" / "portable")))
LAUNCH = BUNDLED.joinpath("launch.py")
RUNTIME = BUNDLED.joinpath("runtime").joinpath("python.exe")


# ═══════════ ① Octop 原生页面（从它的前端包读出来的真实路由，一个不丢）═══════════
PAGES: tuple = (
    ("dashboard", "总览面板", "/", "首页"),
    ("chat", "对话 / 会话", "/chat", "交互"),
    ("workbench", "工作台", "/workbench", "交互"),
    ("workbench-browser", "工作台 · 浏览器", "/workbench/browser", "交互"),
    ("workbench-terminal", "工作台 · 终端", "/workbench/terminal", "交互"),
    ("sessions", "会话记录", "/sessions", "交互"),
    ("tasks", "任务", "/tasks", "交互"),
    ("acp", "Agent 协作协议（ACP）", "/acp", "智能体"),
    ("agents-admin", "智能体管理", "/admin/agents", "智能体"),
    ("agent-config", "智能体配置", "/agent-config", "智能体"),
    ("subagents", "子智能体", "/subagents", "智能体"),
    ("experts", "专家库", "/experts", "智能体"),
    ("skills", "技能", "/skills", "能力"),
    ("skill-packages", "技能包", "/skill-packages", "能力"),
    ("plugins", "插件市场", "/plugins", "能力"),
    ("models", "模型", "/models", "能力"),
    ("knowledge-bases", "知识库", "/knowledge-bases", "能力"),
    ("memory", "记忆", "/memory", "能力"),
    ("connectors", "连接器", "/connectors", "能力"),
    ("channels", "渠道", "/channels", "能力"),
    ("environments", "环境", "/environments", "运行"),
    ("terminal", "终端", "/terminal", "运行"),
    ("workspace", "工作区", "/workspace", "运行"),
    ("cron-jobs", "定时任务", "/cron-jobs", "运行"),
    ("remote-desktop", "远程桌面", "/remote-desktop", "远控"),
    ("remote-desktop-desktop", "远控 · 桌面", "/remote-desktop/desktop", "远控"),
    ("remote-desktop-phone", "远控 · 手机", "/remote-desktop/phone", "远控"),
    ("remote-browser", "远控 · 浏览器", "/remote-browser", "远控"),
    ("remote-android", "远控 · 安卓", "/remote-android", "远控"),
    ("remote-phone", "远控 · 电话", "/remote-phone", "远控"),
    ("token-usage", "Token 用量", "/token-usage", "计量"),
    ("mbti", "MBTI", "/mbti", "个性"),
    ("personalization-skills", "个性化 · 技能", "/personalization/skills", "个性"),
    ("personalization-channels", "个性化 · 渠道", "/personalization/channels", "个性"),
    ("personalization-acp", "个性化 · ACP", "/personalization/acp", "个性"),
    ("admin-advanced", "管理 · 高级", "/admin/advanced", "管理"),
    ("admin-backend", "管理 · 后端", "/admin/backend", "管理"),
    ("admin-models", "管理 · 模型", "/admin/models", "管理"),
    ("admin-plugins", "管理 · 插件", "/admin/plugins", "管理"),
    ("admin-security", "管理 · 安全", "/admin/security", "管理"),
    ("admin-shared-models", "管理 · 共享模型", "/admin/shared-models", "管理"),
    ("admin-sso", "管理 · 单点登录", "/admin/sso", "管理"),
    ("admin-storage", "管理 · 存储", "/admin/storage", "管理"),
    ("admin-users", "管理 · 用户", "/admin/users", "管理"),
    ("admin-audit", "管理 · 审计", "/admin/audit", "管理"),
    ("admin-updates", "管理 · 更新", "/admin/updates", "管理"),
    ("admin-voice", "管理 · 语音", "/admin/voice", "管理"),
    ("advanced-settings", "高级设置", "/advanced-settings", "设置"),
    ("updates", "更新", "/updates", "设置"),
    ("setup", "安装向导", "/setup", "设置"),
    ("login", "登录", "/login", "设置"),
    ("login-oidc", "登录 · OIDC 回调", "/login/oidc/complete", "设置"),
    ("invite", "邀请", "/invite", "设置"),
    ("pwa-debug", "PWA 调试", "/pwa-debug", "调试"),
    # 多品牌/别名路由（Octop 自身也带 octop/orca 前缀的同一批页面）——一个都不丢
    ("octop-admin-audit", "Octop 品牌 · 审计", "/octop/admin/audit", "品牌别名"),
    ("octop-admin-users", "Octop 品牌 · 用户", "/octop/admin/users", "品牌别名"),
    ("octop-channels", "Octop 品牌 · 渠道", "/octop/channels", "品牌别名"),
    ("octop-cron", "Octop 品牌 · 定时", "/octop/cron", "品牌别名"),
    ("orca-admin-audit", "Orca 品牌 · 审计", "/orca/admin/audit", "品牌别名"),
    ("orca-admin-users", "Orca 品牌 · 用户", "/orca/admin/users", "品牌别名"),
    ("orca-channels", "Orca 品牌 · 渠道", "/orca/channels", "品牌别名"),
    ("orca-cron", "Orca 品牌 · 定时", "/orca/cron", "品牌别名"),
    ("remote-desktop-phone-screen", "远控 · 手机屏", "/remote-desktop/phone/screen", "远控"),
    ("remote-desktop-phone-shell", "远控 · 手机壳层", "/remote-desktop/phone/shell", "远控"),
)


def pages() -> dict:
    return {p[0]: {"id": p[0], "标题": p[1], "octop路径": p[2], "分组": p[3]}
            for p in PAGES}


def group_names() -> list:
    seen = []
    for _, _, _, g in PAGES:
        if g not in seen:
            seen.append(g)
    return seen


# ═══════════ ② 真实探活（只读本机 Octop；字面量地址，不做动态拼接）═══════════
def _port_open(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.2)
            return s.connect_ex((OCTOP_HOST, int(port))) == 0
    except OSError:
        return False


def port_up() -> bool:
    return _port_open(active_port())


_PORT_TTL = float(os.environ.get("V9_OCTOP_PORT_TTL", "20"))
_PORT_CACHE = _TTL(ttl=_PORT_TTL, name="octop_port")
_HEALTH_CACHE = _TTL(ttl=float(os.environ.get("V9_OCTOP_HEALTH_TTL", "20")), name="octop_health")
_STATUS_CACHE = _TTL(ttl=float(os.environ.get("V9_OCTOP_STATUS_TTL", "30")), name="octop_status")


def active_port() -> int:
    """当前 Octop 底座真正在听的端口（候选表里第一个通健康检查的）。

    真机踩过：一次 status() 会问三遍端口，而每次探测都要 1.2s 超时 + 一次 2.5s
    健康请求 —— 叠起来就是"打开 Octop 页要等 20 秒"。端口不会毫秒级变，短 TTL 记住即可。
    """
    return _PORT_CACHE.get("p", _probe_port)


def _probe_port() -> int:
    for cand in OCTOP_PORT_CANDIDATES:
        if not _port_open(cand):
            continue
        try:
            import httpx
            r = httpx.get(f"http://{OCTOP_HOST}:{cand}/api/health", timeout=2.5)
            if r.status_code == 200 and (r.json() or {}).get("ok"):
                return cand
            return cand                              # 端口通、健康非 ok：也认它是底座
        except Exception:                            # noqa: BLE001
            return cand
    return OCTOP_PORT                                # 都没通 → 用默认（供 start 用）


def health() -> dict:
    """读 Octop 自己的健康接口（真数字：agents_running / db / users_loaded）。带短缓存。"""
    return _HEALTH_CACHE.get("v", _health_probe)


def _health_probe() -> dict:
    port = active_port()
    if not _port_open(port):
        return {"ok": False, "port": port, "reason": f"端口 {port} 未监听（候选 "
                                                     f"{list(OCTOP_PORT_CANDIDATES)}）"}
    try:
        import httpx
        r = httpx.get(f"http://{OCTOP_HOST}:{port}/api/health", timeout=5.0)
        doc = r.json()
        return {"ok": bool(doc.get("ok")), "port": port,
                "agents_running": doc.get("agents_running"), "db": doc.get("db"),
                "users_loaded": doc.get("users_loaded"), "started_at": doc.get("started_at"),
                "reason": ""}
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "port": port, "reason": f"{type(exc).__name__}"}


# ═══════════ ③ 双向绑定与品牌状态（读真表）═══════════
def _bind_counts() -> dict:
    out = {"能力对数": None, "触手数": None, "能力数": None, "reason": ""}
    if not PANEL_DB.is_file():
        return {**out, "reason": "面板库不存在"}
    try:
        con = sqlite3.connect(f"file:{PANEL_DB}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT COUNT(DISTINCT tentacle||'|'||capability) FROM octop_binding WHERE direction='c2t'"
            ).fetchone()
            out["能力对数"] = row[0] if row else None
            row2 = con.execute(
                "SELECT COUNT(DISTINCT tentacle) FROM octop_binding WHERE direction='c2t'"
            ).fetchone()
            out["触手数"] = row2[0] if row2 else None
            row3 = con.execute(
                "SELECT COUNT(DISTINCT capability) FROM octop_binding WHERE direction='c2t'"
            ).fetchone()
            out["能力数"] = row3[0] if row3 else None
        finally:
            con.close()
    except Exception as exc:                                  # noqa: BLE001
        out["reason"] = type(exc).__name__
    return out


def brand_state() -> dict:
    """品牌统一状态：V9 是否在 Octop 侧注册了插件（seed）+ 是否带自家图标。"""
    seed = Path(os.environ.get(
        "V9_OCTOP_HOME",
        str(Path(os.environ.get("APPDATA", "")) / "GBT小土豆V9" / "octop-home")))
    plug = seed.joinpath("plugins").joinpath("gbt-potato-v9")
    icon = plug.joinpath("icon.svg")
    manifest = plug.joinpath("plugin.yaml")
    ver = ""
    if manifest.is_file():
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    ver = line.split(":", 1)[1].strip()
                    break
        except OSError:
            ver = ""
    return {"插件目录": str(plug), "已注册": plug.is_dir(),
            "图标就位": icon.is_file(), "版本": ver,
            "说明": "V9 以插件形式挂在 Octop 上（Octop 侧也能看到 V9 品牌与能力）"}


# ═══════════ ④ V9 操控整个 APP（启/停/重启 Octop 底座）═══════════
def control_available() -> dict:
    return {"运行时就位": RUNTIME.is_file(), "启动脚本就位": LAUNCH.is_file(),
            "runtime": str(RUNTIME), "launch": str(LAUNCH),
            "可操控": RUNTIME.is_file() and LAUNCH.is_file()}


def control(action: str = "status") -> dict:
    """对 Octop 底座执行动作：status / start / restart / stop。参数列表 spawn（无 shell）。"""
    from core import deploy_ledger as J
    act = str(action or "status").lower()
    if act not in ("status", "start", "restart", "stop"):
        return {"ok": False, "reason": f"未知动作：{action}（status/start/restart/stop）"}
    av = control_available()
    if act == "status":
        h = health()
        r = {"ok": True, "action": act, "在线": port_up(), "端口": active_port(),
             "健康": h, "可用性": av}
        J.record("deploy", f"octop_control:{act}", detail={"在线": r["在线"], "健康": h})
        return r
    if not av["可操控"]:
        return {"ok": False, "action": act, "reason": "便携运行时缺失，无法操控",
                "可用性": av}
    if act in ("restart", "stop"):
        port = active_port()
        if act == "stop":
            pass
        # 停掉占用该端口的进程（只按端口号定位，按参数列表调 PowerShell）
        if _port_open(port):
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command",
                                f"Get-NetTCPConnection -LocalPort {port} "
                                f"-State Listen -ErrorAction SilentlyContinue | "
                                f"ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force }}"],
                               capture_output=True, timeout=30,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as exc:                          # noqa: BLE001
                return {"ok": False, "action": act, "reason": f"停机失败 {type(exc).__name__}"}
            for _ in range(10):
                if not _port_open(port):
                    break
                time.sleep(1)
        if act == "stop":
            r = {"ok": True, "action": "stop", "在线": port_up(), "端口": port}
            J.record("deploy", "octop_control:stop", detail=r)
            return r
    # start / restart
    if act == "start" and port_up():
        # 已经在跑（可能是桌面 APP 自己拉起的底座）→ 不重复起，避免两个底座抢端口
        r = {"ok": True, "action": "start", "在线": True, "端口": active_port(),
             "说明": "已有底座在跑，跳过启动（避免重复实例）"}
        J.record("deploy", "octop_control:start", detail=r)
        return r
    port = OCTOP_PORT if not port_up() else active_port()
    try:
        subprocess.Popen([str(RUNTIME), str(LAUNCH), "run", "--host", OCTOP_HOST,
                          "--port", str(port)],
                         cwd=str(BUNDLED), stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:                                  # noqa: BLE001
        return {"ok": False, "action": act, "reason": f"拉起失败 {type(exc).__name__}"}
    for _ in range(30):
        time.sleep(2)
        if _port_open(port):
            break
    h = health()
    r = {"ok": bool(h.get("ok")), "action": act, "在线": h.get("ok", False),
         "端口": active_port(), "健康": h}
    J.record("deploy", f"octop_control:{act}", detail=r, ok=r["ok"],
             reason="" if r["ok"] else "拉起后健康检查仍未通过")
    return r


# ═══════════ ⑤ 融合总状态 + 固化 ═══════════
def status(*, fresh: bool = False) -> dict:
    """融合总状态。带短 TTL 缓存（默认 30 秒）：真机实测一次要 ~20 秒（端口探测×3 + 健康请求），
    页面每刷一次就等 20 秒是"一停一停"的典型来源。读数仍是真探测，只是不重复做同一件慢事。"""
    if fresh:
        return _status_build()
    out = dict(_STATUS_CACHE.get("v", _status_build))
    out.setdefault("缓存秒", _STATUS_CACHE.ttl)
    return out


def _status_build() -> dict:
    h = health()
    b = _bind_counts()
    brand = brand_state()
    av = control_available()
    port = active_port()                                  # 只探一次，别在同一个返回值里问三遍
    up = bool(h.get("ok"))
    connected = len(PAGES) if up else 0
    sym = (b.get("能力对数") or 0) >= (b.get("触手数") or 0) * (b.get("能力数") or 0)
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "octop在线": up, "健康": h, "底座": f"{OCTOP_HOST}:{port}",
            "端口": port, "端口候选": list(OCTOP_PORT_CANDIDATES),
            "原生页面数": len(PAGES), "已连接页面数": connected,
            "页面分组": group_names(),
            "绑定": b, "绑定对称": sym,
            "品牌": brand, "可操控": av,
            "融合度": round(100.0 * sum(1 for x in (up, sym, brand.get("已注册"), av["可操控"]) if x) / 4, 1),
            "说明": "Octop 原生页面在 V9 站内**浏览器侧内嵌**（不经我们服务器转发，页面零丢失）"}


def register(*, note: str = "") -> dict:
    """固化融合登记表（页面清单 + 控制配置），可回滚。"""
    from core import solidify
    from core import deploy_ledger as J
    payload = {"pages": pages(), "groups": group_names(), "control": control_available(),
               "brand": brand_state(), "status": status()}
    r = solidify.solidify("octop_fusion", payload, note=note or "V9⇄Octop 融合登记表")
    J.record("solidify", "solidify:octop_fusion", detail=r, ok=bool(r.get("ok")))
    return r


__all__ = ["PAGES", "pages", "group_names", "port_up", "health", "brand_state",
           "control_available", "control", "status", "register", "OCTOP_PORT"]
