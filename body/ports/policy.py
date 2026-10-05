"""策略与设置端口。
铁律：策略与设置缺失、非法或过期时必须显式处理，不得静默放宽门限。
"""
from typing import Any, Protocol

from .types import Row


class Policy(Protocol):
    def for_op(self, operation: str) -> Row:
        """返回指定操作的版本化策略；不存在或配置非法时须显式报错。"""
        raise NotImplementedError


class Settings(Protocol):
    def get(self, key: str, default: Any = None) -> Any:
        """读取设置值；读取错误须显式报错，不能将错误当作默认值。"""
        raise NotImplementedError
