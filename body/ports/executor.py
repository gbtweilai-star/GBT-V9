# body/ports/executor.py
from typing import Any, Protocol


class Executor(Protocol):
    async def focus(self, window_title: str) -> bool:
        """须在 UI 独占锁内聚焦并确认目标窗口；失败返回 False，不继续操作。"""
        raise NotImplementedError

    async def locate_uia(self, control: Any) -> tuple[tuple[int, int] | None, float]:
        """须在 UI 锁内定位并返回置信度；置信度不足时不得猜坐标。"""
        raise NotImplementedError

    async def locate_image(self, control: Any) -> tuple[tuple[int, int] | None, float]:
        """须在 UI 锁内做图像匹配；未达到置信度须报告未定位。"""
        raise NotImplementedError

    async def locate_ocr(self, control: Any) -> tuple[tuple[int, int] | None, float]:
        """须在 UI 锁内按 OCR 定位；未达到置信度须报告未定位。"""
        raise NotImplementedError

    async def click(self, x: int, y: int) -> None:
        """须在 UI 锁内点击已定位控件；动作后由调用方验证后置条件。"""
        raise NotImplementedError

    async def drag(self, x: int, y: int, dx: int, dy: int) -> None:
        """须在 UI 锁内执行 DPI/多屏感知拖动；不得使用未经标定的猜测坐标。"""
        raise NotImplementedError

    async def drag_playhead(self, x: int) -> None:
        """须在 UI 锁内移动播放头；调用方必须核验最终时间位置。"""
        raise NotImplementedError

    async def time_to_x(self, seconds: float) -> int:
        """须在 UI 锁内由可见标尺计算时间坐标；标尺不可读时显式失败。"""
        raise NotImplementedError

    async def screenshot(self, roi: Any) -> Any:
        """须在 UI 锁内返回指定 ROI 的 RGB 帧；ROI/DPI 无法确认时显式失败。"""
        raise NotImplementedError

    async def locate_roi(self, name: str) -> Any:
        """须在 UI 锁内定位 ROI 并带置信度；低置信度不得用于帧证据。"""
        raise NotImplementedError

    async def pause_playback(self) -> None:
        """须在 UI 锁内暂停预览；失败时不得将不稳定帧作为证据。"""
        raise NotImplementedError

    async def seek_to(self, seconds: float) -> None:
        """须在 UI 锁内定位并等待预览稳定；超时须显式失败。"""
        raise NotImplementedError

    async def is_foreground(self, window_title: str) -> bool:
        """须核验目标编辑器仍在前台；破坏性 UI 操作前必须检查。"""
        raise NotImplementedError

    async def playhead_seconds_near(self, seconds: float, tol: float) -> bool:
        """须读取真实播放头位置；不在容差内时返回 False。"""
        raise NotImplementedError

    async def is_selected(self, control: Any) -> bool:
        """须核验目标片段/控件已选中；未选中不得执行后续编辑。"""
        raise NotImplementedError

    async def clip_speed_is(self, rate: float) -> bool:
        """须核验编辑器当前片段速度；读取失败不得声称成功。"""
        raise NotImplementedError

    async def window_ok(self) -> bool:
        """须核验窗口与布局符合动作前置条件；不匹配则阻止动作。"""
        raise NotImplementedError

    @property
    def decoder_version(self) -> str:
        """返回可用于采集 fingerprint 的解码器版本；未知时显式失败。"""
        raise NotImplementedError

    @property
    def encoder_version(self) -> str:
        """返回可用于采集 fingerprint 的编码器版本；未知时显式失败。"""
        raise NotImplementedError

    async def preview_roi(self, project_id: str) -> dict[str, Any]:
        """返回预览 ROI 尺寸/缩放等 fingerprint 数据；须与 locate_roi 同权威来源。"""
        raise NotImplementedError
