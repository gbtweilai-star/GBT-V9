# core/coder.py —— 顶尖工程师大脑：计划→写码→跑测试→读错误→修复→循环
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, re, json, time, subprocess, tempfile
from pathlib import Path

PLAN_SYS = """你是顶尖软件工程师。给任务只输出JSON:
{"plan":["步骤..."],"files":[{"path":"...","content":"..."}],
 "tests":"如何验证","risks":["..."]}
规则: 代码必须完整可运行, 不留TODO占位; 不确定处明确标注而非猜测。"""

FIX_SYS = """你是顶尖软件工程师在修复失败。只输出JSON:
{"diagnosis":"根因","patch":[{"path":"...","content":"完整新内容"}],"explain":"改了什么"}
规则: 定位真根因不贴创可贴; 保持原有接口; 只输出完整文件内容。"""

class Coder:
    def __init__(self, brain, workdir=".", max_iters=5, test_cmd=None):
        self.brain, self.dir = brain, Path(workdir)
        self.max_iters = max_iters
        self.test_cmd = test_cmd or os.environ.get("TEST_CMD")

    def _llm(self, sys, user):
        return self.brain.chat([{"role":"system","content":sys},
                                {"role":"user","content":user}])

    def _write(self, files):
        written = []
        for f in files or []:
            p = self.dir / f["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f["content"], encoding="utf-8")
            written.append(f["path"])
        return written

    def _run_tests(self):
        if not self.test_cmd: return {"ok": None, "out": "未配置 TEST_CMD"}
        r = subprocess.run(self.test_cmd, shell=True, cwd=self.dir,
                           capture_output=True, text=True, timeout=600)
        return {"ok": r.returncode == 0,
                "out": (r.stdout + r.stderr)[-6000:]}

    def build(self, task: str) -> dict:
        """完整闭环：计划→写→测→(失败)修复，最多 max_iters 轮"""
        ctx = self._repo_context()
        plan = self._llm(PLAN_SYS, f"任务: {task}\n\n现有代码上下文:\n{ctx}")
        written = self._write(plan.get("files"))
        history = [{"iter": 0, "action": "plan+write", "files": written}]

        for i in range(1, self.max_iters + 1):
            t = self._run_tests()
            history.append({"iter": i, "action": "test", "ok": t["ok"]})
            if t["ok"] is True:
                if not written:            # 跑通但一步没落盘：不算成功，如实上报
                    return {"ok": False,
                            "reason": "测试通过但无文件产出（模型计划里没有 files）",
                            "plan": plan.get("plan"), "history": history}
                return {"ok": True, "iters": i, "files": written,
                        "plan": plan.get("plan"), "history": history}
            if t["ok"] is None:
                return {"ok": None, "reason": t["out"], "files": written,
                        "history": history}          # 无法验证，如实上报
            # 失败 → 读错误 → 修复
            fix = self._llm(FIX_SYS,
                f"任务:{task}\n测试输出:\n{t['out']}\n\n当前文件:\n{self._dump(written)}")
            written += self._write(fix.get("patch"))
            history.append({"iter": i, "action": "fix",
                            "diagnosis": fix.get("diagnosis"),
                            "files": [p["path"] for p in fix.get("patch", [])]})
        return {"ok": False, "reason": f"{self.max_iters}轮未通过", "history": history}

    def _repo_context(self, max_files=40, max_bytes=200000):
        """把工作目录现状喂给模型（只读文本，跳过噪音）"""
        skip = {".git","node_modules","__pycache__",".venv","dist","build",".next"}
        out, total = [], 0
        for p in sorted(self.dir.rglob("*")):
            if any(s in p.parts for s in skip) or not p.is_file(): continue
            if p.suffix not in {".py",".ts",".js",".tsx",".jsx",".go",".rs",".java",
                                ".json",".yaml",".yml",".toml",".md",".sql"}: continue
            try: txt = p.read_text(errors="ignore")[:8000]
            except Exception: continue
            total += len(txt)
            if total > max_bytes: break
            out.append(f"### {p.relative_to(self.dir)}\n{txt}")
            if len(out) >= max_files: break
        return "\n\n".join(out)

    def _dump(self, files):
        return "\n\n".join(
            f"### {f}\n{(self.dir/f).read_text(errors='ignore')[:6000]}"
            for f in files if (self.dir/f).exists())
