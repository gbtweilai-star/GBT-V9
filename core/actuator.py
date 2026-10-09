# core/actuator.py —— 操作执行层：观察→计划→执行→验证 闭环
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 分层执行: AX/DOM → 应用API → 坐标兜底 → 视觉定位
# 每步动作后独立回读验证; 失败重观察重规划; 高风险动作走确认门
# （AUTO_CONFIRM=1 由主人自行放行——开关在你的环境变量手里）
#
# 落盘说明（2026-10-05）：按主人给的完整代码落地，做了三处工程修正——
#   ① action_log 表走 senses.sqldialect 双方言（sqlite ?/INTEGER vs pg %s/BOOLEAN），
#      修掉原稿 PG 专属 TIMESTAMPTZ/now() 在 SQLite 后端直接炸的问题；
#   ② 修原稿笔误 pyautogui_hotkey（未定义）→ self._gui.hotkey("ctrl","c")；
#   ③ 子进程一律参数列表（无 shell 拼接）；launch/api 探针接受 list 或字符串（shlex 切分）。
from core.swallow import swallow as _swallow
import os, time, json, shlex, platform, subprocess
from dataclasses import dataclass, field
from enum import Enum

OSNAME = platform.system()          # Windows / Darwin / Linux

from senses.sqldialect import is_pg, txn


class Risk(str, Enum):
    SAFE = "safe"; WRITE = "write"; DESTRUCTIVE = "destructive"


# 高风险动作（默认必须人工确认；AUTO_CONFIRM=1 可放行——你自己决定）
CONFIRM_PATTERNS = ("删除", "清空", "格式化", "转账", "付款", "卸载", "覆盖", "drop ",
                    "delete", "rm ", "format", "payment", "uninstall")


@dataclass
class Action:
    primitive: str                 # click/type/scroll/drag/hotkey/wait/launch
    target: dict                   # {ax_path:|selector:|coords:[x,y]|app:}
    args: dict = field(default_factory=dict)
    verify: dict = field(default_factory=dict)   # {kind: element|text|pixel|state, probe:...}
    timeout_ms: int = 5000
    risk: str = "safe"
    action_id: str = ""


def _as_cmd_list(cmd):
    """launch/api 探针统一收口为参数列表：list 原样用；字符串 shlex 切分（无 shell）。"""
    if isinstance(cmd, (list, tuple)):
        return [str(c) for c in cmd]
    return shlex.split(str(cmd), posix=(OSNAME != "Windows"))


class Actuator:
    def __init__(self, ledger, brain, devour=None, auto_confirm=False,
                 pyautogui=None):
        self.led, self.brain, self.devour = ledger, brain, devour
        self.auto_confirm = auto_confirm or os.environ.get("AUTO_CONFIRM") == "1"
        self._gui = pyautogui                      # 测试注入假鼠标；真跑时惰性加载
        self._init_table()

    @property
    def gui(self):
        if self._gui is None:
            import pyautogui as _pg
            _pg.FAILSAFE = True                    # 鼠标甩左上角可紧急中止
            self._gui = _pg
        return self._gui

    def _init_table(self):
        if is_pg(self.led):
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS action_log(
                    action_id TEXT PRIMARY KEY, ts TIMESTAMPTZ DEFAULT now(),
                    primitive TEXT, target TEXT, args TEXT, layer TEXT,
                    ok BOOLEAN, verified BOOLEAN, error TEXT, before_state TEXT,
                    after_state TEXT, screenshot TEXT)""")
        else:
            with txn(self.led) as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS action_log(
                    action_id TEXT PRIMARY KEY, ts TEXT DEFAULT (datetime('now')),
                    primitive TEXT, target TEXT, args TEXT, layer TEXT,
                    ok INTEGER, verified INTEGER, error TEXT, before_state TEXT,
                    after_state TEXT, screenshot TEXT)""")

    # ═══ 分层定位：AX/DOM → API → 坐标 ═══
    def _locate(self, target) -> dict:
        # ① 无障碍/DOM（语义定位，抗布局变化）
        if target.get("ax_path") or target.get("selector"):
            r = self._locate_ax(target)
            if r:
                return {"layer": "ax", **r}
        # ② 应用 API
        if target.get("app") and target.get("api"):
            r = self._locate_api(target)
            if r:
                return {"layer": "api", **r}
        # ③ 坐标兜底
        if target.get("coords"):
            return {"layer": "coords", "x": target["coords"][0], "y": target["coords"][1]}
        # ④ 最后一招：视觉定位（吞噬能截图 + 大脑找）
        return self._locate_vision(target)

    def _locate_ax(self, target):
        """跨平台无障碍树定位"""
        try:
            if OSNAME == "Windows":
                import pywinauto
                app = pywinauto.Desktop(backend="uia")
                if target.get("selector"):
                    el = app.window(auto_id=target["selector"])
                else:
                    el = app.window(title=target.get("name"))
                r = el.rectangle()
                return {"x": (r.left + r.right) // 2, "y": (r.top + r.bottom) // 2, "el": el}
            if OSNAME == "Darwin":
                script = ('tell application "System Events" to get position of '
                          'UI element "' + str(target.get("selector")) + '" of front window of '
                          '(first process whose frontmost is true)')
                out = subprocess.run(["osascript", "-e", script],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3).stdout
                x, y = [int(v.strip()) for v in out.split(",")]
                return {"x": x, "y": y}
            if OSNAME == "Linux":
                import pyatspi
                # 简化：遍历桌面找匹配 name
                for app in pyatspi.Registry.getDesktop(0):
                    for el in app:
                        if target.get("selector") and target["selector"] in str(el.name):
                            return {"x": el.get_position(0)[0] + 5,
                                    "y": el.get_position(0)[1] + 5, "el": el}
        except Exception:
            return None
        return None

    def _locate_api(self, target):
        try:
            r = subprocess.run(_as_cmd_list(target["api"]), capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=target.get("timeout_ms", 5000) / 1000)
            return {"out": r.stdout.strip()} if r.returncode == 0 else None
        except Exception:
            return None

    def _grab_image(self):
        """从吞噬能取一帧并转成 PIL 图（兼容 ndarray / PNG bytes / 原始 RGB bytes）。"""
        if not self.devour:
            return None
        import io
        import numpy as np
        from PIL import Image
        frame = self.devour._grab()
        if isinstance(frame, bytes):
            if frame[:4] == b"\x89PNG":
                return Image.open(io.BytesIO(frame))
            arr = np.frombuffer(frame, dtype=np.uint8)
            w = getattr(self.devour, "w", None) or getattr(self.devour, "width", None)
            h = getattr(self.devour, "h", None) or getattr(self.devour, "height", None)
            if not (w and h and arr.size >= w * h * 3):
                return None
            return Image.fromarray(arr[:w * h * 3].reshape(h, w, 3))
        return Image.fromarray(frame)

    def _locate_vision(self, target):
        """视觉定位：截图 → 大脑给坐标"""
        img = self._grab_image()
        if img is None:
            return None
        r = self.brain.chat([{"role": "user", "content":
            "在截图中找「" + str(target.get("name", "目标")) +
            "」，只回JSON:{\"x\":0,\"y\":0,\"found\":true}"}])
        if isinstance(r, dict) and r.get("found"):
            return {"layer": "vision", "x": r["x"], "y": r["y"]}
        return None

    # ═══ 执行原语 ═══
    def _exec(self, a: Action, loc: dict) -> dict:
        gui = self.gui
        p = a.primitive
        if p == "click":
            gui.click(loc.get("x"), loc.get("y"),
                      button=a.args.get("button", "left"),
                      clicks=a.args.get("clicks", 1))
        elif p == "type":
            gui.typewrite(a.args.get("text", ""), interval=0.02)
        elif p == "hotkey":
            gui.hotkey(*a.args.get("keys", []))
        elif p == "scroll":
            gui.scroll(a.args.get("amount", -3))
        elif p == "drag":
            gui.moveTo(loc.get("x"), loc.get("y"))
            gui.dragTo(a.args["to"][0], a.args["to"][1],
                       duration=a.args.get("dur", 0.5))
        elif p == "wait":
            time.sleep(a.args.get("sec", 1))
        elif p == "launch":
            subprocess.Popen(_as_cmd_list(a.args["cmd"]),   # 参数列表，无 shell
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            raise ValueError(f"未知原语 {p}")
        return {"executed": True}

    # ═══ 验证：独立回读，不假设成功 ═══
    def _verify(self, a: Action) -> bool:
        v = a.verify or {}
        kind = v.get("kind")
        if not kind:
            return True           # 未声明验证，如实返回 True 但记账
        time.sleep(v.get("settle_ms", 400) / 1000)
        if kind == "text":
            try:
                import pyperclip
                self.gui.hotkey("ctrl", "c")       # 修原稿笔误 pyautogui_hotkey
                return v.get("expect", "") in (pyperclip.paste() or "")
            except Exception:
                return False
        if kind == "element":
            return self._locate({"selector": v.get("probe")}) is not None
        if kind == "pixel":
            return self._pixel_match(v.get("region"), v.get("expect"))
        if kind == "state":
            return self._locate_api({"api": v.get("probe"), "app": True}) is not None
        return False

    def _pixel_match(self, region, expect):
        img = self._grab_image()
        if img is None:
            return False
        import hashlib
        import numpy as np
        arr = np.array(img)
        if region:
            x, y, w, h = region
            arr = arr[y:y + h, x:x + w]
        h = hashlib.sha256(arr.tobytes()).hexdigest()[:16]
        return h == expect

    # ═══ 主入口：单步执行 + 验证 + 失败上报 ═══
    def run_action(self, a: Action) -> dict:
        a.action_id = a.action_id or f"act-{int(time.time() * 1000)}"
        # 风险门：高风险需确认
        if not self.auto_confirm:
            blob = f"{a.primitive} {a.target} {a.args}".lower()
            if any(p in blob for p in CONFIRM_PATTERNS) or a.risk == "destructive":
                self.brain.ask("actuator", a.action_id, f"需人工确认: {blob}")
                return {"ok": False, "needs_confirm": True, "action_id": a.action_id}

        before = self._state_snapshot()
        loc = self._locate(a.target)
        if not loc:
            return self._fail(a, "定位失败(三层均未命中)", before)
        try:
            self._exec(a, loc)
        except Exception as e:
            return self._fail(a, f"执行异常: {e}", before, layer=loc.get("layer"))
        verified = self._verify(a)
        after = self._state_snapshot()
        self._log(a, loc.get("layer"), True, verified, "", before, after)
        if not verified:
            # 结果不确定 → 回大脑，禁止盲目重复
            self.brain.ask("actuator", a.action_id, f"执行后验证失败，需重规划: {a.primitive}")
        return {"ok": verified, "layer": loc.get("layer"),
                "action_id": a.action_id, "verified": verified}

    def _fail(self, a, err, before, layer=None):
        after = self._state_snapshot()
        self._log(a, layer, False, False, err, before, after)
        self.brain.ask("actuator", a.action_id, f"动作失败: {err}")
        return {"ok": False, "error": err, "action_id": a.action_id}

    def _state_snapshot(self):
        try:
            return json.dumps({"ts": time.time(), "focus": self._focus()})[:500]
        except Exception:
            return ""

    def _focus(self):
        try:
            if OSNAME == "Windows":
                import pywinauto
                return pywinauto.Desktop(backend="uia").window(active_only=True).window_text()
            if OSNAME == "Darwin":
                return subprocess.run(["osascript", "-e",
                    'tell app "System Events" to get name of first process whose frontmost is true'],
                    capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=2).stdout.strip()
        except Exception as e:
            _swallow(__file__, e)
        return ""

    def _log(self, a, layer, ok, verified, err, before, after):
        row = (a.action_id, a.primitive, json.dumps(a.target), json.dumps(a.args),
               layer, 1 if ok else 0, 1 if verified else 0, err, before, after)
        try:
            if is_pg(self.led):
                with txn(self.led) as cur:
                    cur.execute("""INSERT INTO action_log
                        (action_id,primitive,target,args,layer,ok,verified,error,
                         before_state,after_state)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                        ON CONFLICT (action_id) DO NOTHING""", row)
            else:
                with txn(self.led) as cur:
                    cur.execute("""INSERT INTO action_log
                        (action_id,primitive,target,args,layer,ok,verified,error,
                         before_state,after_state)
                        VALUES(?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT (action_id) DO NOTHING""", row)
        except Exception as e:
            _swallow(__file__, e)

    # ═══ 任务级：观察→计划→逐步执行→验证 ═══
    def run_task(self, goal: str, max_steps=15) -> dict:
        trace = []
        for step in range(max_steps):
            obs = self._observe()
            plan = self.brain.chat([{"role": "system", "content":
                '你是电脑操作规划器。基于观察给下一步单步动作,只回JSON:'
                '{"done":false,"action":{"primitive":"click|type|hotkey|launch|wait",'
                '"target":{},"args":{},"verify":{}},"reason":"..."}'
                '若目标已达成回 {"done":true}'},
                {"role": "user", "content": f"目标:{goal}\n观察:{obs}\n已完成:{trace}"}])
            if plan.get("done"):
                return {"ok": True, "steps": step, "trace": trace}
            act = plan.get("action", {})
            a = Action(primitive=act.get("primitive", "wait"),
                       target=act.get("target", {}), args=act.get("args", {}),
                       verify=act.get("verify", {}),
                       risk=act.get("risk", "safe"))
            r = self.run_action(a)
            trace.append({"step": step, "action": act.get("primitive"),
                          "ok": r.get("ok"), "reason": plan.get("reason")})
            if r.get("needs_confirm"):
                return {"ok": False, "paused": True, "needs_confirm": True, "trace": trace}
        return {"ok": False, "reason": f"{max_steps}步未完成", "trace": trace}

    def _observe(self):
        """观察：焦点窗口 + 最近一帧（交给大脑）"""
        out = {"focus": self._focus(), "os": OSNAME}
        if self.devour:
            try:
                img = self._grab_image()
                if img is not None:
                    outdir = getattr(self.devour, "out", None) or getattr(self.devour, "outdir", None)
                    if outdir:
                        path = os.path.join(str(outdir), f"obs_{int(time.time() * 1000)}.png")
                        img.save(path)
                        out["frame"] = path
            except Exception as e:
                _swallow(__file__, e)
        return json.dumps(out, ensure_ascii=False)[:1500]


__all__ = ["Actuator", "Action", "Risk", "CONFIRM_PATTERNS"]
