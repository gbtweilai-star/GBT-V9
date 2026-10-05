# skills/diagram.py —— 图表能力: typed JSON IR → 校验 → 自包含 HTML/SVG
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 来源: https://github.com/tt-a1i/archify
#   支持 Architecture/Workflow/Sequence/DataFlow/Lifecycle
#   源码图必须带 --repo-root 证据校验; 截图推断的图显式标注"非源码验证"
import os, json, shutil, subprocess, tempfile
from pathlib import Path

ARCHIFY_BIN = os.environ.get("ARCHIFY_BIN", "archify")


def _bin():
    return shutil.which(ARCHIFY_BIN) or shutil.which("archify")


class DiagramSkill:
    name, version = "diagram", "archify-1.0"
    def __init__(self, out_dir="panel/exports", devour=None, brain=None):
        self.out = Path(out_dir); self.out.mkdir(parents=True, exist_ok=True)
        self.devour, self.brain = devour, brain

    def probe(self):
        from skills.native import Availability
        b = _bin()
        return Availability(bool(b), "" if b else "未找到 archify CLI",
                            detail={"bin": b or ARCHIFY_BIN})

    def _validate(self, ir_path, quality, repo_root):
        cmd = [_bin(), "validate", "workflow", str(ir_path), "--quality", quality, "--json"]
        if repo_root:
            cmd += ["--repo-root", repo_root]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        try:
            return json.loads(r.stdout or "{}")
        except Exception:
            return {"ok": False, "raw": (r.stdout + r.stderr)[-2000:]}

    def _deliver(self, ir_path, out_path, quality, repo_root):
        cmd = [_bin(), "deliver", "workflow", str(ir_path), str(out_path),
               "--quality", quality, "--json"]
        if repo_root:
            cmd += ["--repo-root", repo_root]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.returncode == 0, (r.stdout + r.stderr)[-2000:]

    def render(self, ir, name="diagram", repo_root=None, source_kind="code", quality="showcase"):
        with tempfile.TemporaryDirectory() as td:
            irp = Path(td) / f"{name}.json"
            irp.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
            v = self._validate(irp, quality, repo_root)
            outp = self.out / f"{name}.html"
            ok, log = self._deliver(irp, outp, quality, repo_root)
            warnings = ["截图推断生成，非源码验证"] if source_kind == "screenshot" else []
            arts = []
            if ok and outp.exists():
                from skills.native import _sha
                arts.append({"path": str(outp), "sha256": _sha(outp), "kind": "diagram"})
                if self.devour:
                    try:
                        self.devour.archive_artifact(str(outp), kind="diagram")
                    except Exception:
                        pass
            return {"ok": ok, "html": str(outp), "validate": v,
                    "warnings": warnings, "artifacts": arts, "log": log}

    def spec(self) -> dict:
        return {
            "inputs": {
                "ir": {"type": "object", "required": True, "help": "typed JSON IR"},
                "name": {"type": "string", "default": "diagram"},
                "repo_root": {"type": "string"},
                "source_kind": {"type": "enum", "values": ["code", "screenshot"],
                                "default": "code"},
            },
            "outputs": {"html": {"type": "string"}, "validate": {"type": "object"},
                        "artifacts": {"type": "array"}},
            "idempotent": True, "risk": "low",
        }

    def run(self, ctx, request):
        from skills.native import SkillResult
        ir = request.get("ir")
        if not ir:
            return SkillResult(False, error="缺少 ir(typed JSON IR)")
        out = self.render(ir, request.get("name", "diagram"), request.get("repo_root"),
                          request.get("source_kind", "code"))
        return SkillResult(out["ok"], output=out, artifacts=out["artifacts"],
                           warnings=out["warnings"], error="" if out["ok"] else "渲染失败")
