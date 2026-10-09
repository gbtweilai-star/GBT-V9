# core/pulse.py —— 万能插脉冲 · dev: 自由的风 · 万物皆可插，万物皆可控
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 设计：脉冲 = 触手末梢的万能插头。任何外部对象（进程/文件/API/网页）插一个
#       "标准插座描述符" 就变成一根可扫可控的触手分支；优先结构化插座（秒级、零幻觉），
#       插不上再走坐标兜底（视觉路径）。
from core.swallow import swallow as _swallow
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

# "model" = **装在万能插肚子里的模型槽**（主人令 2026-10-09）：模型不散落在外面，
#   而是插进万能插、在插肚子里的沙盒里跑，输出走桥接回来。
SOCKET_KINDS = ("process", "file", "api", "web", "model")


@dataclass
class Socket:
    kind: str
    target: str
    options: dict = field(default_factory=dict)
    plugged_at: float = 0.0
    state: str = "unplugged"          # unplugged | plugged | failed
    detail: str = ""


class Pulse:
    def __init__(self, ledger=None, brain=None) -> None:
        self.ledger, self.brain = ledger, brain
        self.plugged: dict[str, Socket] = {}

    # ── 插入：结构化插座优先 ──
    def plug(self, socket: Socket) -> Socket:
        if socket.kind not in SOCKET_KINDS:
            socket.state, socket.detail = "failed", f"不支持的插座类型 {socket.kind}"
            return socket
        try:
            if socket.kind == "file":
                p = Path(socket.target)
                socket.state = "plugged" if p.exists() else "failed"
                socket.detail = f"exists={p.exists()} size={p.stat().st_size if p.exists() else 0}"
            elif socket.kind == "process":
                socket.state = "plugged"
                socket.detail = "process socket ready (dispatch via adapter)"
            elif socket.kind == "model":
                # 模型槽：云插件凭据齐 或 本机有 ollama ⇒ 算插上；两边都不行就如实 failed
                from core import cloud_runner as CR
                cred = CR.credentials()
                cloud_ok = bool(cred.get("token") and cred.get("account"))
                local = (Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe").is_file()
                socket.state = "plugged" if (cloud_ok or local) else "failed"
                socket.detail = "云插件%s · 本地ollama%s" % ("齐" if cloud_ok else "未齐", "在" if local else "不在")
            else:                                    # api / web：有 URL 描述即算插上（真请求由适配器做）
                scheme = socket.target.split(":", 1)[0].lower()
                socket.state = "plugged" if scheme in ("http", "https") else "failed"
                socket.detail = f"scheme={scheme}"
        except Exception as exc:  # noqa: BLE001
            socket.state, socket.detail = "failed", f"{type(exc).__name__}: {exc}"
        socket.plugged_at = time.time()
        self.plugged[socket.target] = socket
        if self.ledger:
            try:
                self.ledger.log("pulse", f"plug:{socket.target}", "scanned",
                                f"{socket.kind} {socket.state}")
            except Exception as e:
                _swallow(__file__, e)
        return socket

    # ── 分发：插上的走结构化通道 ──
    def dispatch(self, target: str, payload: dict) -> dict:
        """分发 = **真驱动**（原先只回 handled:True 的空壳，2026-10-09 接上 run_plugged）。

        payload 约定：{"action": "read|write|exists|run|...", "args": {...}}；
        没有 action/args 时，整个 payload 当 args。
        """
        sock = self.plugged.get(target)
        if sock is None or sock.state != "plugged":
            return {"ok": False, "error": "未插上（先 plug 再 dispatch）", "target": target}
        p = dict(payload or {})
        action = str(p.pop("action", "run"))
        args = p.pop("args", None)
        if not isinstance(args, dict):
            args = p
        r = run_plugged(sock, action=action, args=args, ledger=self.ledger)
        return {"ok": bool(r.get("ok")), "socket": sock.kind, "handled": True, "结果": r}

    def unplug(self, target: str) -> bool:
        sock = self.plugged.pop(target, None)
        if sock:
            sock.state = "unplugged"
            return True
        return False

    def snapshot(self) -> dict:
        return {"plugged": {k: {"kind": v.kind, "state": v.state, "detail": v.detail}
                            for k, v in self.plugged.items()},
                "count": len(self.plugged)}

    def selftest(self, *, sample_path: str | None = None) -> dict:
        """脉冲自检：真插几个插座 + 真分发一次，全部落账。

        面板上那个"调用 N 次"必须来自真实动作，不能是装饰数字 —— 所以这里
        插的是**本机真实存在的文件**，并故意放一个非法类型，让失败也如实记下来。
        """
        target_file = sample_path or str(Path(__file__).resolve())
        targets = [
            Socket("file", target_file),
            Socket("api", "https://example.com/health"),
            Socket("process", "selftest"),
            Socket("not_a_kind", "x"),                        # 故意非法：必须如实 failed
        ]
        plugged = [self.plug(s) for s in targets]
        ok_items = [s for s in plugged if s.state == "plugged"]
        disp = (self.dispatch(ok_items[0].target, {"ping": 1}) if ok_items
                else {"ok": False, "error": "没有插上的插座"})
        if self.ledger:
            try:
                self.ledger.log("pulse", "selftest", "scanned" if disp.get("ok") else "vuln",
                                f"plugged={len(ok_items)}/{len(plugged)} dispatch={disp.get('ok')}")
            except Exception as e:
                _swallow(__file__, e)
        return {"ok": bool(disp.get("ok")), "插座数": len(plugged),
                "插上": [(s.kind + ":" + s.target) for s in ok_items],
                "失败": [(s.kind + ":" + s.target + " " + s.detail)
                         for s in plugged if s.state != "plugged"],
                "分发": disp, "台账": self.snapshot()["count"],
                "说明": "插上是真探测（文件存在/进程就绪/URL 合法），分发走结构化通道"}


# ── 万能插执行器（主人 2026-10-09 澄清："使用自身能源为驱动能源"，不是攻击）──────────
# 科幻原型：机器人把线插进控制台 → **她自己供能、自己驱动它**。落到工程就是：
#   · process 插座：能源 = 本机 CPU + 她的调度（起进程、送参、收结果）
#   · file    插座：能源 = 本机 IO（读/写/查）
#   · api     插座：能源 = **她金库里的凭据**（一次存入 → 之后自动注入，主人不必再配）
#   · web/gui 插座：能源 = **她的眼 + 手**（截屏→找→点，人类级操作）
# 三条纪律：
#   ① **不攻击**：不改目标程序、不绕它的授权校验；够不着就如实说够不着；
#   ② 凭据**只从金库/env 取**，记录里只写"用了哪一份"，绝不回显原文；
#   ③ 每插一次、每跑一次都**落账**（谁授权、用哪份能源、花了多久）。
SOCKET_ENERGY = {"process": "本机 CPU + 她的调度", "file": "本机 IO",
                 "api": "她金库里的凭据（一次存入·自动注入）",
                 "web": "她的眼+手（人类级操作）",
                 "model": "云插件 neurons（每根触手独立配额）/ 本地算力"}


def energy() -> dict:
    """她的能源总账（面板一行）：云算力 + 本机 + 能自动注入的凭据位。"""
    import os as _o
    out = {"插座能源": dict(SOCKET_ENERGY)}
    try:
        from core import cloud_runner as CR
        n = CR.neurons_today()
        out["云算力"] = {"免费额度": n["免费额度"], "估算已用": n["估算已用"],
                        "估算剩余": n["估算剩余"], "撞过4006": n["撞过4006"]}
    except Exception as exc:                                 # noqa: BLE001
        out["云算力"] = {"读不到": type(exc).__name__}
    try:
        import shutil
        out["本机"] = {"磁盘可用GB": round(
            shutil.disk_usage(_o.environ.get("SystemDrive", "C:") + "\\").free / 1e9, 1)}
    except Exception as e:
        _swallow(__file__, e)
    vault = []
    for nm, env in (("gui_grant", "GUI_GRANT_SECRET"), ("order", "GBT_ORDER_SECRET")):
        try:
            from core import local_secret as LS
            vault.append({"位": nm, "持久": bool(LS.is_persistent(nm)), "env": bool(_o.environ.get(env))})
        except Exception as e:
            _swallow(__file__, e)
    out["凭据位"] = vault
    return out


def plug_and_run(target: str, *, kind: str = "process", action: str = "run",
                 args: dict | None = None, timeout: float = 30.0, ledger=None,
                 pulse=None) -> dict:
    """**一行搞定：插入 → 驱动 → 回读数**（这就是"插上并驱动它"的正式入口）。"""
    p = pulse or Pulse(ledger=ledger)
    try:
        sock = Socket(kind=kind, target=target)
    except TypeError:
        sock = Socket(target=target, kind=kind)
    p.plug(sock)
    if sock.state != "plugged":
        return {"ok": False, "reason": "插入失败", "插座": getattr(sock, "detail", ""),
                "目标": target, "类型": kind}
    r = run_plugged(sock, action=action, args=args, timeout=timeout, ledger=ledger or p.ledger)
    r["插座状态"] = sock.state
    return r


def run_plugged(socket, *, action: str = "run", args: dict | None = None,
                timeout: float = 30.0, ledger=None) -> dict:
    """**插上并驱动它**（真执行，按插座类型用不同能源）。**不攻击、不绕授权校验。**"""
    import subprocess
    import sys as _sys
    a = args or {}
    if getattr(socket, "state", "") != "plugged":
        return {"ok": False, "reason": "先 plug 成功再驱动", "插座": getattr(socket, "target", "")}
    t0 = time.time()
    res = {"ok": False, "插座": socket.kind, "目标": socket.target, "动作": action,
           "能源": SOCKET_ENERGY.get(socket.kind, "未知")}
    try:
        if socket.kind == "file":
            p = Path(socket.target)
            if action == "read":
                res.update({"ok": True, "读回": p.read_text(encoding="utf-8", errors="replace")[:400]})
            elif action == "write":
                p.write_text(str(a.get("text") or ""), encoding="utf-8")
                res.update({"ok": True, "写了字节": p.stat().st_size})
            elif action == "exists":
                res.update({"ok": True, "存在": p.exists()})
            else:
                res.update({"ok": False, "reason": "file 插座支持 read/write/exists"})
        elif socket.kind == "process":
            cmd = a.get("cmd") or socket.target
            if isinstance(cmd, str):
                cmd = [_sys.executable, "-c", cmd[3:]] if cmd.startswith("py:") else cmd.split()
            cp = subprocess.run(cmd, capture_output=True, timeout=timeout)
            res.update({"ok": cp.returncode == 0, "退出码": cp.returncode,
                        "stdout": (cp.stdout or b"").decode("utf-8", "replace")[:400],
                        "stderr": (cp.stderr or b"").decode("utf-8", "replace")[:200]})
        elif socket.kind == "model":
            # ★ 模型在**万能插肚子里的沙盒**里跑（手桥接进、输出桥接出）
            from core.pulse_sandbox import Sandbox
            sb = Sandbox(timeout=timeout or 60.0, name="plug-model")
            m = sb.model(str(a.get("prompt") or socket.target or ""),
                         backend=str(a.get("backend") or "cloud"),
                         face=str(a.get("face") or "文本"),
                         tentacle=str(a.get("tentacle") or "t001"))
            res.update({"ok": bool(m.get("ok")), "后端": m.get("后端"), "出字": m.get("出字"),
                        "沙盒": m.get("沙盒"), "产物": m.get("产物"), "秒": m.get("秒")})
        elif socket.kind == "api":
            import urllib.request
            from body.net_guard import assert_safe_outbound_url
            url = a.get("url") or socket.target
            assert_safe_outbound_url(url)
            cred_name = a.get("cred") or ""
            token = ""
            if cred_name:
                from core import local_secret as LS
                token = LS.local_secret(cred_name, a.get("env") or cred_name.upper()) or ""
            hdr = {"Content-Type": "application/json"}
            if token:
                hdr["Authorization"] = "Bearer " + token
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                res.update({"ok": r.status == 200, "http": r.status,
                            "凭据位": cred_name or "（无，匿名）",
                            "回": r.read().decode("utf-8", "replace")[:300]})
        else:
            res.update({"ok": False,
                        "reason": "web/gui 插座要她**自己的眼+手**：走 GuiAgent（需主人一次性授权）",
                        "怎么走": "core.gui_agent.GuiAgent(dry_run=False, allow_actions=True)"})
    except Exception as exc:                                 # noqa: BLE001
        res.update({"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:180]})
    res["ms"] = int((time.time() - t0) * 1000)
    socket.detail = "ran:%s ok=%s" % (action, res["ok"])
    if ledger is not None:
        try:
            ledger.log("pulse", "run:" + socket.target, "ok" if res["ok"] else "fail",
                       "%s %s %sms" % (socket.kind, action, res["ms"]))
        except Exception as e:
            _swallow(__file__, e)
    return res


__all__ = ["Pulse", "Socket", "SOCKET_KINDS", "SOCKET_ENERGY", "energy", "run_plugged",
           "plug_and_run"]
