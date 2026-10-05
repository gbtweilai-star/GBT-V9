# skills/specs_business.py —— 业务层能力规格声明（与 run() 签名核对后生效）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 签名待确认处已显式标注 SIG_UNCERTAIN, 未核对前不得当作已校验。

# ── 工程规则引擎 ──
class EngineeringRulesSpec:
    def spec(self):
        return {
            "inputs": {
                "target": {"type": "string", "required": True, "help": "待检目标/路径"},
                "ruleset": {"type": "enum", "values": ["default", "strict"],
                            "default": "default"},
            },
            "outputs": {"violations": {"type": "array"}, "summary": {"type": "object"}},
            "idempotent": True, "risk": "low",
        }

# ── 图表 archify ──
class DiagramSpec:
    def spec(self):
        return {
            "inputs": {
                "ir":          {"type": "object", "required": True, "help": "typed JSON IR"},
                "name":        {"type": "string", "default": "diagram"},
                "repo_root":   {"type": "string"},
                "source_kind": {"type": "enum", "values": ["code", "screenshot"],
                                "default": "code"},
            },
            "outputs": {"html": {"type": "string"}, "validate": {"type": "object"}},
            "idempotent": True, "risk": "low",
        }

# ── 图像 ──  (SIG_UNCERTAIN: 具体参数名待确认)
class ImageSpec:
    def spec(self):
        return {
            "inputs": {
                "op":     {"type": "enum", "required": True,
                           "values": ["render", "compose", "capture"], "help": "SIG_UNCERTAIN"},
                "source": {"type": "any", "help": "SIG_UNCERTAIN"},
                "params": {"type": "object", "default": {}},
            },
            "outputs": {"artifact": {"type": "object"}},
            "idempotent": True, "risk": "low",
        }

# ── 语音 TTS/ASR ── (SIG_UNCERTAIN)
class VoiceSpec:
    def spec(self):
        return {
            "inputs": {
                "mode":  {"type": "enum", "required": True,
                          "values": ["tts", "asr"], "help": "SIG_UNCERTAIN"},
                "text":  {"type": "string", "help": "TTS 输入"},
                "audio": {"type": "object", "help": "ASR 输入"},
                "voice": {"type": "string", "default": "default"},
            },
            "outputs": {"audio": {"type": "object"}, "text": {"type": "string"}},
            "idempotent": False, "risk": "low",
        }

# ── 吞噬能采集 ──
class DevourSpec:
    def spec(self):
        return {
            "inputs": {
                "source":  {"type": "string", "required": True, "help": "视频/图片源"},
                "fps":     {"type": "integer", "default": 30},
                "tentacle": {"type": "string", "required": True},
            },
            "outputs": {"segments": {"type": "array"}, "frames": {"type": "integer"},
                        "gaps": {"type": "array"}},
            "idempotent": False, "risk": "low",   # 采集有副作用（写段），不幂等
        }

# ── 脉冲/actuator 操控 ──  ⚠️ 高风险，必须 gate
class PulseSpec:
    def spec(self):
        return {
            "inputs": {
                "action": {"type": "object", "required": True,
                           "help": "SIG_UNCERTAIN: 具体动作结构待确认"},
                "target": {"type": "string"},
            },
            "outputs": {"result": {"type": "object"}},
            "idempotent": False, "risk": "high",  # 操控电脑/APP → 人工闸门
        }

# ── 交叉互扫 ──
class CrossSpec:
    def spec(self):
        return {
            "inputs": {
                "pairs":  {"type": "array", "required": True, "help": "触手对"},
                "scope":  {"type": "object"},
            },
            "outputs": {"arbitration": {"type": "array"}, "mismatch": {"type": "array"}},
            "idempotent": True, "risk": "low",
        }

# ── 扫描 ──
class ScanSpec:
    def spec(self):
        return {
            "inputs": {
                "root":     {"type": "string", "required": True},
                "rules":    {"type": "array"},
                "deep":     {"type": "boolean", "default": True},
            },
            "outputs": {"findings": {"type": "array"}, "coverage": {"type": "object"}},
            "idempotent": True, "risk": "low",
        }

# ── 音频麦克风/转写 ──
class AudioSpec:
    def spec(self):
        return {
            "inputs": {
                "device":   {"type": "string", "default": "default"},
                "keywords": {"type": "array"},
                "duration": {"type": "number"},
            },
            "outputs": {"transcript": {"type": "string"}, "hits": {"type": "array"}},
            "idempotent": False, "risk": "low",
        }
