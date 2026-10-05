# pulse.py —— 万能插脉冲 · dev: 自由的风 · 万物皆可插，万物皆可控
from pathlib import Path
import subprocess, requests

# 标准插座描述符：脉冲的唯一入参
SOCKETS = {
    "file":    lambda arg: {"scan": lambda: arg,       "act": lambda cmd: Path(arg).write_text(cmd)},
    "process": lambda arg: {"self": subprocess.Popen(arg, shell=True)},
    "http":    lambda arg: {"self": requests.get(arg, timeout=10)},
}

class Pulse:
    """把任意插座加速成触手分支：插上即拥有 eyes/hands，并自动注册到账本"""
    def __init__(self, brain, ledger):
        self.brain, self.ledger, self.plugged = brain, ledger, {}

    def plug(self, sock_type, arg, tentacle_id="t1"):
        if sock_type not in SOCKETS:
            # 不认识的插座 → 交主脑反思，让 LLM 生成新插座定义
            hint = self.brain.chat([
                {"role": "user", "content": f"未知插座类型={sock_type} 参数={arg}。"
                 "只回JSON:{\"verdict\":\"new_socket\",\"cmd\":\"plug|abort\",\"hint\":\"构造建议\"}"}])
            if hint.get("cmd") != "plug": return None
        branch = SOCKETS[sock_type](arg)
        self.ledger.log(tentacle_id, f"{sock_type}:{arg}", Status.SCANNED, "脉冲接入")
        self.plugged[f"{sock_type}:{arg}"] = (tentacle_id, branch)
        return branch

    def unplug_all(self):     # 安全拔出：清理所有分支进程
        for _, (tid, b) in self.plugged.items():
            if proc := b.get("self"):
                if proc.poll() is None: proc.terminate()
