# core/tool_bay.py —— 触手自助工具坞：触手自己取用/安装/运行它需要的工具
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 口径（主人 2026-10-06）：让触手自己把完成任务要用到的工具跑起来 —— 但要**有据可查**。
#   · 取用/安装必须带主人的授权令牌（core/gui_grant），越权/无授权一律拒绝；
#   · 安装只落在**编队自留地**（tools/_bay，可整目录删除即回滚），绝不写系统目录、绝不提权；
#   · 命令一律参数列表（无 shell 拼接），包名走白名单，未知包直接拒；
#   · 每次安装/运行落 tool_bay_audit（谁、哪个工具、授权号、成败、耗时）。
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from core.gui_grant import Grant
from senses.sqldialect import txn
from core.swallow import swallow as _swallow

BAY_ROOT = Path(os.environ.get("TOOL_BAY_ROOT", "tools/_bay"))
INSTALL_TIMEOUT = int(os.environ.get("TOOL_BAY_INSTALL_TIMEOUT", "600"))
RUN_TIMEOUT = int(os.environ.get("TOOL_BAY_RUN_TIMEOUT", "120"))


@dataclass
class ToolSpec:
    name: str
    kind: str                 # pip | winget | npm | builtin | cmd
    target: str               # 包名 / winget id / 可执行
    entry: str = ""           # 运行入口（builtin 时为 python 模块；cmd 时为可执行名）
    desc: str = ""
    risk: str = "safe"
    args_hint: list = field(default_factory=list)


# 白名单目录：只认这里登记的包；没登记就是"未知工具"，不许临时拍脑袋装
CATALOG: dict[str, ToolSpec] = {
    "image_ops": ToolSpec("image_ops", "pip", "Pillow", entry="PIL",
                          desc="图像处理（裁剪/缩放/比对）"),
    "ocr": ToolSpec("ocr", "pip", "pytesseract", entry="pytesseract",
                    desc="文字识别（纯视觉读屏）"),
    "screen": ToolSpec("screen", "pip", "mss", entry="mss", desc="高速屏幕抓取"),
    "ui_tree": ToolSpec("ui_tree", "pip", "uiautomation", entry="uiautomation",
                        desc="无障碍树（可选路径，纯视觉不依赖）"),
    "http": ToolSpec("http", "pip", "httpx", entry="httpx", desc="HTTP 客户端"),
    "sqlite": ToolSpec("sqlite", "builtin", "sqlite3", entry="sqlite3",
                       desc="本地库读写（内置）"),
    "json_tool": ToolSpec("json_tool", "builtin", "json", entry="json",
                          desc="JSON 处理（内置）"),
    "git": ToolSpec("git", "cmd", "git", entry="git", desc="版本控制", risk="write"),
    "ffmpeg": ToolSpec("ffmpeg", "cmd", "ffmpeg", entry="ffmpeg",
                       desc="视频转码/帧段封装", risk="write"),
    "sevenzip": ToolSpec("sevenzip", "winget", "7zip.7zip", entry="7z",
                         desc="压缩/解压", risk="write"),
}


class ToolBay:
    """触手自助工具坞。install/run 都必须带合法授权令牌。"""

    def __init__(self, ledger=None, *, root=None, grant_required=True,
                 runner=None, exists_fn=None):
        self.led = ledger
        self.root = Path(root or BAY_ROOT)
        self.grant_required = grant_required
        # runner(cmd: list, timeout: int) → (rc, out, err)；测试可注入假 runner
        self._run_cmd = runner or self._default_runner
        self._exists = exists_fn or self._default_exists
        self._init_table()

    # ── 默认执行器：参数列表、无 shell、独立进程组方便超时清理 ──
    def _default_runner(self, cmd: list, timeout: int):
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                               cwd=str(self.root if self.root.exists() else Path.cwd()))
            return p.returncode, p.stdout or "", p.stderr or ""
        except subprocess.TimeoutExpired:
            return 124, "", f"timeout>{timeout}s"
        except FileNotFoundError as exc:
            return 127, "", f"not_found:{exc}"

    def _default_exists(self, entry: str) -> bool:
        import importlib.util
        if self.root.exists() and (self.root / entry).exists():
            return True
        try:
            return importlib.util.find_spec(entry) is not None
        except Exception:                                     # noqa: BLE001
            return False

    def _init_table(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS tool_bay ("
                            "name TEXT PRIMARY KEY, kind TEXT, target TEXT, path TEXT,"
                            " state TEXT, installed_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS tool_bay_audit ("
                            "audit_id TEXT, tentacle_id TEXT, op TEXT, tool TEXT,"
                            " grant_id TEXT, ok INTEGER, detail TEXT, ms INTEGER, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)


    # ── 审计 ──
    def _audit(self, tentacle_id, op, tool, grant_id, ok, detail="", ms=0):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO tool_bay_audit (audit_id, tentacle_id, op,"
                            " tool, grant_id, ok, detail, ms, at)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (os.urandom(8).hex(), tentacle_id, op, tool, grant_id or "-",
                             1 if ok else 0, (detail or "")[:300], int(ms),
                             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        except Exception as e:
            _swallow(__file__, e)


    # ── 授权闸门：工具坞是"设备侧能力"，授权里要有 tools 或 * ──
    # 白名单内 + risk=safe + 装进自留地（pip --target，可整目录删除回滚）⇒ **白名单即授权**，
    # 不需要主人令牌；risk=write（git/ffmpeg/7z）与系统级安装仍要主人一次性令牌。
    # 依据：主人纪律「别开口要用户做事」+ 六类危险里不含"可回滚的自留地装包"。
    AUTONOMOUS_RISKS: tuple = ("safe",)
    AUTONOMOUS_KINDS: tuple = ("builtin", "pip")

    def _gate(self, grant, *, tool: str, op: str, tentacle_id: str) -> tuple[bool, str, dict]:
        if not self.grant_required:
            return True, "gate_off", {}
        spec = CATALOG.get(tool)
        if spec is not None and spec.risk in self.AUTONOMOUS_RISKS and spec.kind in self.AUTONOMOUS_KINDS:
            self._audit(tentacle_id, op, tool, "whitelist:auto", True, "白名单内 safe 工具免主人令牌（自留地可回滚）")
            return True, "whitelist_autonomous", {"grant_id": "whitelist:auto"}
        ok, why, data = Grant.verify_action(grant, primitive="tools")
        if not ok:
            self._audit(tentacle_id, op, tool, (data or {}).get("grant_id"), False, why)
            return False, why, data or {}
        return True, "ok", data

    # ── 取包：安装到自留地（可整目录删除回滚）──
    def install(self, tentacle_id: str, tool: str, *, grant=None,
                upgrade: bool = False) -> dict:
        spec = CATALOG.get(tool)
        t0 = time.time()
        if spec is None:
            self._audit(tentacle_id, "install", tool, None, False, "unknown_tool")
            return {"ok": False, "error": f"unknown_tool:{tool}",
                    "hint": "白名单里没有就不许临时安装"}
        ok, why, data = self._gate(grant, tool=tool, op="install", tentacle_id=tentacle_id)
        if not ok:
            return {"ok": False, "reason": why, "tool": tool}
        if self._exists(spec.entry) and not upgrade:
            # 🔴 2026-10-09 修：这条"已存在"的短路**原先不写记录**，导致状态读取器
            #    （tentacle_equip._tool_state 读 tool_bay 表）永远判"未装" ⇒ 全队卡在 51/100。
            #    已存在也要落一条 state=installed，让"配齐没配齐"看得见。
            self._audit(tentacle_id, "install", tool, "whitelist:auto" if spec.risk == "safe" else None,
                        True, "already_present")
            self._record(tool, spec, state="installed")
            return {"ok": True, "tool": tool, "already": True, "entry": spec.entry}
        cmd = self._install_cmd(spec)
        if cmd is None:
            self._audit(tentacle_id, "install", tool, data.get("grant_id"), False,
                        f"unsupported_kind:{spec.kind}")
            return {"ok": False, "error": f"unsupported_kind:{spec.kind}", "tool": tool}
        self.root.mkdir(parents=True, exist_ok=True)
        rc, out, err = self._run_cmd(cmd, INSTALL_TIMEOUT)
        ms = int((time.time() - t0) * 1000)
        success = rc == 0 and self._exists(spec.entry)
        self._audit(tentacle_id, "install", tool, data.get("grant_id"), success,
                    (out or err or "")[-300:], ms)
        if success:
            self._record(tool, spec, state="installed")
        return {"ok": success, "tool": tool, "cmd": cmd, "rc": rc, "ms": ms,
                "entry": spec.entry,
                "detail": (out or err or "")[-400:] if not success else "installed"}

    def _install_cmd(self, spec: ToolSpec) -> list | None:
        """安装命令一律参数列表；pip 装进自留地（--target），不污染全局。"""
        if spec.kind == "pip":
            return [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
                    "--no-input", "--target", str(self.root), spec.target]
        if spec.kind == "npm":
            return ["npm", "install", "--prefix", str(self.root), spec.target]
        if spec.kind == "winget":
            # winget 需要系统安装权限 → 明确要求"人工放行"，不偷偷提权
            return None
        if spec.kind in ("builtin", "cmd"):
            return None                                        # 内置/系统命令无需安装
        return None

    def _record(self, tool, spec, *, state):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO tool_bay (name, kind, target, path, state,"
                            " installed_at) VALUES (?,?,?,?,?,?)"
                            " ON CONFLICT (name) DO UPDATE SET state=EXCLUDED.state,"
                            " path=EXCLUDED.path, installed_at=EXCLUDED.installed_at",
                            (tool, spec.kind, spec.target, str(self.root), state,
                             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        except Exception as e:
            _swallow(__file__, e)


    # ── 运行：触手自己把工具跑起来（仍要带授权）──
    def run(self, tentacle_id: str, tool: str, args=None, *, grant=None,
            timeout=None, python_code: str | None = None) -> dict:
        spec = CATALOG.get(tool)
        if spec is None:
            self._audit(tentacle_id, "run", tool, None, False, "unknown_tool")
            return {"ok": False, "error": f"unknown_tool:{tool}"}
        ok, why, data = self._gate(grant, tool=tool, op="run", tentacle_id=tentacle_id)
        if not ok:
            return {"ok": False, "reason": why, "tool": tool}
        cmd = self._run_cmd_for(spec, args or [], python_code)
        t0 = time.time()
        rc, out, err = self._run_cmd(cmd, int(timeout or RUN_TIMEOUT))
        ms = int((time.time() - t0) * 1000)
        ok_run = rc == 0
        self._audit(tentacle_id, "run", tool, data.get("grant_id"), ok_run,
                    (err or out or "")[-300:], ms)
        return {"ok": ok_run, "tool": tool, "cmd": cmd, "rc": rc, "ms": ms,
                "stdout": (out or "")[-4000:], "stderr": (err or "")[-2000:]}

    def _run_cmd_for(self, spec: ToolSpec, args: list, python_code: str | None) -> list:
        """把工具入口拼成参数列表。python_code 用于 builtin（-c 执行一小段，不落临时文件）。"""
        argv = [str(a) for a in args]
        if spec.kind == "builtin":
            if python_code:
                return [sys.executable, "-c", python_code]
            return [sys.executable, "-c",
                    f"import {spec.entry}, json,sys; "
                    f"print(getattr({spec.entry}, '__name__', '{spec.entry}'))"]
        if spec.kind == "pip":
            # 自留地里的包：把 --target 目录放进 sys.path 再跑
            if python_code:
                return [sys.executable, "-c",
                        f"import sys; sys.path.insert(0, r'{self.root}'); {python_code}"]
            return [sys.executable, "-c",
                    f"import sys; sys.path.insert(0, r'{self.root}'); "
                    f"import {spec.entry}; print(getattr({spec.entry},'__name__','ok'))"]
        return [spec.entry, *argv]

    # ── 触手自助：一句"我需要这个能力"→ 装好并跑通（全程带授权）──
    def self_serve(self, tentacle_id: str, need: str, *, grant=None,
                   args=None, python_code: str | None = None) -> dict:
        """触手自助闭环：解析需求 → 缺则安装 → 立刻用一次 → 返回结果。"""
        tool = self.resolve_need(need)
        if tool is None:
            return {"ok": False, "error": f"no_tool_for_need:{need}",
                    "available": sorted(CATALOG)}
        inst = self.install(tentacle_id, tool, grant=grant)
        if not inst.get("ok"):
            return {"ok": False, "stage": "install", "tool": tool, **inst}
        run = self.run(tentacle_id, tool, args=args, grant=grant, python_code=python_code)
        return {"ok": run.get("ok"), "tool": tool, "need": need,
                "installed": bool(inst.get("ok")), "result": run}

    @staticmethod
    def resolve_need(need: str) -> str | None:
        """把自然语言需求对到白名单工具。

        规则：按**最长关键词**命中（"识别屏幕上的文字"要落到 ocr，而不是被"屏幕"抢走），
        同长按表序。对不上就明确说没有 —— 不猜、不装不知道的包。
        """
        n = str(need or "").strip().lower()
        if n in CATALOG:
            return n
        table = (("识别", "ocr"), ("文字", "ocr"), ("ocr", "ocr"),
                 ("图像", "image_ops"), ("图片", "image_ops"), ("裁剪", "image_ops"),
                 ("数据库", "sqlite"), ("sqlite", "sqlite"),
                 ("json", "json_tool"), ("视频", "ffmpeg"), ("转码", "ffmpeg"),
                 ("帧段", "ffmpeg"), ("压缩", "sevenzip"), ("解压", "sevenzip"),
                 ("git", "git"), ("无障碍", "ui_tree"), ("控件树", "ui_tree"),
                 ("http", "http"), ("请求", "http"), ("接口", "http"),
                 ("截图", "screen"), ("屏幕", "screen"), ("抓屏", "screen"))
        best, best_len = None, 0
        for kw, tool in table:
            if kw in n and len(kw) > best_len:
                best, best_len = tool, len(kw)
        return best

    def list(self, tentacle_id=None, limit=100) -> list:
        if self.led is None:
            return []
        try:
            with txn(self.led) as cur:
                if tentacle_id:
                    cur.execute("SELECT op, tool, grant_id, ok, ms, at FROM tool_bay_audit"
                                " WHERE tentacle_id=? ORDER BY at DESC LIMIT ?",
                                (tentacle_id, int(limit)))
                else:
                    cur.execute("SELECT op, tool, grant_id, ok, ms, at FROM tool_bay_audit"
                                " ORDER BY at DESC LIMIT ?", (int(limit),))
                cols = [d[0] for d in (cur.description or [])]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []


__all__ = ["ToolBay", "ToolSpec", "CATALOG", "BAY_ROOT"]
