# workflows/store.py —— 工作流定义的持久化（乐观锁版本控制）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 真机病因（2026-10-08 逐行复核）：本文件原先装的是**一段面板路由代码**（第 4 行还写着
#   from workflows.store import list_flows, get_flow, save_flow, VersionConflict —— 从自己 import 自己），
#   于是 ① 这些函数全仓根本不存在 ② DAG 引擎没有定义可存可取 ③ 引擎也没有任何 HTTP 入口。
#   本文件按它自己声明的契约把那四个名字**真实现出来**：flow 落账本库，改并发用版本号挡。
# 纪律：所有外部输入参数绑定；方言差异只走 audit/ddl 的常量片段；冲突显式抛 VersionConflict，不静默覆盖。
from __future__ import annotations

import json
import time

from audit.ddl import run_script
from senses.sqldialect import txn

TABLE = "workflow_flows"


class VersionConflict(Exception):
    """乐观锁：拿旧版本号写新内容 —— 挡的是“两个人同时覆盖一份 flow”。"""


def _ddl(dialect: str) -> str:
    ts = "REAL" if dialect == "sqlite" else "DOUBLE PRECISION"
    return (f"CREATE TABLE IF NOT EXISTS {TABLE}("
            "flow_id TEXT PRIMARY KEY, name TEXT, definition TEXT,"
            f"version INTEGER, updated_at {ts})")


def _ensure(led) -> str:
    d = getattr(led, "dialect", "sqlite")
    with txn(led) as cur:
        run_script(cur, _ddl(d), d)
    return d


def _row_to_flow(r) -> dict:
    return {"flow_id": r[0], "name": r[1],
            "definition": json.loads(r[2] or "{}"),
            "version": int(r[3] or 0), "updated_at": r[4]}


def list_flows(led) -> list:
    """全部 flow 的目录（不带 definition，省流量）。"""
    if led is None:
        return []
    _ensure(led)
    with txn(led) as cur:
        cur.execute(f"SELECT flow_id, name, version, updated_at FROM {TABLE} "
                    "ORDER BY updated_at DESC")
        # id 与 flow_id 同值都给：legacy_spec/test_workflow_editor.py:66 按 id 找，
        # 面板路由按 flow_id 找 —— 两处契约都不能破。
        return [{"id": r[0], "flow_id": r[0], "name": r[1],
                 "version": int(r[2] or 0), "updated_at": r[3]}
                for r in cur.fetchall()]


def get_flow(led, fid: str):
    if led is None:
        return None
    d = _ensure(led)
    ph = "?" if d == "sqlite" else "%s"
    with txn(led) as cur:
        cur.execute(f"SELECT flow_id, name, definition, version, updated_at "
                    f"FROM {TABLE} WHERE flow_id={ph}", (fid,))
        r = cur.fetchone()
    return _row_to_flow(r) if r else None


def save_flow(led, fid: str, name: str, definition: dict, version=None, *,
              expect_version=None) -> dict:
    """存一份 flow。已存在且传入的期望版本与当前不符 → VersionConflict（不覆盖别人的改动）。

    version / expect_version 两个名字都收：tests/legacy_spec/test_workflow_editor.py:60 用的是
    expect_version=（编辑器侧的原始契约），面板路由传的是 version=。同义，取先给的那个。
    """
    want = expect_version if expect_version is not None else version
    if led is None:
        raise RuntimeError("save_flow 需要账本")
    d = _ensure(led)
    ph = "?" if d == "sqlite" else "%s"
    now = time.time()
    body = json.dumps(definition or {}, ensure_ascii=False)
    with txn(led) as cur:
        cur.execute(f"SELECT version FROM {TABLE} WHERE flow_id={ph}", (fid,))
        row = cur.fetchone()
        if row is None:
            cur.execute(f"INSERT INTO {TABLE}(flow_id,name,definition,version,updated_at) "
                        f"VALUES({','.join([ph] * 5)})", (fid, name, body, 1, now))
            return {"flow_id": fid, "name": name, "version": 1, "created": True}
        cur_v = int(row[0] or 0)
        if want is not None and int(want) != cur_v:
            raise VersionConflict(
                f"flow {fid} 版本冲突：服务端 {cur_v} / 你手里 {int(want)}")
        nv = cur_v + 1
        cur.execute(f"UPDATE {TABLE} SET name={ph}, definition={ph}, version={ph}, "
                    f"updated_at={ph} WHERE flow_id={ph}", (name, body, nv, now, fid))
        return {"flow_id": fid, "name": name, "version": nv, "created": False}


def delete_flow(led, fid: str) -> bool:
    if led is None:
        return False
    d = _ensure(led)
    ph = "?" if d == "sqlite" else "%s"
    with txn(led) as cur:
        cur.execute(f"DELETE FROM {TABLE} WHERE flow_id={ph}", (fid,))
    return True


__all__ = ["VersionConflict", "list_flows", "get_flow", "save_flow", "delete_flow", "TABLE"]
