# skills/integrate.py —— 把规则内核注入大脑/工程师, 并注册全部原生能力
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from skills.rules import EngineeringRules, rules_text
from skills.diagram import DiagramSkill
from skills.imagegen import ImageSkill
from skills.native import SkillRegistry, SkillContext


def build_registry(ledger=None, brain=None, devour=None, panel=None):
    reg = SkillRegistry(ledger=ledger, brain=brain, devour=devour, panel=panel)
    reg.register(EngineeringRules())
    try:
        from skills.native_codex import CodexTool

        reg.register(CodexTool(ledger=ledger, brain=brain))   # Codex = V9 的编程工具之一
    except Exception:
        pass
    try:
        from skills.engine import CoderRouter

        reg.register(CoderRouter(brain, workspace="."))   # coder.engine@1
    except Exception:
        pass
    try:
        from senses.voice import VoiceAdapter

        reg.register(VoiceAdapter(ledger=ledger, brain=brain))  # voice.io@1
    except Exception:
        pass
    reg.register(DiagramSkill(devour=devour, brain=brain))
    reg.register(ImageSkill(devour=devour))
    return reg


def inject_rules(brain, mode="full"):
    """规则包注入大脑: 作为系统提示前缀, 对 chat/ask 全部生效"""
    prefix = rules_text(mode)
    orig = brain.chat
    def patched(messages, **kw):
        msgs = list(messages)
        if msgs and msgs[0].get("role") == "system":
            msgs = [{"role": "system", "content": prefix + "\n\n" + msgs[0]["content"]}] + msgs[1:]
        else:
            msgs = [{"role": "system", "content": prefix}] + msgs
        return orig(msgs, **kw)
    brain.chat = patched
    brain.rules_prefix = prefix
    return brain
