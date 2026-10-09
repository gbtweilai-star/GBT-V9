# tests/test_loop_verifier.py —— 闭环验证器：每条闭环都要有状态+证据+阻塞口径
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖主人要求"全量生产推进直到全部跑通闭环全量上线"：
#   ① 8 条闭环齐全，状态只能是 已跑通/部分/阻塞/无法确认/未验
#   ② 已跑通的必须带**证据**（真数字/真产物），不能空口"跑通了"
#   ③ 阻塞的必须写清阻塞原因 + 解锁动作（不能只说"失败"）
#   ④ 媒体队列闭环要**真跑一次**入队→认领→完成
#   ⑤ 驱动闭环在缺余额时必须判"阻塞"，不许判"已跑通"
import pytest

from core import loop_verifier as LV


def test_all_loops_present_and_states_legal():
    v = LV.verify_all(deep=True)
    assert v["闭环总数"] == 8
    names = {x["闭环"] for x in v["闭环"]}
    assert any("绑定" in n for n in names) and any("驱动" in n for n in names)
    legal = ("已跑通", "部分", "阻塞", "无法确认", "未验")
    for x in v["闭环"]:
        assert any(str(x["状态"]).startswith(p) for p in legal), x
        assert v["已跑通"] + v["部分"] + v["阻塞"] <= v["闭环总数"]


def test_passing_loops_carry_evidence():
    v = LV.verify_all(deep=True)
    for x in v["闭环"]:
        if str(x["状态"]).startswith("已跑通"):
            assert x["证据"], f"{x['闭环']} 说跑通了却没证据"


def test_blocked_loop_declares_reason_and_unlock():
    v = LV.verify_all(deep=True)
    for x in v["闭环"]:
        if x["状态"] in ("阻塞", "部分"):
            assert x["阻塞"], f"{x['闭环']} 阻塞却没写原因"
    bl = v["阻塞清单"]
    assert all(b["原因"] for b in bl)
    # 驱动闭环：缺余额时必须是"阻塞"，且解锁动作非空
    drive = [x for x in v["闭环"] if "驱动" in x["闭环"]][0]
    if not drive["证据"].get("成功"):
        assert drive["状态"] == "阻塞" and drive["解锁"]


def test_bindings_and_compute_loops_read_real_numbers():
    b = LV.loop_bindings()
    assert b["状态"].startswith("已跑通"), b
    ev = b["证据"]
    assert ev["触手↔云插件"]["对数"] == 10000
    assert ev["触手↔库槽"]["对数"] == 10000
    # 期望值从唯一真源推导（不是写死的数字）：能力目录 × 触手数。
    # 病因（2026-10-07）：此处曾写死 33900（=339×100，即 7 项 v9tool 未绑的残缺态），
    # 补齐到 346 后断言当场失真。改成推导式，能力目录一变，断言自动跟着变。
    from core import octop_bridge as OB
    expect_octop = len(OB.capability_ids()) * len(OB.OctopBridge(ledger=None).tentacle_ids())
    assert ev["触手↔Octop能力"]["对数"] == expect_octop
    assert ev["触手↔Octop能力"]["期望"] == expect_octop
    c = LV.loop_compute()
    assert c["状态"].startswith("已跑通")
    assert c["证据"]["本地预留显存MB"] == 0
    assert c["证据"]["映射问题"] == 0


def test_media_queue_loop_really_completes_a_job():
    q = LV.loop_media_queue()
    assert q["状态"].startswith("已跑通"), q
    assert q["证据"].get("入队→认领→完成") == "成功"


def test_pages_and_solidify_loops_are_green():
    p = LV.loop_pages()
    assert p["状态"].startswith("已跑通") and p["证据"]["接口缺失"] == 0
    s = LV.loop_solidify()
    assert s["状态"].startswith("已跑通") and s["证据"]["坏哈希"] == []


def test_summary_is_lightweight_and_serializable():
    import json
    s = LV.summary()
    assert s["闭环总数"] == 8 and s["就绪度"] is not None
    assert json.dumps(s, ensure_ascii=False)
    # 轻量版不真跑驱动 → 驱动那条应是"未验"
    drive = [x for x in s["闭环"] if "驱动" in x["闭环"]][0]
    assert drive["状态"].startswith("未验")
