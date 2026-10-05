# common/timeutil.py —— 时间归一化唯一真相
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 写入与比较必须用同一种格式; naive 一律按 UTC 解释;
#       SQLite 存 canon_iso(带 Z), PG 存 tz-aware datetime

from datetime import datetime, timezone


def as_utc(value) -> datetime:
    if isinstance(value, (int, float)):
        value = datetime.fromtimestamp(float(value), timezone.utc)
    elif isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"不支持的时间类型: {type(value).__name__}")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)      # naive → UTC
    return value.astimezone(timezone.utc)


def canon_iso(value) -> str:
    """SQLite 存储/比较用的规范格式，永远带 Z 与微秒。"""
    return as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def to_epoch(value) -> float:
    return as_utc(value).timestamp()


def timestamp_param(dialect: str, value):
    dt = as_utc(value)
    return canon_iso(dt) if dialect == "sqlite" else dt
