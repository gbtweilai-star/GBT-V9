# skills/office/bridge.py —— Python 主脑 ↔ Node worker（对话片段补全为可导入模块）
from __future__ import annotations

import json
import subprocess
from pathlib import Path


def validate_worker_response(result):
    """worker 输出必须过校验；默认只做基本形状检查（可按现场替换）。"""
    if not isinstance(result, dict) or "ok" not in result:
        raise ValueError(f"office worker response invalid: {type(result).__name__}")
    return result


def run_worker(request, *, worker_dir: str | Path, timeout_s: int = 60) -> dict:
    """调用 Node worker：输入 JSON，输出必须过校验（shell=False）。"""
    proc = subprocess.run(
        ["node", "office_worker.mjs"],
        input=json.dumps(request), text=True, capture_output=True,
        timeout=timeout_s, cwd=worker_dir, shell=False,
    )
    result = json.loads(proc.stdout)
    validate_worker_response(result)
    return result
