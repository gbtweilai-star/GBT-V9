"""共享端口类型。
铁律：状态值是受限词汇；不得用未知状态掩盖错误。
"""
from typing import Any, Literal, TypeAlias

Row: TypeAlias = dict[str, Any]
Params: TypeAlias = tuple[Any, ...]
Ref: TypeAlias = str
Epoch: TypeAlias = int
LeaseId: TypeAlias = str

ArtifactState: TypeAlias = Literal["staged", "ready", "deleting", "deleted"]
LeaseStatus: TypeAlias = Literal["active", "released", "expired"]
