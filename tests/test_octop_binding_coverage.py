"""Octop 能力位 × 触手双向绑定的覆盖度守门测试（防遗漏）。

主人 2026-10-08 要求："Octop 所有能力和页面都要一一对齐别遗漏了，每一项能力文件
每一页都要使用触手双向绑定登记好。" —— 这些断言就是那条要求的自动守卫：
  ① 页面必须算作能力位（page:<id>）并参与绑定；
  ② 每项能力、每一页都要有绑定，一个不缺；
  ③ 双向成对（t2c 与 c2t 行数相等）；
  ④ 对数 == 能力数 × 触手数（真源推导，不写死）。

只读真表，不写数据。
"""

import sqlite3
from pathlib import Path

import pytest

from core import octop_bridge as OB
from core import octop_fusion as OF

DB = Path(__file__).resolve().parent.parent / "data" / "gbt_v9.sqlite3"


def _rows():
    if not DB.is_file():
        pytest.skip("面板库不存在")
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        return con.execute(
            "SELECT tentacle, capability, direction FROM octop_binding").fetchall()
    finally:
        con.close()


def test_pages_are_first_class_capabilities():
    """Octop 的 64 个原生页都要作为能力位（否则永远绑不上——这次漏的就是这块）。"""
    pages = OB.page_ids()
    assert len(pages) == len(OF.PAGES) >= 60
    assert all(p.startswith("page:") for p in pages)
    assert set(pages).issubset(set(OB.capability_ids())), "页面必须并进能力位清单"


def test_no_capability_or_page_left_unbound():
    cov = OB.coverage()
    assert cov["齐不齐"] is True, f"有没登记的能力/页面：{cov['缺'][:10]}"
    assert cov["缺数量"] == 0
    assert cov["页面已绑"] == cov["页面应绑"]


def test_bindings_are_bidirectional_and_complete():
    rows = _rows()
    t2c = sum(1 for r in rows if r[2] == "t2c")
    c2t = sum(1 for r in rows if r[2] == "c2t")
    assert t2c == c2t, f"双向不对称：t2c={t2c} c2t={c2t}"
    caps = len(OB.capability_ids())
    tents = len(OB.OctopBridge(ledger=None).tentacle_ids())
    assert t2c == caps * tents, f"对数不足：{t2c} != {caps}×{tents}"


def test_no_duplicate_binding_rows():
    """同一 (触手, 能力, 方向) 只能有一行（先查后插的幂等性）。"""
    rows = _rows()
    assert len(rows) == len({(r[0], r[1], r[2]) for r in rows})


def test_every_page_bound_to_every_tentacle():
    rows = _rows()
    page_rows = [r for r in rows if r[0].startswith("page:") or r[1].startswith("page:")]
    tents = len(OB.OctopBridge(ledger=None).tentacle_ids())
    assert len(page_rows) == len(OB.page_ids()) * tents * 2, "页面绑定不成对/有缺"
