# core/governance/control.py —— governance.control@1 (Paperclip)
class GovernanceControl:
    def run(self, action, company_ref=None, task_ref=None,
            approval_ref=None, payload=None): ...
    def spec(self):
        return {
            "inputs": {
                "action": {"type": "enum", "required": True,
                           "values": ["sync_task", "get_status",
                                      "request_approval", "record_run"]},
                "company_ref":  {"type": "string"},
                "task_ref":     {"type": "string"},
                "approval_ref": {"type": "string"},
                "payload":      {"type": "object"},
            },
            "outputs": {"external_id": {"type": "string"}, "status": {"type": "string"},
                        "approval": {"type": "object"}},
            "idempotent": False, "risk": "high",   # 审批/治理写操作 → 闸门
        }
