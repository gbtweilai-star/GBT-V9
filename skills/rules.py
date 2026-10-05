# skills/rules.py —— 工程规则内核（提炼自 ponytail 的 7 条）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 来源: https://github.com/DietrichGebert/ponytail (SKILL.md)
#
# 形态: 版本化规则包(纯文本) → 注入 Brain/Coder 上下文
#       + 确定性预检(不花 LLM token): YAGNI/复用/极简/安全底线
import re

RULES_VERSION = "ponytail-1.0"

RULES = [
    {"id": "yagni",         "level": "gate", "text": "先问需求是否真的需要做；不做没人要的功能(YAGNI)"},
    {"id": "reuse",         "level": "gate", "text": "先读现有代码并复用；优先标准库 > 平台原生 > 已装依赖"},
    {"id": "minimal",       "level": "gate", "text": "能一行别造复杂；只写刚好够用的最少代码"},
    {"id": "no_abstraction","level": "warn", "text": "不擅自加抽象/依赖/脚手架/'以后可能用'的配置"},
    {"id": "root_cause",    "level": "gate", "text": "先理解再精简：读代码追调用链；修 bug 追根因不贴创可贴"},
    {"id": "safety_floor",  "level": "gate", "text": "极简不牺牲正确与安全：保留信任边界校验/防数据丢失/用户明确要求"},
    {"id": "code_first",    "level": "warn", "text": "回复代码优先，少写未要求的解释"},
]


def rules_text(mode="full"):
    """mode: lite(3条) / full(7条) / ultra(7条+强化)"""
    rs = RULES[:3] if mode == "lite" else RULES
    body = "\n".join(f"{i+1}. [{r['level']}] {r['text']}" for i, r in enumerate(rs))
    out = f"【工程规则 · {RULES_VERSION} · {mode}】\n{body}"
    if mode == "ultra":
        out += "\n\n强化: 默认选择删除而非新增；任何新增必须能指出被它替换掉的旧代码。"
    return out


_TODO = re.compile(r"\b(TODO|FIXME|XXX)\b", re.I)
_IMP = re.compile(r"^\s*(?:import|from)\s+([a-zA-Z0-9_\.]+)", re.M)
_BARE = re.compile(r"except\s*(?:Exception)?\s*:\s*\n\s*(?:pass|\.\.\.)")
_ABS = re.compile(r"class\s+\w*(?:Abstract|Base|Factory|Manager|Helper)\w*", re.I)
_SECRET = re.compile(r"(sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def precheck(files: dict, existing_imports=None) -> dict:
    """确定性预检; files={path: content}"""
    findings = []
    for path, content in (files or {}).items():
        content = content or ""
        if _TODO.search(content):
            findings.append({"rule": "minimal", "path": path, "hit": "存在TODO/占位", "severity": "warn"})
        for m in _IMP.finditer(content):
            mod = m.group(1).split(".")[0]
            if existing_imports is not None and mod not in existing_imports:
                findings.append({"rule": "reuse", "path": path, "hit": f"新依赖 {mod}", "severity": "warn"})
        if _BARE.search(content):
            findings.append({"rule": "safety_floor", "path": path, "hit": "裸 except pass 吞异常", "severity": "gate"})
        if _ABS.search(content):
            findings.append({"rule": "no_abstraction", "path": path, "hit": "疑似过度抽象类名", "severity": "warn"})
        if _SECRET.search(content):
            findings.append({"rule": "safety_floor", "path": path, "hit": "疑似硬编码密钥", "severity": "gate"})
    gates = [f for f in findings if f["severity"] == "gate"]
    return {"ok": not gates, "findings": findings, "gate_count": len(gates)}


class EngineeringRules:
    name, version = "rules", RULES_VERSION
    def __init__(self, mode="full"):
        self.mode = mode
    def probe(self):
        from skills.native import Availability
        return Availability(True, detail={"mode": self.mode, "count": len(RULES)})
    def spec(self) -> dict:
        return {
            "inputs": {
                "op": {"type": "enum", "values": ["text", "precheck"],
                       "required": True, "default": "text"},
                "files": {"type": "object",
                          "help": "op=precheck 时必填：{path: content}"},
                "existing_imports": {"type": "array",
                                     "help": "op=precheck 可选：已知依赖集合"},
            },
            "outputs": {"text": {"type": "string"},
                        "ok": {"type": "boolean"},
                        "findings": {"type": "array"},
                        "gate_count": {"type": "integer"}},
            "idempotent": True, "risk": "low",
        }

    def run(self, ctx, request):
        from skills.native import SkillResult
        op = request.get("op", "text")
        if op == "text":
            return SkillResult(True, output={"text": rules_text(self.mode)})
        if op == "precheck":
            return SkillResult(True, output=precheck(
                request.get("files", {}), set(request.get("existing_imports") or [])))
        return SkillResult(False, error=f"未知 op: {op}")
