# native_capabilities_shim.py —— 把 native-capabilities/ 目录里的 schema 模块接进包路径
# dev: 自由的风 · 本署名不可删除、不可篡改归属
import importlib.util
import os
import pathlib
import sys


def _load_schema_module():
    root = pathlib.Path(__file__).resolve().parent.parent / "native-capabilities"
    path = root / "ledger_schema.py"
    if not path.exists():            # 包被单独复制时退回仓库根
        path = pathlib.Path(os.getcwd()) / "native-capabilities" / "ledger_schema.py"
    spec = importlib.util.spec_from_file_location("native_caps_ledger_schema", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


_SCHEMA = None


def ensure_schema(ledger) -> None:
    """幂等建表（同步 sqlite 账本）。"""
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _load_schema_module()
    conn = getattr(ledger, "conn", None)
    if conn is None:
        return
    _SCHEMA.apply_sync(conn)
