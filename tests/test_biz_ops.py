# tests/test_biz_ops.py —— 接单→交付→变现：状态机 / 报价 / 交付证据 / 回款逾期 / 五大方向
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 说明：知识来源是那本书**封面写明的骨架**（接单·交付·变现 / 五大方向 / 一人公司），
# 本测试验证的是这套操作框架本身可用且不许糊（非法流转要拒、金额要算得出、逾期要认）。
import pytest

from core.biz_ops import (DIRECTIONS, DIRECTION_SCENES, OrderBook, Quote, BizError,
                          book_notes, plan_direction)
from audit.ledger import Ledger


@pytest.fixture
def book(tmp_path):
    led = Ledger(db=str(tmp_path / "biz.db"))
    yield OrderBook(ledger=led), led
    led.close()


# ═══ 报价：每一项可解释，毛利算得出 ═══
def test_quote_is_explainable_and_margin_computed():
    q = Quote(hours=10, rate=300, complexity=1.3, rush=1.3, discount=0.9, cost=200).compute()
    assert q["subtotal"] == pytest.approx(10 * 300 * 1.3 * 1.3)
    assert q["total"] == pytest.approx(q["subtotal"] * 0.9)
    assert q["margin"] == pytest.approx(q["total"] - 200)
    assert 0 < q["margin_rate"] < 1
    assert [l["item"] for l in q["lines"]] == ["基础工时", "复杂度", "加急", "折扣", "直接成本"]
    assert all("expr" in l for l in q["lines"])            # 客户能看懂每一项怎么来的


# ═══ 状态机：合法流转走通，非法直接拒 ═══
def test_happy_path_inquiry_to_paid(book):
    ob, _led = book
    o = ob.create(client="某电商", need="批量剪片", direction="兼职赚钱")
    assert o["state"] == "inquiry"
    ob.quote(o["order_id"], hours=8, rate=200)
    assert ob.get(o["order_id"])["state"] == "quoted"
    ob.transition(o["order_id"], "accepted")
    ob.deliver(o["order_id"], name="首批 10 条成片", content=b"video-bytes",
               acceptance="无黑边、字幕无错字")
    assert ob.get(o["order_id"])["state"] == "delivered"
    ob.verify(o["order_id"], by="客户", accepted=True)
    ob.invoice(o["order_id"])
    paid = ob.pay(o["order_id"])
    assert paid["fully_paid"] is True
    assert ob.get(o["order_id"])["state"] == "paid"


def test_illegal_transition_is_refused(book):
    ob, _led = book
    o = ob.create(client="A", need="B")
    with pytest.raises(BizError, match="非法流转"):
        ob.transition(o["order_id"], "paid")               # 询价 → 已回款：跳步，拒
    with pytest.raises(BizError, match="非法流转"):
        ob.transition(o["order_id"], "delivered")


def test_cannot_deliver_before_accepting(book):
    ob, _led = book
    o = ob.create(client="A", need="B")
    with pytest.raises(BizError, match="不能交付"):
        ob.deliver(o["order_id"], name="东西")


def test_rejected_delivery_goes_back_to_working(book):
    ob, _led = book
    o = ob.create(client="A", need="B")
    ob.quote(o["order_id"], hours=4)
    ob.transition(o["order_id"], "accepted")
    ob.deliver(o["order_id"], name="v1")
    ob.verify(o["order_id"], accepted=False, note="排版不对")
    assert ob.get(o["order_id"])["state"] == "working"      # 打回重做，而不是直接结账


def test_unknown_direction_and_bad_order_refused(book):
    ob, _led = book
    with pytest.raises(BizError, match="未知方向"):
        ob.create(client="A", need="B", direction="炒股")
    with pytest.raises(BizError, match="订单不存在"):
        ob.transition("nope", "quoted")
    bare = ob.create(client="先别开票", need="还没报价")
    with pytest.raises(BizError, match="金额必须"):
        ob.invoice(bare["order_id"])                       # 没报价 → 金额算不出来 → 拒


# ═══ 交付物带指纹（客户可核）═══
def test_deliverable_carries_fingerprint(book):
    ob, _led = book
    o = ob.create(client="A", need="B")
    ob.quote(o["order_id"], hours=4)
    ob.transition(o["order_id"], "accepted")
    d = ob.deliver(o["order_id"], name="成片", content=b"abc", acceptance="客户确认")
    assert len(d["sha256"]) == 64 and d["acceptance"] == "客户确认"


# ═══ 回款与逾期 ═══
def test_overdue_invoice_is_reported(book):
    ob, _led = book
    o = ob.create(client="欠款户", need="做站")
    ob.quote(o["order_id"], hours=20, rate=500)
    ob.transition(o["order_id"], "accepted")
    ob.invoice(o["order_id"], kind="prepay", due_at="2020-01-01T00:00:00Z")  # 早就到期
    st = ob.status()
    assert st["overdue_amount"] > 0
    assert st["overdue_orders"][0]["order_id"] == o["order_id"]
    assert st["invoiced"] > 0 and st["collected"] == 0


def test_partial_payment_then_full(book):
    ob, _led = book
    o = ob.create(client="A", need="B")
    ob.quote(o["order_id"], hours=10, rate=300)
    ob.transition(o["order_id"], "accepted")
    ob.deliver(o["order_id"], name="x")
    ob.verify(o["order_id"])
    ob.invoice(o["order_id"])
    half = ob.pay(o["order_id"], amount=1500)
    assert half["fully_paid"] is False and ob.get(o["order_id"])["state"] == "verified"
    rest = ob.pay(o["order_id"])
    assert rest["fully_paid"] is True and rest["state"] == "paid"


# ═══ 五大方向 playbook → 接单包 ═══
def test_direction_plan_makes_a_quotable_package():
    p = plan_direction("办公提效", client="某工厂行政", rush=1.3)
    assert p["direction"] == "办公提效" and p["client"] == "某工厂行政"
    assert len(p["milestones"]) == 3
    assert sum(m["billing"] for m in p["milestones"]) == pytest.approx(1.0)
    assert sum(m["amount"] for m in p["milestones"]) == pytest.approx(p["quote"]["total"], rel=1e-6)
    assert all(m["acceptance"] for m in p["milestones"])    # 每个里程碑都有验收标准
    assert all(s["cap"] for s in p["sop"])                  # SOP 全部映射到 V9 现有能力
    assert p["risk"] and p["price_band"]["min"] > 0


def test_all_five_directions_are_complete():
    assert set(DIRECTIONS) == {"办公提效", "兼职赚钱", "一人公司", "内容变现", "自动化服务"}
    for name, d in DIRECTIONS.items():
        assert d["clients"] and d["deliverables"] and d["sop"]
        assert d["hours"]["min"] <= d["hours"]["max"]
        assert d["price"]["min"] < d["price"]["max"]
        assert d["risk"]                                    # 风险必须写出来，不许含糊
    assert set(DIRECTION_SCENES) <= set(DIRECTIONS)


def test_book_notes_state_the_honest_source():
    n = book_notes()
    assert "封面" in n["source"] and "honest" in n          # 不冒充实读过正文
    assert any("一人公司" in t for t in n["themes"])
