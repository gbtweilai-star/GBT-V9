# core/biz_ops.py —— 接单 → 交付 → 变现 闭环（蒸馏自《WorkBuddy AI赚钱》封面骨架）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# ⚠ 诚实标注（这条不能含糊）：本模块的知识来源是那本书**封面写明的骨架** ——
#   「用 AI 智能体接单、交付与变现」「五大实战方向全覆盖」「不止是工具，更是你的同事」
#   「从职场发展到一人公司」。我没有该书正文；因此这里实现的是**可执行的操作框架**
#   （状态机 / 报价 / 交付证据 / 回款与逾期 / 五大方向 playbook），而不是对书中章节内容的转述。
#   拿到 PDF/EPUB 后可再逐章校对补全（届时只改 DIRECTIONS 与 BOOK_NOTES）。
#
# 与 V9 已有的能力咬合：报价用真实工时估算，交付物挂 V9 的证据链（sha256 + artifact），
# 每个方向的 SOP 直接引用现有能力 id（exec.gui_agent / media.capture / scan.coverage /
# mail 编队 / voice 播报 …），所以"接单包"一下来就知道由哪根触手怎么干。
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict

from senses.sqldialect import txn
from core.swallow import swallow as _swallow

# 状态机：询价 → 报价 → 已接单 → 进行中 → 已交付 → 已验收 → 已回款；另有 流单
STATES = ("inquiry", "quoted", "accepted", "working", "delivered", "verified", "paid")
TERMINAL = ("paid", "lost")
TRANSITIONS: dict[str, tuple] = {
    "inquiry": ("quoted", "lost"),
    "quoted": ("accepted", "lost", "inquiry"),
    "accepted": ("working", "lost"),
    "working": ("delivered", "accepted"),
    "delivered": ("verified", "working"),
    "verified": ("paid",),
    "paid": (),
    "lost": (),
}
STATE_LABEL = {"inquiry": "询价", "quoted": "已报价", "accepted": "已接单",
               "working": "进行中", "delivered": "已交付", "verified": "已验收",
               "paid": "已回款", "lost": "流单"}

# 书里点名的三个场景（封面）：办公提效 / 兼职赚钱 / 一人公司
DIRECTION_SCENES = ("办公提效", "兼职赚钱", "一人公司")

# 五大实战方向 playbook：目标客户 / 交付物 / SOP（映射到 V9 现有能力）/ 工时 / 定价区间
DIRECTIONS: dict[str, dict] = {
    "办公提效": {
        "clients": ["中小企业行政/人事", "个体老板", "部门主管"],
        "deliverables": ["批量报表自动化脚本", "表格清洗与合并", "周报/月报自动生成器",
                         "会议纪要与待办归档"],
        "sop": [{"step": "看现状", "cap": "media.capture", "note": "吞噬能录屏取帧，摸清手工流程"},
                {"step": "定流程", "cap": "exec.gui_agent", "note": "纯视觉复现人工操作，不依赖任何 API"},
                {"step": "打包交付", "cap": "scan.coverage", "note": "覆盖清单即交付清单，页页有据"},
                {"step": "教会客户", "cap": "voice.tts", "note": "语音操作说明，客户照着念就会"}],
        "hours": {"min": 6, "max": 20}, "price": {"min": 800, "max": 6000},
        "risk": "低（只读数据 + 本地脚本，不碰客户生产系统）",
    },
    "兼职赚钱": {
        "clients": ["自媒体/博主", "小电商", "留学生/自由职业者"],
        "deliverables": ["短视频批量剪辑与字幕", "商品图文与详情页", "数据采集与整理",
                         "多平台内容分发"],
        "sop": [{"step": "收需求", "cap": "mail.dispatch", "note": "每根触手一个专属永久邮箱收单"},
                {"step": "量产", "cap": "media.queue", "note": "队列调度 + 显存预算，批量不打架"},
                {"step": "质检", "cap": "scan.cross_review", "note": "交叉互扫：另一根触手复核"},
                {"step": "交付验收", "cap": "biz.deliver", "note": "交付物带指纹，客户可核"}],
        "hours": {"min": 2, "max": 10}, "price": {"min": 200, "max": 3000},
        "risk": "中（涉及平台规则，注明「内容合规由客户确认」）",
    },
    "一人公司": {
        "clients": ["想开副业的个人", "小微品牌"],
        "deliverables": ["品牌视觉与落地页", "知识付费产品（课程/手册）", "自动化客服与跟进",
                         "月度运营报告"],
        "sop": [{"step": "定位与报价", "cap": "biz.quote", "note": "可解释报价：工时×单价×系数"},
                {"step": "搭建交付", "cap": "exec.coder", "note": "Codex 工具做真实代码交付"},
                {"step": "常态运营", "cap": "voice.report", "note": "每晚语音播报应收/进度/逾期"},
                {"step": "复购", "cap": "biz.retention", "note": "按回款与验收记录做复购提醒"}],
        "hours": {"min": 20, "max": 80}, "price": {"min": 3000, "max": 30000},
        "risk": "中高（周期长，必须分期收款 + 里程碑验收）",
    },
    "内容变现": {
        "clients": ["知识博主", "培训机构"],
        "deliverables": ["课程视频成片", "图文/PPT 讲义", "口播稿与配音", "分发矩阵与复盘"],
        "sop": [{"step": "选题库", "cap": "brain.intent_split", "note": "意图拆分选题，批量排产"},
                {"step": "生产", "cap": "video.edit", "note": "剪辑/字幕/配音一条线"},
                {"step": "发布", "cap": "web.scrape", "note": "抓平台反馈数据"},
                {"step": "复盘", "cap": "voice.report", "note": "情绪化播报复盘要点"}],
        "hours": {"min": 8, "max": 40}, "price": {"min": 1500, "max": 20000},
        "risk": "中（版权与素材授权需客户确认）",
    },
    "自动化服务": {
        "clients": ["有重复劳动的业务方", "IT 部门"],
        "deliverables": ["跨系统流程机器人", "定时任务与告警", "数据对账与差异追溯", "私有化部署"],
        "sop": [{"step": "审计现状", "cap": "scan.coverage", "note": "先出覆盖清单，漏扫即漏活"},
                {"step": "搭机器人", "cap": "exec.actuator", "note": "人类操作模式，任何 APP 都能驱动"},
                {"step": "接告警", "cap": "panel.alerts", "note": "失败即红卡 + 语音告警"},
                {"step": "出账", "cap": "biz.invoice", "note": "按里程碑开账单、追回款"}],
        "hours": {"min": 12, "max": 60}, "price": {"min": 2000, "max": 40000},
        "risk": "高（进生产系统，必须沙箱 + 回滚预案 + 分期付款）",
    },
}

BOOK_NOTES = {
    "source": "《WorkBuddy AI赚钱：用AI智能体接单、交付与变现》封面骨架",
    "themes": ["用 AI 智能体接单、交付与变现", "五大实战方向全覆盖", "不止是工具，更是你的同事",
               "从职场发展到一人公司"],
    "honest": "仅据封面骨架实现可执行框架；正文未获取，章节级提炼待原书（PDF/EPUB）到位后补。",
}


# ══════════ 报价：可解释（每一项都算得出来，不拍脑袋）══════════
@dataclass
class Quote:
    hours: float
    rate: float = 300.0
    complexity: float = 1.0          # 0.8 简单 / 1.0 常规 / 1.3 复杂 / 1.6 很复杂
    rush: float = 1.0                # 1.0 常规 / 1.3 加急 / 1.6 特急
    discount: float = 1.0            # 1.0 不打折 / 0.9 老客户 …
    cost: float = 0.0                # 直接成本（算力/素材/外包）
    lines: list = field(default_factory=list)

    def compute(self) -> dict:
        base = float(self.hours) * float(self.rate)
        adj = base * float(self.complexity) * float(self.rush)
        final = adj * float(self.discount)
        margin = final - float(self.cost)
        self.lines = [
            {"item": "基础工时", "expr": f"{self.hours:g}h × {self.rate:g}/h", "amount": round(base, 2)},
            {"item": "复杂度", "expr": f"×{self.complexity:g}", "amount": round(adj - base, 2)},
            {"item": "加急", "expr": f"×{self.rush:g}", "amount": round(adj * (self.rush - 1) / max(self.rush, 1e-9), 2)},
            {"item": "折扣", "expr": f"×{self.discount:g}", "amount": round(final - adj, 2)},
            {"item": "直接成本", "expr": "-", "amount": -round(float(self.cost), 2)},
        ]
        return {"subtotal": round(adj, 2), "total": round(final, 2),
                "cost": round(float(self.cost), 2), "margin": round(margin, 2),
                "margin_rate": round(margin / final, 4) if final else None,
                "lines": self.lines, "currency": os.environ.get("BIZ_CURRENCY", "CNY")}


# ══════════ 订单本：状态机 + 交付证据 + 账单回款 ══════════
class BizError(RuntimeError):
    pass


@dataclass
class Order:
    order_id: str
    client: str
    need: str
    state: str = "inquiry"
    direction: str = ""
    quote: dict = field(default_factory=dict)
    due_at: str | None = None
    created_at: str = ""
    updated_at: str = ""


class OrderBook:
    """接单本。所有流转都必须合法（非法流转直接拒），每步落 biz_audit。"""

    def __init__(self, ledger=None, *, now_fn=time.time):
        self.led = ledger
        self._now = now_fn
        self._init_tables()

    # ── 表（字面 SQL，值全绑定）──
    def _init_tables(self):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS biz_order ("
                            "order_id TEXT PRIMARY KEY, client TEXT, need TEXT,"
                            " direction TEXT, state TEXT, quote_json TEXT,"
                            " due_at TEXT, created_at TEXT, updated_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS biz_deliverable ("
                            "id TEXT PRIMARY KEY, order_id TEXT, name TEXT,"
                            " artifact_ref TEXT, sha256 TEXT, acceptance TEXT,"
                            " state TEXT, at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS biz_invoice ("
                            "invoice_id TEXT PRIMARY KEY, order_id TEXT, amount REAL,"
                            " kind TEXT, due_at TEXT, paid_at TEXT, amount_paid REAL,"
                            " created_at TEXT)")
                cur.execute("CREATE TABLE IF NOT EXISTS biz_audit ("
                            "audit_id TEXT, order_id TEXT, action TEXT, detail TEXT, at TEXT)")
        except Exception as e:
            _swallow(__file__, e)


    def _audit(self, order_id, action, detail=""):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO biz_audit (audit_id, order_id, action, detail, at)"
                            " VALUES (?,?,?,?,?)",
                            (uuid.uuid4().hex[:12], order_id, action,
                             json.dumps(detail, ensure_ascii=False)[:800], self._iso()))
        except Exception as e:
            _swallow(__file__, e)


    def _iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self._now()))

    # ── 建单（询价）──
    def create(self, *, client: str, need: str, direction: str = "",
               due_at: str | None = None, order_id: str | None = None) -> dict:
        if not client or not need:
            raise BizError("client 与 need 必填（客户与需求都要说清楚）")
        if direction and direction not in DIRECTIONS:
            raise BizError(f"未知方向 {direction}；可选：{list(DIRECTIONS)}")
        oid = order_id or ("o" + uuid.uuid4().hex[:10])
        order = Order(order_id=oid, client=client, need=need, direction=direction,
                      due_at=due_at, created_at=self._iso(), updated_at=self._iso())
        if self.led is not None:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO biz_order (order_id, client, need, direction,"
                            " state, quote_json, due_at, created_at, updated_at)"
                            " VALUES (?,?,?,?,?,?,?,?,?)",
                            (oid, client, need, direction, order.state, "{}",
                             due_at, order.created_at, order.updated_at))
        self._audit(oid, "create", {"client": client, "direction": direction})
        return self.get(oid) or asdict(order)

    def get(self, order_id: str) -> dict | None:
        if self.led is None:
            return None
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT * FROM biz_order WHERE order_id=?", (order_id,))
                row = cur.fetchone()
                cols = [d[0] for d in (cur.description or [])]
                return dict(zip(cols, row)) if row else None
        except Exception:
            return None

    # ── 报价（写入订单，状态 inquiry → quoted）──
    def quote(self, order_id: str, *, hours: float, rate: float = 300.0,
              complexity: float = 1.0, rush: float = 1.0, discount: float = 1.0,
              cost: float = 0.0) -> dict:
        q = Quote(hours=hours, rate=rate, complexity=complexity, rush=rush,
                  discount=discount, cost=cost).compute()
        self.transition(order_id, "quoted",
                        detail={"quote": q, "why": "报价明细见 lines（每一项可解释）"})
        if self.led is not None:
            with txn(self.led) as cur:
                cur.execute("UPDATE biz_order SET quote_json=? WHERE order_id=?",
                            (json.dumps(q, ensure_ascii=False), order_id))
        return q

    # ── 状态流转：非法即拒（fail closed）──
    def transition(self, order_id: str, to_state: str, *, detail=None) -> dict:
        cur_order = self.get(order_id)
        if cur_order is None:
            raise BizError(f"订单不存在：{order_id}")
        frm = cur_order["state"]
        if to_state not in TRANSITIONS.get(frm, ()):
            raise BizError(f"非法流转：{STATE_LABEL.get(frm, frm)} → "
                           f"{STATE_LABEL.get(to_state, to_state)}"
                           f"（允许：{', '.join(STATE_LABEL.get(s, s) for s in TRANSITIONS.get(frm, ()))}）")
        if self.led is not None:
            with txn(self.led) as cur:
                cur.execute("UPDATE biz_order SET state=?, updated_at=? WHERE order_id=?",
                            (to_state, self._iso(), order_id))
        self._audit(order_id, f"→{to_state}", detail or {})
        return self.get(order_id)

    # ── 交付物：带指纹与验收标准（客户可核）──
    def deliver(self, order_id: str, *, name: str, artifact_ref: str = "",
                content: bytes | None = None, acceptance: str = "") -> dict:
        order = self.get(order_id)
        if order is None:
            raise BizError(f"订单不存在：{order_id}")
        if order["state"] not in ("accepted", "working"):
            raise BizError(f"当前状态 {STATE_LABEL.get(order['state'])} 不能交付"
                           f"（先接单/进行中）")
        sha = hashlib.sha256(content).hexdigest() if content else ""
        did = "d" + uuid.uuid4().hex[:10]
        if self.led is not None:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO biz_deliverable (id, order_id, name, artifact_ref,"
                            " sha256, acceptance, state, at) VALUES (?,?,?,?,?,?,?,?)",
                            (did, order_id, name, artifact_ref, sha, acceptance,
                             "delivered", self._iso()))
        if order["state"] == "accepted":
            self.transition(order_id, "working", detail={"reason": "首个交付物已产出"})
        self.transition(order_id, "delivered", detail={"deliverable": name, "sha256": sha})
        return {"deliverable_id": did, "name": name, "sha256": sha,
                "acceptance": acceptance, "state": "delivered"}

    def verify(self, order_id: str, *, by: str = "客户", accepted: bool = True,
               note: str = "") -> dict:
        if not accepted:
            return self.transition(order_id, "working",
                                   detail={"rejected_by": by, "note": note})
        return self.transition(order_id, "verified", detail={"by": by, "note": note})

    # ── 账单与回款（应收/已收/逾期）──
    def invoice(self, order_id: str, *, amount: float | None = None,
                kind: str = "milestone", due_at: str | None = None) -> dict:
        order = self.get(order_id)
        if order is None:
            raise BizError(f"订单不存在：{order_id}")
        amt = float(amount if amount is not None
                    else (order.get("quote_json") and
                          json.loads(order["quote_json"]).get("total") or 0.0))
        if amt <= 0:
            raise BizError("开票金额必须 > 0（先报价）")
        iid = "i" + uuid.uuid4().hex[:10]
        if self.led is not None:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO biz_invoice (invoice_id, order_id, amount, kind,"
                            " due_at, paid_at, amount_paid, created_at)"
                            " VALUES (?,?,?,?,?,?,?,?)",
                            (iid, order_id, amt, kind, due_at, None, 0.0, self._iso()))
        self._audit(order_id, "invoice", {"amount": amt, "kind": kind, "due_at": due_at})
        return {"invoice_id": iid, "order_id": order_id, "amount": amt,
                "kind": kind, "due_at": due_at, "paid": 0.0}

    def pay(self, order_id: str, *, amount: float | None = None,
            invoice_id: str | None = None) -> dict:
        order = self.get(order_id)
        if order is None:
            raise BizError(f"订单不存在：{order_id}")
        if self.led is None:
            return {"ok": False, "reason": "no_ledger"}
        with txn(self.led) as cur:
            if invoice_id:
                cur.execute("SELECT amount, amount_paid, invoice_id FROM biz_invoice"
                            " WHERE invoice_id=?", (invoice_id,))
            else:
                cur.execute("SELECT amount, amount_paid, invoice_id FROM biz_invoice"
                            " WHERE order_id=? ORDER BY created_at DESC LIMIT 1", (order_id,))
            row = cur.fetchone()
            if row is None:
                raise BizError("没有账单可核销（先开票）")
            total, paid, iid = float(row[0]), float(row[1] or 0.0), row[2]
            amt = float(amount) if amount is not None else (total - paid)
            newpaid = min(total, paid + amt)
            cur.execute("UPDATE biz_invoice SET amount_paid=?, paid_at=? WHERE invoice_id=?",
                        (newpaid, self._iso() if newpaid >= total else None, iid))
            cur.execute("SELECT COALESCE(SUM(amount),0), COALESCE(SUM(amount_paid),0)"
                        " FROM biz_invoice WHERE order_id=?", (order_id,))
            tot, pd = cur.fetchone()
        fully = float(pd or 0) >= float(tot or 0) and float(tot or 0) > 0
        if fully and order["state"] == "verified":
            self.transition(order_id, "paid", detail={"amount_paid": float(pd or 0)})
        self._audit(order_id, "pay", {"amount": amt, "fully_paid": fully})
        return {"ok": True, "invoice_id": iid, "amount": amt, "paid": float(pd or 0),
                "total": float(tot or 0), "fully_paid": fully,
                "state": (self.get(order_id) or {}).get("state")}

    # ── 汇总：在谈/在手/已回款/逾期/毛利/转化率 ──
    def status(self, *, now: float | None = None) -> dict:
        now = now if now is not None else self._now()
        out = {"orders": 0, "by_state": {}, "in_talk_amount": 0.0, "in_hand_amount": 0.0,
               "invoiced": 0.0, "collected": 0.0, "overdue_amount": 0.0,
               "overdue_orders": [], "margin": 0.0, "win_rate": None,
               "directions": {d: 0 for d in DIRECTIONS}}
        if self.led is None:
            return {**out, "reason": "no_ledger"}
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT state, COUNT(*), COALESCE(SUM("
                            "CAST(json_extract(quote_json, '$.total') AS REAL)),0)"
                            " FROM biz_order GROUP BY state")
                for st, n, amt in cur.fetchall():
                    out["by_state"][st] = n
                    out["orders"] += n
                    if st in ("inquiry", "quoted"):
                        out["in_talk_amount"] += float(amt or 0)
                    elif st in ("accepted", "working", "delivered", "verified"):
                        out["in_hand_amount"] += float(amt or 0)
                cur.execute("SELECT state, COUNT(*) FROM biz_order WHERE direction != ''"
                            " GROUP BY state")
                cur.execute("SELECT COALESCE(SUM(amount),0), COALESCE(SUM(amount_paid),0)"
                            " FROM biz_invoice")
                inv, col = cur.fetchone()
                out["invoiced"], out["collected"] = float(inv or 0), float(col or 0)
                cur.execute("SELECT invoice_id, order_id, amount, amount_paid, due_at"
                            " FROM biz_invoice WHERE due_at IS NOT NULL AND due_at < ?"
                            " AND COALESCE(amount_paid,0) < amount", (self._iso(),))
                for iid, oid, amt, paid, due in cur.fetchall():
                    out["overdue_amount"] += float(amt) - float(paid or 0)
                    out["overdue_orders"].append({"invoice_id": iid, "order_id": oid,
                                                  "due_at": due,
                                                  "outstanding": round(float(amt) - float(paid or 0), 2)})
                cur.execute("SELECT direction, COUNT(*) FROM biz_order"
                            " WHERE direction != '' GROUP BY direction")
                for d, n in cur.fetchall():
                    out["directions"][str(d)] = int(n)
            won = out["by_state"].get("verified", 0) + out["by_state"].get("paid", 0)
            lost = out["by_state"].get("lost", 0)
            out["win_rate"] = round(won / (won + lost), 3) if (won + lost) else None
        except Exception as exc:                              # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
        return out

    def list(self, limit: int = 50) -> list:
        if self.led is None:
            return []
        try:
            with txn(self.led) as cur:
                cur.execute("SELECT order_id, client, need, direction, state, due_at,"
                            " updated_at FROM biz_order ORDER BY updated_at DESC LIMIT ?",
                            (int(limit),))
                cols = [d[0] for d in (cur.description or [])]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
        except Exception:
            return []


# ══════════ 五大方向 → 接单包（一下来就知道怎么干、谁干、多少钱）══════════
def plan_direction(direction: str, *, client: str = "", hours: float | None = None,
                   rate: float | None = None, rush: float = 1.0,
                   complexity: float = 1.0) -> dict:
    """把一个方向 + 客户简报，变成可执行的接单包：范围/交付物/里程碑/报价/验收。"""
    if direction not in DIRECTIONS:
        raise BizError(f"未知方向 {direction}；可选：{list(DIRECTIONS)}")
    d = DIRECTIONS[direction]
    est_hours = float(hours or (d["hours"]["min"] + d["hours"]["max"]) / 2)
    q = Quote(hours=est_hours,
              rate=float(rate if rate is not None
                         else os.environ.get("BIZ_HOURLY_RATE", 300)),
              complexity=complexity, rush=rush).compute()
    # 里程碑：按"看现状 → 定流程 → 交付 → 验收"切三段收款
    milestones = [
        {"name": "M1 勘察与方案", "deliverable": "现状清单 + 流程方案",
         "billing": 0.3, "acceptance": "客户确认现状清单无缺项"},
        {"name": "M2 实施与自测", "deliverable": d["deliverables"][0],
         "billing": 0.4, "acceptance": "在客户环境跑通 1 遍，留证据（指纹可核）"},
        {"name": "M3 交付与培训", "deliverable": "操作说明 + 培训录音",
         "billing": 0.3, "acceptance": "客户能独立操作 1 次"},
    ]
    for m in milestones:
        m["amount"] = round(q["total"] * m["billing"], 2)
    return {"direction": direction, "client": client or "（待填）",
            "scope": d["deliverables"], "sop": d["sop"], "risk": d["risk"],
            "estimate_hours": est_hours, "price_band": d["price"],
            "quote": q, "milestones": milestones,
            "billing_advice": "分期收款：M1 开工前，M2 验收后，M3 交付后（逾期即停后续）",
            "why_this_price": ("按真实工时×单价，再加复杂度/加急系数；"
                               "区间参考 " + f"{d['price']['min']}~{d['price']['max']} 元")}


def book_notes() -> dict:
    return {**BOOK_NOTES, "directions": list(DIRECTIONS), "scenes": list(DIRECTION_SCENES)}


__all__ = ["OrderBook", "Order", "Quote", "BizError", "DIRECTIONS", "DIRECTION_SCENES",
           "STATE_LABEL", "STATES", "TRANSITIONS", "plan_direction", "book_notes"]
