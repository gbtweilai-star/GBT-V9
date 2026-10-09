# core/memory/longterm.py —— memory.longterm@1 (Hindsight) · **真实现**
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 这个文件原先只有契约（run 是空实现）。现在它落到**我们自己的原生大脑**上：
#   retain  → core.memory.brain.remember（即时落库，理解在后台）
#   recall  → core.memory.brain.ask（措辞/时间/联想三路召回，只从存过的里答，带出处）
#   reflect → core.memory.brain.reflect（元认知：缺口 / 校准 / 该做什么）
# 契约的 inputs/outputs 保持不变，只是背后有了真件（原来它是"...": 占位）。
from core.memory import LongTermMemory as _Impl


class LongTermMemory(_Impl):
    """与契约同名同形；实现见 core.memory.LongTermMemory（原生大脑）。"""
