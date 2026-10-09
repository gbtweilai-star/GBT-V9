# core/pulse_sandbox.py —— 万能插肚子里的沙盒（模型在这里跑，产物从这里出）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「这个模型我要安装在万能插内部，在万能插肚子里设计一个沙盒，
#   把手和输出桥接做好，那就是无敌杀器。」
#
# 三件事：
#   ① **沙盒**：每次运行一个独立目录 state/sandbox/<runid>/，四面墙 ——
#      时间墙(timeout) · 空间墙(只许在沙盒目录内读写) · 输出墙(stdout/stderr 截断上限) ·
#      出网墙(默认不出网；要出网必须显式 allow_net=True，且仍过 body.net_guard)。
#   ② **手桥接（触手 → 沙盒）**：put_files() 把输入文件/参数送进去（触手的手伸进沙盒）。
#   ③ **输出桥接（沙盒 → 触手/工作流）**：收 stdout/stderr + 扫产物 → 每个产物算 sha256/字节，
#      回结构化结果并落台账（谁跑的、跑了什么、产了什么、多久、成没成）。
from __future__ import annotations
from core.swallow import swallow as _swallow

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SANDBOX_ROOT = ROOT / "state" / "sandbox"
LEDGER = ROOT / "state" / "sandbox_ledger.jsonl"

# 命令白名单：只有这些可执行名允许在沙盒里跑（不许拍脑袋装/跑未知二进制）
CMD_WHITELIST: tuple = ("python", "python.exe", "node", "node.exe", "ffmpeg", "ffprobe", "ollama", "ollama.exe", "git")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


class Sandbox:
    """一个沙盒 = 一次运行一个目录 + 四道墙。用完可整目录删（可回滚）。"""

    def __init__(self, *, timeout: float = 90.0, max_out_kb: int = 128, allow_net: bool = False,
                 name: str = ""):
        self.timeout = float(timeout)
        self.max_out_kb = int(max_out_kb)
        self.allow_net = bool(allow_net)
        self.run_id = (name or "run") + "-" + uuid.uuid4().hex[:8]
        self.dir = SANDBOX_ROOT / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.t0 = time.time()
        self.artifacts: list = []

    # ── ② 手桥接：把输入送进去 ──
    def put_text(self, rel: str, text: str) -> dict:
        p = self.dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return {"ok": True, "放入": str(p.relative_to(self.dir)), "字节": p.stat().st_size}

    def put_file(self, src: str | Path, rel: str = "") -> dict:
        s = Path(src)
        if not s.is_file():
            return {"ok": False, "reason": "源文件不存在: %s" % src}
        dst = self.dir / (rel or s.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(s, dst)
        return {"ok": True, "放入": str(dst.relative_to(self.dir)), "字节": dst.stat().st_size}

    def _env(self) -> dict:
        if self.allow_net:
            return dict(os.environ)
        # 出网墙：默认剥掉代理与云凭据，跑不满网
        e = {k: v for k, v in os.environ.items()
             if not any(s in k.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "PROXY", "API_KEY"))}
        e["GBT_SANDBOX"] = "1"
        e["GBT_SANDBOX_NET"] = "off"
        return e

    def _bridge_out(self) -> list:
        """③ 输出桥接：扫沙盒里的文件（除输入脚本外都算产物），算 hash/字节。"""
        out = []
        for p in sorted(self.dir.rglob("*")):
            if p.is_file() and p.suffix not in (".py",):
                out.append({"文件": str(p.relative_to(self.dir)), "sha256": _sha(p)[:16],
                            "字节": p.stat().st_size})
        return out

    def run(self, cmd: list, *, stdin_text: str = "") -> dict:
        exe = Path(str(cmd[0])).name.lower()
        if exe not in CMD_WHITELIST:
            return {"ok": False, "reason": "命令不在白名单: %s（白名单 %s）" % (exe, list(CMD_WHITELIST))}
        try:
            cp = subprocess.run(cmd, cwd=str(self.dir), capture_output=True, text=True,
                                timeout=self.timeout, env=self._env(),
                                input=stdin_text if stdin_text else None)
            rc, so, se = cp.returncode, cp.stdout or "", cp.stderr or ""
        except subprocess.TimeoutExpired:
            rc, so, se = -9, "", "沙盒超时（%.0fs）" % self.timeout
        ms = int((time.time() - self.t0) * 1000)
        self.artifacts = self._bridge_out()
        rec = {"ok": rc == 0, "退出码": rc, "ms": ms, "沙盒": str(self.dir.relative_to(ROOT)),
               "stdout": so[: self.max_out_kb * 1024], "stderr": se[: self.max_out_kb * 1024],
               "产物": self.artifacts, "出网": self.allow_net}
        self._log(rec, cmd)
        return rec

    def run_python(self, code: str, *, args: dict | None = None) -> dict:
        self.put_text("job.py", code)
        if args:
            self.put_text("args.json", json.dumps(args, ensure_ascii=False))
        return self.run([sys.executable, "job.py"])

    def _log(self, rec: dict, cmd: list) -> None:
        try:
            LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "沙盒": rec["沙盒"],
                                    "命令": [str(c)[:60] for c in cmd], "ok": rec["ok"],
                                    "退出码": rec["退出码"], "ms": rec["ms"],
                                    "产物数": len(rec["产物"])}, ensure_ascii=False) + chr(10))
        except Exception as e:
            _swallow(__file__, e)

    # ── 模型槽：模型在沙盒里被调用，输出也走桥接回来 ──
    def model(self, prompt: str, *, backend: str = "cloud", face: str = "文本", tentacle: str = "t001",
              local_model: str = "qwen3:0.6b", timeout: float = 60.0) -> dict:
        """在沙盒肚子里调模型：cloud → 云插件；local → 本机 ollama。"""
        t0 = time.time()
        out: dict
        if backend == "cloud":
            from core import cloud_plugin as CP
            # ★判据+自动换人：云调用失败/超时就重试一次，还不行自动切本地（不空手回来）
            r, txt, tries = None, "", 0
            for tries in (1, 2):
                try:
                    r = CP.invoke(tentacle, prompt, face=face, timeout=min(float(timeout), 45.0))
                except Exception as e:  # noqa: BLE001
                    r = {"ok": False, "reason": "%s: %s" % (type(e).__name__, e)}
                res = (r or {}).get("结果") or {}
                txt = res.get("出字") or ""
                if not txt and isinstance(res.get("原文前 200"), str):
                    txt = res["原文前 200"][:200]
                if (r or {}).get("ok") and txt:
                    break
            if (r or {}).get("ok") and txt:
                out = {"ok": True, "后端": "云插件", "触手": tentacle, "面": face, "出字": txt,
                       "原始": (r or {}).get("结果"), "ms": (r or {}).get("ms"), "尝试": tries}
            else:
                # 换人：切本地 ollama（本机装了什么就用什么）
                exe = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
                out = {"ok": False, "后端": "本地ollama（云失败后换人）", "触手": tentacle, "云失败原因":
                       str(((r or {}).get("结果") or {}).get("reason") or (r or {}).get("reason") or "")[:160],
                       "尝试": tries}
                if exe.is_file():
                    try:
                        cp = subprocess.run([str(exe), "run", local_model, prompt], capture_output=True,
                                            text=True, encoding="utf-8", errors="replace", timeout=max(60.0, timeout))
                        t = (cp.stdout or "").strip()
                        out.update({"ok": bool(t), "出字": t[:2000], "模型": local_model, "退出码": cp.returncode})
                    except subprocess.TimeoutExpired:
                        out["reason"] = "本地也超时"
                else:
                    out["reason"] = "本机没有 ollama，无法换人"
        else:
            exe = Path.home() / "AppData" / "Local" / "Programs" / "Ollama" / "ollama.exe"
            out = {"ok": False, "后端": "本地ollama", "模型": local_model}
            if not exe.is_file():
                out["reason"] = "ollama 不在"
            else:
                try:
                    cp = subprocess.run([str(exe), "run", local_model, prompt], capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=timeout)
                    out.update({"ok": cp.returncode == 0, "出字": (cp.stdout or "").strip()[:2000],
                                "退出码": cp.returncode, "stderr": (cp.stderr or "")[:300]})
                except subprocess.TimeoutExpired:
                    out["reason"] = "本地模型超时（%.0fs）" % timeout
        out["沙盒"] = str(self.dir.relative_to(ROOT))
        out["秒"] = round(time.time() - t0, 2)
        # 输出桥接：把模型的出字**落成产物**（这样工作流能取件）
        if out.get("出字"):
            self.put_text("out/model_reply.txt", str(out["出字"]))
        self.artifacts = self._bridge_out()
        out["产物"] = self.artifacts
        self._log({**out, "退出码": out.get("退出码", 0), "ms": int(out["秒"] * 1000)}, ["model:" + backend])
        # ★ 往这根触手的**随身库一丢**（省能源：不打断主脑；主脑 inbox() 慢看）
        try:
            from core import tentacle_store as TS
            if backend == "cloud":
                TS.report(tentacle, "云插件调用（面 %s）" % face,
                          str(out.get("出字") or "")[:300], level="常规",
                          payload={"后端": out.get("后端"), "秒": out.get("秒"), "沙盒": out.get("沙盒")})
            else:
                TS.stash(tentacle, "model/%s/%s" % (backend, self.run_id),
                         str(out.get("出字") or "")[:500], kind="备忘")
            for a in self.artifacts:
                TS.drop(tentacle, a["文件"], a.get("sha256", ""), a.get("字节", 0), "沙盒产物")
            out["已落随身库"] = True
        except Exception as exc:  # noqa: BLE001
            out["落库失败"] = "%s: %s" % (type(exc).__name__, exc)
        return out


__all__ = ["Sandbox", "SANDBOX_ROOT", "LEDGER", "CMD_WHITELIST"]
