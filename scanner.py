# scanner.py —— 从工作树顶层遍历，每个文件夹/每页都过钩子
import os, hashlib
from pathlib import Path

SKIP_NAMES = {".git", "node_modules", "__pycache__", ".venv", "dist", "build"}

def enumerate_targets(root: str) -> set:
    """权威目标清单：先建全集，扫完后用 coverage() 对账，少一个都不算达标"""
    targets = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_NAMES]  # 只跳过噪音，不跳过业务目录
        for name in dirnames + filenames:
            targets.add(os.path.join(dirpath, name))
    return targets
