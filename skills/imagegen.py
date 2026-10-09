# skills/imagegen.py —— 图像提示词编译器 + 可替换生成后端
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 来源: https://github.com/freestylefly/awesome-gpt-image-2 (提示词/模板库)
#   模板库≠生成API; 这里只把需求编译成结构化提示词, 生成交给 ImageBackend
from core.swallow import swallow as _swallow
import os, json, base64, time, urllib.request
from pathlib import Path

LIB = Path(os.environ.get("IMAGE_LIB", "skills/data/style-library.json"))

TEMPLATES = {
    "ui":          ["type", "platform", "product", "layout", "style", "content", "constraints"],
    "infographic": ["type", "topic", "audience", "structure", "style", "constraints"],
    "poster":      ["type", "subject", "composition", "style", "palette", "constraints"],
}


def load_library():
    if LIB.exists():
        try:
            return json.loads(LIB.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def compile_prompt(spec, tpl="ui") -> dict:
    keys = TEMPLATES.get(tpl, TEMPLATES["ui"])
    fields = {k: spec.get(k) for k in keys if spec.get(k) is not None}
    lib, style_hint, want = load_library(), None, (spec.get("style") or "")
    if isinstance(lib, dict):
        for name, meta in lib.items():
            if want and want.lower() in name.lower():
                style_hint = meta
                break
    prompt = f"【图像任务 · {tpl}】\n" + "\n".join(
        f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in fields.items())
    if style_hint:
        prompt += "\n参考风格: " + json.dumps(style_hint, ensure_ascii=False)[:800]
    return {"template": tpl, "fields": fields, "prompt": prompt,
            "style_ref": style_hint, "lib": str(LIB)}


class ImageBackend:
    """可替换生成后端: 默认未配置即降级; 可插 OpenAI 兼容 /v1/images"""
    def __init__(self, base_url=None, api_key=None, model=None, client=None):
        self.base_url = base_url or os.environ.get("IMAGE_BASE_URL")
        self.api_key = api_key or os.environ.get("IMAGE_API_KEY")
        self.model = model or os.environ.get("IMAGE_MODEL", "gpt-image-1")
        self.client = client
    def available(self):
        return bool(self.base_url and self.api_key) or self.client is not None
    def generate(self, prompt, size="1024x1024", n=1):
        if not self.available():
            raise RuntimeError("图像后端未配置(IMAGE_BASE_URL/IMAGE_API_KEY)")
        if self.client is None:
            from openai import OpenAI
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        r = self.client.images.generate(model=self.model, prompt=prompt, size=size, n=n)
        return [d.b64_json or d.url for d in r.data]


class ImageSkill:
    name, version = "image", "gpt-image-tpl-1.0"
    def __init__(self, backend=None, devour=None, out_dir="devoured/images"):
        self.backend = backend or ImageBackend()
        self.devour = devour
        self.out = Path(out_dir); self.out.mkdir(parents=True, exist_ok=True)
    def probe(self):
        from skills.native import Availability
        return Availability(True, detail={"backend_ready": self.backend.available(),
                                          "lib": str(LIB), "lib_found": LIB.exists()})
    def spec(self) -> dict:
        return {
            "inputs": {
                "op": {"type": "enum", "values": ["compile", "generate"],
                       "required": True, "default": "compile"},
                "spec": {"type": "object", "required": True,
                         "help": "模板字段（type/platform/... 见 TEMPLATES）"},
                "template": {"type": "enum", "values": ["ui", "infographic", "poster"],
                             "default": "ui"},
                "size": {"type": "string", "default": "1024x1024",
                         "help": "仅 op=generate"},
            },
            "outputs": {"prompt": {"type": "string"}, "fields": {"type": "object"},
                        "artifacts": {"type": "array"}},
            "idempotent": False, "risk": "low",
        }

    def run(self, ctx, request):
        from skills.native import SkillResult
        op = request.get("op", "compile")
        if op == "compile":
            return SkillResult(True, output=compile_prompt(request.get("spec", {}),
                                                           request.get("template", "ui")))
        if op == "generate":
            comp = compile_prompt(request.get("spec", {}), request.get("template", "ui"))
            if not self.backend.available():
                return SkillResult(False, output=comp, error="图像后端未配置，已降级为仅编译提示词")
            try:
                items = self.backend.generate(comp["prompt"], request.get("size", "1024x1024"))
            except Exception as e:
                return SkillResult(False, output=comp, error=f"生成失败: {e}")
            arts = []
            for i, item in enumerate(items):
                p = self.out / f"{int(time.time())}_{i}.png"
                if isinstance(item, str) and item.startswith("http"):
                    urllib.request.urlretrieve(item, p)
                else:
                    p.write_bytes(base64.b64decode(item))
                from skills.native import _sha
                arts.append({"path": str(p), "sha256": _sha(str(p)), "kind": "image"})
                if self.devour:
                    try:
                        self.devour.archive_artifact(str(p), kind="image")
                    except Exception as e:
                        _swallow(__file__, e)
            return SkillResult(True, output=comp, artifacts=arts)
        return SkillResult(False, error=f"未知 op: {op}")
