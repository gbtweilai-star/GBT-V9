"""吞噬能帧证据端口。
铁律：用于判定的帧、指标与热图必须同源；证据不足不得判为 verified。
"""
from typing import Protocol

from .leases import LeaseSession
from .types import Ref, Row


class Devour(Protocol):
    """帧采集、保全、比较、判定与校准接口。"""

    async def snapshot_at(self, project_id: str, time_s: float) -> bytes:
        """采集指定项目时间点的原始帧字节；不可缓存或缺帧时须显式报错。"""
        raise NotImplementedError

    async def pin_snapshot(self, ref: Ref, *, holder: str, purpose: str,
                           ttl_s: int | None = None) -> LeaseSession:
        """为已存帧创建 lease session；lease 获取必须与 eviction 同行串行化。"""
        raise NotImplementedError

    async def color_metrics_delta(self, baseline_ref: Ref, result_ref: Ref, *,
                                  roi: tuple[int, int, int, int] | None = None) -> Row:
        """计算基线帧与结果帧的颜色指标差；取帧与算法失败必须显式报错。"""
        raise NotImplementedError

    async def effect_metrics(self, baseline_ref: Ref, result_ref: Ref, *,
                             roi: tuple[int, int, int, int] | None = None) -> Row:
        """计算效果验证指标；必须与对应热图使用同一指标内核和阈值口径。"""
        raise NotImplementedError

    async def frame_diff_refs(self, baseline_ref: Ref, result_ref: Ref) -> Ref:
        """生成并持久化帧差/热图工件，返回内容寻址 ref；失败不得返回空 ref。"""
        raise NotImplementedError

    async def verdict_and_spec(self, metrics: Row, spec: Row,
                               calibration: Row | None = None) -> Row:
        """按明确 spec 与有效 calibration 判定；证据不足须返回 unknown，而非 verified。"""
        raise NotImplementedError

    async def evidence_row(self, *, trace_id: str, project_id: str, operation: str,
                           baseline_ref: Ref, result_ref: Ref, metrics: Row,
                           verdict: Row, spec: Row) -> Row:
        """写入或构造可审计证据行；必须保留两个原始 ref 与判定依据。"""
        raise NotImplementedError

    async def verification_times(self, start_s: float, end_s: float, *,
                                 interval_s: float) -> list[float]:
        """返回验证采样时间点；范围非法或间隔无效须显式报错。"""
        raise NotImplementedError

    async def calibrate_inline(self, project_id: str, operation: str,
                               samples: list[Row], *, ttl_s: int) -> Row:
        """按项目与操作持久化标定结果及有效期；样本不足或写入失败须报错。"""
        raise NotImplementedError
