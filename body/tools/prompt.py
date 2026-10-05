# body/tools/prompt.py —— 数字人的工具清单与系统约束（意图路由 → 工具）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from body.tools.base import TOOLS
from body.tools import domains            # noqa: F401  ← 导入即注册（清单必须完整）


def tool_specs() -> list[dict]:
    """给 LLM 的函数清单。只列已注册的工具，绝不出现"清单里有、实现里没有"。"""
    return [t.spec() for t in TOOLS.values()]


def tool_names() -> list[str]:
    return sorted(TOOLS)


SYSTEM_RULE = """涉及任何状态或数量时，你必须调用对应的只读工具，并如实转述返回的
safe_sentence 与 facts。硬性约束：
1) 禁止自行计算、推算、补猜任何数字；
2) stale=true 或 unknown_reason 非空时，必须说明"无法确认"，绝不能把旧读数说成当前值；
3) 参数缺失先澄清，不要瞎猜参数；
4) 不能编造工具没返回的字段（例如没返回帧率就不要说帧率）；
5) 见证相关的问题（有效票/在岗/证据等级/掉票）必须走 get_witness_snapshot —— 它读的是
   见证快照单行，与你看到的卡片是同一份数据；不许另开探测、不许拿配置声明当证据。
意图路由：采集/录像/帧段/丢帧/断点/归档/缓存 → media.capture；
扫描/覆盖/漏扫/漏洞/触手覆盖/责任页 → scan.coverage；
队列/等待/失败率/死信/显存/排队 → media.queue；
见证/有效票/在岗/掉票/证据等级 → get_witness_snapshot。"""
