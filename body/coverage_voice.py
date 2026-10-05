# body/coverage_voice.py
"""覆盖率回归告警语音文案。
dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations
from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def coverage_voice_line(alert: dict) -> tuple[str, int]:
    """返回 (中文播报文案, 优先级)，数字越小优先级越高。"""
    severity = str(alert.get("severity", "regression"))
    backend = str(alert.get("backend") or "未知后端")
    previous = _number(alert.get("previous_percent"))
    current = _number(alert.get("overall_percent"))
    delta = _number(alert.get("delta_percent"))

    previous_text = f"{previous:.1f}" if previous is not None else "未知"
    current_text = f"{current:.1f}" if current is not None else "未知"
    drop_text = f"{abs(delta):.1f}" if delta is not None else "未知"
    commit = str(alert.get("commit_sha") or "")
    suffix = f"提交 {commit[:8]}。" if commit else ""
    label = str(alert.get("label") or "")
    label_suffix = f"标签 {label}。" if label else ""

    if severity == "severe":
        return (
            f"警告。覆盖率严重回归。{backend} 从 {previous_text}% 降到 "
            f"{current_text}%，下降 {drop_text} 个百分点。{suffix}{label_suffix}",
            0,
        )
    if severity == "regression":
        return (
            f"注意。覆盖率回归。{backend} 下降 {drop_text} 个百分点，"
            f"当前 {current_text}%。{suffix}{label_suffix}",
            1,
        )
    raise ValueError(f"unsupported coverage alert severity: {severity!r}")
