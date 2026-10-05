# core/memory/longterm.py —— memory.longterm@1 (Hindsight)
class LongTermMemory:
    def run(self, action, content=None, query=None,
            document_id=None, metadata=None): ...
    def spec(self):
        return {
            "inputs": {
                "action":      {"type": "enum", "required": True,
                                "values": ["retain", "recall", "reflect"]},
                "content":     {"type": "any", "help": "text/image/file 内容块"},
                "query":       {"type": "string"},
                "document_id": {"type": "string", "help": "来源追踪"},
                "metadata":    {"type": "object"},
            },
            "outputs": {"facts": {"type": "array"}, "answer": {"type": "string"},
                        "sources": {"type": "array"}},
            "idempotent": False, "risk": "low",
        }
