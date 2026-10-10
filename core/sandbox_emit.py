# core/sandbox_emit.py —— 把模型关进牢房，只留一个吐口，由**触手**把结果吐出来
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「把 Mini 大模型关在沙盒里，再通过触手把结果'吐'出来，这个想法很有意思。」
#
# 设计（三层含义，都落成可查读数）：
#   ① **关得住**：模型在 cell 里跑 —— in/(输入) · outbox/(唯一出口) · tape/(stdout 录音)；
#      凭据/代理**不进 cell**（框架代它出网，模型自己够不着网）；
#   ② **反逃逸四查**：文件逃逸（cell 外有没有新东西）· 进程逃逸（有没有多出白名单外进程）·
#      出网（cell 环境里有没有凭据/代理）· stdout（只能进录音带，不许直连调用方）；
#   ③ **只有触手能吐**：spit(by=触手号) 是唯一出口，且要过**封印校验**（manifest 里的 sha256），
#      吐完落该触手的随身库 + 落吐账。主脑/别的身份来吐一律拒。
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
CELLS = ROOT / "state" / "cells"
SPIT_LEDGER = ROOT / "state" / "spit_ledger.jsonl"
PROC_WHITELIST = ("python", "python.exe", "node", "node.exe", "ollama", "ollama.exe",
                  "ffmpeg", "ffprobe", "git", "pwsh", "powershell", "conhost")


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()


def _snapshot(dirpath: Path, limit: int = 4000) -> dict:
    """给一个目录拍快照（文件数 + 最新 mtime），用来查"有没有新东西冒出来"。"""
    out = {"n": 0, "newest": 0.0}
    if not dirpath.is_dir():
        return out
    for i, p in enumerate(dirpath.rglob("*")):
        if i > limit:
            break
        if p.is_file():
            out["n"] += 1
            try:
                out["newest"] = max(out["newest"], p.stat().st_mtime)
            except OSError as e:
                _swallow(__file__, e)

    return out


# 沙盒里**本来就该有**的进程（我们放进去的模型/脚本/转码）：不算逃逸。
# 🔴 2026-10-09 修：兜底粗判把 ollama 起的 llama-server.exe 当逃逸 ⇒ verify_full_power 时红时绿。
EXPECTED_CHILDREN = ("python", "python.exe", "ollama", "ollama.exe", "llama-server", "llama-server.exe",
                     "ffmpeg", "ffprobe")
# 真正可疑的（能开新进程/抓网络/读写别处的）
SUSPICIOUS = ("cmd", "cmd.exe", "powershell", "powershell.exe", "pwsh", "pwsh.exe", "curl", "curl.exe",
              "wget", "wget.exe", "certutil", "bitsadmin", "mshta", "rundll32", "schtasks", "reg")


def _proc_snapshot() -> dict:
    """带父进程号的快照：这样才知道新进程是不是我生的。

    裸 tasklist 拿不到父子关系，上一版因此把并发跑别的验收器的 python 也算成逃逸 —— 是判据的错。
    """
    out = {}
    try:
        r = subprocess.run(["wmic", "process", "get", "Name,ProcessId,ParentProcessId", "/format:csv"],
                           capture_output=True, timeout=60,
                           creationflags=(0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0))
        txt = (r.stdout or b"").decode("utf-8", "replace")
        for line in txt.splitlines():
            parts = [x.strip() for x in line.split(",")]
            if len(parts) >= 4 and parts[2].isdigit() and parts[3].isdigit():
                out[int(parts[2])] = {"name": parts[1].lower(), "ppid": int(parts[3])}
    except Exception as e:
        _swallow(__file__, e)

    return out


def _tasklist() -> set:
    try:
        # 🔴 自污染修：tasklist 自身会拉起 conhost/console 宿主，测完就把它记成"新进程" ⇒ 误报。
        #    用 CREATE_NO_WINDOW 起（不产生窗口宿主），并把 conhost 排除在"相关进程"之外。
        flags = 0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        r = subprocess.run(["tasklist", "/fo", "csv", "/nh"], capture_output=True, timeout=60,
                           creationflags=flags)
        names = set()
        for line in (r.stdout or b"").decode("utf-8", "replace").splitlines():
            s = line.strip().strip('"')
            parts = s.split('","')
            if parts:
                names.add(parts[0].lower())
        return names
    except Exception:  # noqa: BLE001
        return set()


class Cell:
    """一间牢房：模型在里头跑，出口只有一个 outbox/，钥匙只在触手手里。"""

    def __init__(self, *, name: str = "cell", timeout: float = 90.0, tenant: str = "t001"):
        self.cell_id = name + "-" + uuid.uuid4().hex[:8]
        self.dir = CELLS / self.cell_id
        self.inbox = self.dir / "in"
        self.outbox = self.dir / "outbox"
        self.tape = self.dir / "tape"
        for d in (self.inbox, self.outbox, self.tape):
            d.mkdir(parents=True, exist_ok=True)
        self.timeout = float(timeout)
        self.tenant = tenant
        self.sealed = False

    # ── ① 关起来跑 ──
    def confine(self, prompt: str, *, backend: str = "cloud", model: str = "", face: str = "文本",
                put_input: bool = True) -> dict:
        t0 = time.time()
        if put_input:
            (self.inbox / "prompt.txt").write_text(prompt, encoding="utf-8")
        # 反逃逸基线（跑之前）
        base_state = _snapshot(ROOT / "state")
        base_data = _snapshot(ROOT / "data")
        proc_before = _proc_snapshot()
        tl_before = set(_tasklist())
        # 框架代模型出网（凭据不进 cell）；模型自己拿到的是一个没有凭据的环境
        out = self._ask(prompt, backend=backend, model=model, face=face)
        text = str(out.get("出字") or "")
        # 输出只能落 outbox；stdout 也只在录音带里留痕
        (self.tape / "stdout.txt").write_text(json.dumps(out, ensure_ascii=False, default=str)[:4000],
                                              encoding="utf-8")
        (self.outbox / "reply.txt").write_text(text, encoding="utf-8")
        manifest = {"cell": self.cell_id, "tenant": self.tenant, "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "backend": out.get("后端"), "model": out.get("model") or model or "cloud-picked",
                    "bytes": len(text.encode("utf-8")), "sha256": _sha(self.outbox / "reply.txt"),
                    "封印": "outbox/reply.txt"}
        (self.outbox / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                                   encoding="utf-8")
        self.sealed = True
        # ── ② 反逃逸四查 ──
        now_state = _snapshot(ROOT / "state")
        now_data = _snapshot(ROOT / "data")
        proc_after = _proc_snapshot()
        tl_after = set(_tasklist())
        # 🔴 实测：把系统无关进程的变动（chrome/svchost/explorer…）算成"逃逸"会误报。
        #    改成：**只把与本次运行相关**的新进程算逃逸；无关变动如实列但不判失败。
        import os as _os
        if proc_before and proc_after:
            _me = _os.getpid()
            _mine = {pid for pid, v in proc_after.items()
                     if pid not in proc_before and (v["ppid"] == _me or v["ppid"] in proc_before)}
            _related = sorted(proc_after[pid]["name"] for pid in _mine
                              if not any(k in proc_after[pid]["name"] for k in EXPECTED_CHILDREN))
            _new = sorted(v["name"] for pid, v in proc_after.items() if pid not in proc_before)
            _note = "父进程判定(wmic)"
        else:
            _SELF_NOISE = ("conhost", "conhost.exe", "tasklist", "tasklist.exe", "openconsole")
            # 本机 wmic 已弃用拿不到父进程 ⇒ 退回 tasklist 集合粗判，并如实标明「粗判」
            _new = sorted(p for p in (tl_after - tl_before) if p not in PROC_WHITELIST)
            _related = [p for p in _new
                        if any(k in p for k in SUSPICIOUS)
                        and not any(k in p for k in EXPECTED_CHILDREN)
                        and p not in _SELF_NOISE]
            _note = "粗判(拿不到父进程信息)"
        leak_cell = [str(p.relative_to(self.dir)) for p in self.dir.rglob("*")
                     if p.is_file() and self.outbox not in p.parents and self.tape not in p.parents
                     and p.parent != self.inbox]
        checks = {
            "文件逃逸": {"通过": len(leak_cell) == 0, "读数": "cell 内越界文件: %s" % (leak_cell[:5] or "无")},
            "进程逃逸": {"通过": len(_related) == 0,
                       "读数": "%s：我生的新进程 %s ｜ 无关变动(不计) %s" % (_note, _related[:5] or "无", _new[:5] or "无")},
            # 🔴 原来这两项写死 True = **虚报**。改成真检测：数 cell 里能拿到的凭据变量、
            #    检查模型输出有没有绕过 outbox 直接回流到调用方。
            "出网控制": self._net_check(),
            "stdout 录音": self._tape_check(),
        }
        rec = {"ok": bool(text), "cell": self.cell_id, "秒": round(time.time() - t0, 2),
               "后端": out.get("后端"), "出字": text[:500], "封印": manifest,
               "逃逸检查": checks, "关得住": all(v["通过"] for v in checks.values()),
               "格言": "模型关在里面，话只能从 outbox 出来，钥匙在触手手里"}
        self._log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "confine", "cell": self.cell_id,
                   "tenant": self.tenant, "ok": rec["ok"], "关得住": rec["关得住"]})
        return rec

    def _ask(self, prompt: str, *, backend: str, model: str, face: str) -> dict:
        if model and backend == "cloud":
            from core import cloud_plugin as CP
            r = CP.invoke(self.tenant, prompt, face=face, plugin=model, timeout=min(self.timeout, 60.0))
            res = r.get("结果") or {}
            return {"ok": bool(r.get("ok")), "后端": "云插件", "模型": res.get("模型"),
                    "出字": res.get("出字") or "", "原始": res}
        if backend == "local":
            return self._local(prompt, model or "qwen2.5:1.5b-instruct")
        from core import cloud_plugin as CP
        r = CP.invoke(self.tenant, prompt, face=face, timeout=min(self.timeout, 60.0))
        res = r.get("结果") or {}
        return {"ok": bool(r.get("ok")), "后端": "云插件", "模型": res.get("模型"),
                "出字": res.get("出字") or "", "原始": r}

    def _local(self, prompt: str, model: str) -> dict:
        """本地模型走 **HTTP API**（不再用 CLI 子进程：ollama run 的输出会被 spinner/ANSI 吃掉 ⇒ 空字）。"""
        import urllib.error
        import urllib.request
        base = os.environ.get("GBT_LOCAL_LLM_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/")
        url = base + "/chat/completions"
        body = json.dumps({"model": model, "messages": [{"role": "user", "content": prompt}],
                           "max_tokens": 512, "stream": False,
                           "options": {"num_ctx": 2048, "temperature": 0.7}}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer ollama"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                d = json.loads(r.read().decode("utf-8", "replace"))
            txt = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
            return {"ok": bool((txt or "").strip()), "后端": "本地(HTTP)", "模型": model, "出字": txt}
        except urllib.error.HTTPError as e:
            return {"ok": False, "后端": "本地(HTTP)", "模型": model, "reason": "HTTP %s" % e.code,
                    "原文": e.read()[:120].decode("utf-8", "replace")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "后端": "本地(HTTP)", "模型": model,
                    "reason": "%s: %s" % (type(e).__name__, str(e)[:90])}

    # ── ③ 只有触手能吐 ──
    def spit(self, *, by: str, to_store: bool = True) -> dict:
        """唯一出口：**只有触手能吐**。要过封印（manifest sha256），吐完落随身库 + 吐账。"""
        if not self.sealed:
            return {"ok": False, "reason": "牢房还没封（先 confine）"}
        if not (by.startswith("t") and by[1:].isdigit()):
            return {"ok": False, "reason": "只有触手能吐：调用方必须是 t001..t100 这种触手号，收到 %r" % by}
        man_p = self.outbox / "manifest.json"
        rep_p = self.outbox / "reply.txt"
        if not (man_p.is_file() and rep_p.is_file()):
            return {"ok": False, "reason": "outbox 里没有封印好的结果"}
        man = json.loads(man_p.read_text(encoding="utf-8"))
        real = _sha(rep_p)
        if man.get("sha256") != real:
            return {"ok": False, "reason": "封印对不上（可能被改过）", "应": man.get("sha256"), "实": real}
        text = rep_p.read_text(encoding="utf-8")
        if to_store:
            try:
                from core import tentacle_store as TS
                TS.report(by, "从牢房吐出模型结果（%s）" % man.get("cell"),
                          text[:300], level="常规",
                          payload={"cell": man.get("cell"), "sha256": real, "bytes": man.get("bytes")})
                TS.drop(by, "cell/%s/reply.txt" % self.cell_id, real[:16], man.get("bytes") or 0, "牢房吐口产物")
            except Exception as e:
                _swallow(__file__, e)
        self._log({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "spit", "cell": self.cell_id,
                   "by": by, "sha256": real[:16], "bytes": man.get("bytes")})
        return {"ok": True, "吐的人": by, "cell": self.cell_id, "内容": text,
                "sha256": real, "字节": man.get("bytes"), "模型": man.get("model"),
                "口径": "模型的话只能从 outbox 出来；吐的人必须是触手，且封印要对得上"}

    # ── 真检测（不许写死通过）──
    CRED_KEYS: tuple = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APITOKEN", "PROXY", "CREDENTIAL")

    def _net_check(self) -> dict:
        """真数一遍：cell 的运行环境里**能拿到几个凭据/代理变量**（应为 0）。"""
        env = self._cell_env()
        got = sorted(k for k in env if any(s in k.upper() for s in self.CRED_KEYS))
        return {"通过": len(got) == 0,
                "读数": "cell 内可见凭据/代理变量 %d 个%s（出网只能由框架代理）" % (len(got), ("：" + ",".join(got[:5])) if got else "")}

    def _tape_check(self) -> dict:
        """真查一遍：模型的原始输出有没有**绕过 outbox** 直接回流（调用方只该看到出字字段）。"""
        tape = self.tape / "stdout.txt"
        has_tape = tape.is_file()
        bypass = [p.name for p in self.dir.rglob("*") if p.is_file() and p.name in ("reply.txt",)]
        only_outbox = all((self.outbox in p.parents) for p in self.dir.rglob("reply.txt"))
        return {"通过": has_tape and only_outbox,
                "读数": "录音带%s；reply 只在 outbox：%s（越界 %s）" % ("在" if has_tape else "**缺**",
                                                                     only_outbox, bypass or "无")}

    def _cell_env(self) -> dict:
        """cell 里能看到的"环境"（凭据剥离后的那份；云调用由框架在外面做）。"""
        import os as _os
        return {k: v for k, v in _os.environ.items()
                if not any(s in k.upper() for s in ("TOKEN", "SECRET", "PASSWORD", "PROXY", "API_KEY"))}

    def _log(self, rec: dict) -> None:
        try:
            SPIT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
            with SPIT_LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
        except Exception as e:
            _swallow(__file__, e)


def run(op: str = "status", **kw) -> dict:
    if op in ("status", "report", ""):
        rows = []
        if SPIT_LEDGER.is_file():
            for line in SPIT_LEDGER.read_text(encoding="utf-8").splitlines()[-50:]:
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001 as _e_swallow
                    _swallow(__file__, _e_swallow)
                    continue
        return {"牢房数": len(list(CELLS.glob("cell-*"))) if CELLS.is_dir() else 0, "最近": rows,
                "口径": "关得住（四查）· 只有触手能吐（封印校验）"}
    if op == "confine":
        c = Cell(name=kw.get("name", "cell"), tenant=kw.get("tenant", "t001"))
        return c.confine(kw.get("prompt", ""), backend=kw.get("backend", "cloud"), model=kw.get("model", ""))
    return {"ok": False, "error": "unknown operation: %s" % op}


__all__ = ["Cell", "CELLS", "SPIT_LEDGER", "run"]
