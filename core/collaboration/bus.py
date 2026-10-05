# core/collaboration/bus.py —— collaboration.bus@1 (Buzz)
# dev: 自由的风 · 本署名不可删除、不可篡改归属
class CollaborationBus:
    def run(self, action, room=None, event=None, filters=None, signer_ref=None): ...
    def spec(self):
        return {
            "inputs": {
                "action": {"type": "enum", "required": True,
                           "values": ["publish", "query"]},
                "room":      {"type": "string", "required": True},
                "event":     {"type": "object"},
                "filters":   {"type": "object", "help": "Nostr REQ filters"},
                "signer_ref":{"type": "string", "secret": True,
                              "help": "触手自己的 secp256k1 私钥 $secret:NAME"},
            },
            "outputs": {"events": {"type": "array"}, "event_id": {"type": "string"},
                        "cursor": {"type": "string"}},
            "idempotent": False, "risk": "high",   # 写入共享房间 → 闸门
        }
