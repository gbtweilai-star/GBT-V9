# body/witness_tool.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import json, os, time

from body.tools.base import _age_text as _humanize      # 措辞口径与各域统一


def _ts(value) -> float:
    """快照时间戳 → epoch。时间口径统一走 common.timeutil（naive 按 UTC）。"""
    from common.timeutil import to_epoch
    try:
        return float(to_epoch(value))
    except Exception:
        return 0.0

WITNESS_TOOL_SPEC = {
    "name": "get_witness_snapshot",
    "description": ("读取当前见证快照。回答见证/有效票/在岗见证数量时必须调用本工具，"
                    "只能转述返回的 safe_sentence 与字段；禁止自行推算、补猜或触发新的探测。"),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False,
                   "required": []},
}

# 强约束：写进数字人的 system prompt
SYSTEM_RULE = ("涉及见证数量/有效票/见证状态时，你必须调用 get_witness_snapshot，"
               "并如实转述 safe_sentence。若 stale=true，必须说明状态无法确认，"
               "不得把快照里的旧计数当作当前值，也不得自行推断。")


async def get_witness_snapshot(ledger, *, now_fn=time.time, stale_after=None) -> dict:
    """只读：同一份 witness_snapshot，不重算、不探测。"""
    from body.witness_voice import render_witness_sentence
    snap = await ledger.fetch_one("SELECT * FROM witness_snapshot WHERE id=1")
    if snap is None:
        return {"revision": None, "observed_at": None, "stale": True,
                "valid_count": None, "required": int(os.getenv("BODY_WITNESS_REQUIRED_EXTERNAL", 2)),
                "status": "unknown",
                "safe_sentence": "我现在没有见证快照，无法回答当前有效见证数。"}
    age = now_fn() - _ts(snap["observed_at"])
    limit = stale_after or int(os.getenv("BODY_WITNESS_PROBE_TTL", 60)) * 2
    stale = age > limit
    age_text = _humanize(age)
    return {"revision": snap["revision"], "observed_at": snap["observed_at"],
            "stale": stale, "valid_count": snap["valid_count"],
            "required": snap["required"], "status": snap["status"],
            "age_seconds": int(age),
            "safe_sentence": render_witness_sentence(
                {"valid_count": snap["valid_count"], "required": snap["required"]},
                stale=stale, age_text=age_text),
            "states": json.loads(snap["states_json"])}
