# integrations/ai_memory/client.py —— ai-memory MCP 客户端（跨 Agent 长期记忆）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律：不直写它的 SQLite / Wiki 文件；读写一律走 MCP。
#       只存“决策 / 摘要 / 未完成任务交接”，不存原始录音、密钥、事实账本。
import os, json, httpx

MCP_URL = os.environ.get("AI_MEMORY_MEMORY_URL", "http://127.0.0.1:49374/mcp")
TOKEN = os.environ.get("AI_MEMORY_TOKEN", "")


class AiMemory:
    def __init__(self, project=None, workspace=None):
        self.project, self.workspace = project, workspace
        self.h = {"Content-Type": "application/json"}
        if TOKEN: self.h["Authorization"] = f"Bearer {TOKEN}"

    def _call(self, tool, args):
        # 静态 MCP 客户端要显式给 project/workspace，别依赖服务器猜“最近活动项目”
        args.setdefault("project", self.project)
        args.setdefault("workspace", self.workspace)
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": tool, "arguments": args}}
        r = httpx.post(MCP_URL, headers=self.h, json=body, timeout=30)
        r.raise_for_status()
        return r.json().get("result")

    def query(self, q, limit=10):      return self._call("memory_query", {"query": q, "limit": limit})
    def read(self, path=None, q=None): return self._call("memory_read_page", {"path": path, "query": q})
    def write(self, path, body, pinned=False):
        return self._call("memory_write_page", {"path": path, "body": body, "pinned": pinned})
    def handoff_begin(self, summary, next_steps=None):
        return self._call("memory_handoff_begin", {"summary": summary, "next_steps": next_steps or []})
    def message_send(self, to, body):  return self._call("memory_message_send", {"to": to, "body": body})
    def status(self):
        try: return self._call("memory_status", {})
        except Exception as e: return {"ok": False, "error": str(e)}
