# mesh.py —— D1..D100 双向绑定互通，D1 瞬间到达任意节点
import asyncio
from dataclasses import dataclass, field

@dataclass
class Node:
    id: str                      # "D1" .. "D100"
    handler: object              # 该节点的能力（存储/推理/工具）
    peers: set = field(default_factory=set)

class Mesh:
    """效果 = 全互联：注册进总线即与所有节点双向可达，无需 4950 条手工链路"""
    def __init__(self):
        self.nodes = {}

    def register(self, node: Node):
        for other in self.nodes.values():      # 双向绑定
            other.peers.add(node.id)
            node.peers.add(other.id)
        self.nodes[node.id] = node

    async def send(self, src, dst, payload):   # D1 → D100 直达
        if dst not in self.nodes[src].peers:
            raise PermissionError(f"{src} 与 {dst} 未绑定")
        return await self.nodes[dst].handler(payload)

    async def broadcast(self, src, payload):   # 一次调用全网互通
        return await asyncio.gather(*[
            n.handler(payload) for k, n in self.nodes.items() if k != src])

# 部署：一个 R2 账号，100 个 bucket 各挂一个节点 —— 合规且效果等同
for i in range(1, 101):
    Mesh().register(Node(f"D{i}", make_bucket_handler(f"tentacle-d{i}")))
