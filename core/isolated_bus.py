# core/isolated_bus.py —— 共享网关 · 隔离使用（key/配额/记忆三维隔离）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import os, time, threading
from openai import OpenAI

GATEWAY = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8317/v1")

class TentacleKeyRing:
    """密钥分发所：主脑签发，触手只拿自己的，谁也看不见谁的"""
    _keys, _quota = {}, {}
    _lock = threading.Lock()

    @classmethod
    def issue(cls, tentacle_id, rpm=20):
        with cls._lock:
            cls._keys[tentacle_id] = f"tk-{tentacle_id}-{os.urandom(6).hex()}"
            cls._quota[tentacle_id] = {"rpm": rpm, "window": [], "tokens": 0}
        return cls._keys[tentacle_id]

    @classmethod
    def key_of(cls, tentacle_id):
        with cls._lock:
            return cls._keys.get(tentacle_id)

    @classmethod
    def client_for(cls, tentacle_id):
        """每触手一个只带自己 key 的客户端：调用天然隔离、日志可区分"""
        with cls._lock:
            key = cls._keys.get(tentacle_id, "tk-unknown")
        return OpenAI(api_key=key, base_url=GATEWAY)

    @classmethod
    def allow(cls, tentacle_id):
        """配额闸门：本地 rpm 桶，超了返回 False（触手跳过 LLM 只落账）"""
        with cls._lock:
            q = cls._quota.setdefault(tentacle_id, {"rpm": 60, "window": [], "tokens": 0})
            now = time.time()
            q["window"] = [t for t in q["window"] if now - t < 60]
            if len(q["window"]) >= q["rpm"]:
                return False
            q["window"].append(now)
            return True

    @classmethod
    def usage(cls, tentacle_id):
        with cls._lock:
            q = cls._quota.get(tentacle_id, {"window": [], "rpm": 0})
            return {"rpm": q["rpm"], "used_last_min": len(q["window"])}

class MemoryStore:
    """触手记忆：命名空间硬隔离，只有主脑有跨空间读权限"""
    def __init__(self):
        self._mem, self._lock = {}, threading.Lock()

    def remember(self, tid, key, value):
        with self._lock:
            self._mem.setdefault(tid, {})[key] = {"value": value, "ts": time.time()}

    def recall(self, tid, key=None):
        with self._lock:
            ns = self._mem.get(tid, {})
            return ns if key is None else ns.get(key)

    def inspect_all(self):
        """⚠ 仅主脑可调：跨命名空间全量读"""
        with self._lock:
            return {tid: dict(ns) for tid, ns in self._mem.items()}
