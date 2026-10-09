# tests/test_v9_organs.py —— V9 自主层六器官 + 数据中枢（融合蒸馏机制到本体）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖：信息素场地（只追加/衰减是算的）· 心跳调度（在册处理器/迟到留痕）·
#       主动汇报（四维打分/不打扰门/去重）· 长任务（租约/检查点/续作/升级）·
#       镜像排练（落地过闸门）· 数据中枢（页面/部件/布局/盲区）
import json
import time

import pytest

from core import console_hub as HUB
from core import longrun as LR
from core import mirror as MR
from core import proactive as PA
from core import sched as SCH
from core import stigmergy as SG


# ── 信息素场地 ──
def test_stigmergy_is_append_only_and_decay_is_computed(tmp_path, monkeypatch):
    monkeypatch.setattr(SG, "LEDGER", tmp_path / "s.jsonl")
    SG.deposit("甲", {"说": "一"}, strength=1.0, key="k")
    SG.deposit("甲", {"说": "二"}, strength=1.0, key="k")
    rows = SG.read(limit=10)
    assert len(rows) == 1 and rows[0]["payload"]["说"] == "二", "同 key 只留最新"
    raw = (tmp_path / "s.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(raw) == 2, "账本必须两条都在（只追加，不删历史）"
    # 衰减是算出来的：把半衰期设成 1 秒，强度应显著下降
    old = rows[0]
    assert SG.effective(old, half_life_s=1.0, now=float(old["at"]) + 5) < 0.2
    d = SG.decay(half_life_s=1.0)
    assert d["痕迹总数"] == 2 and d["已淡出"] >= 0


# ── 心跳调度 ──
def test_sched_rejects_unknown_handler_and_records_late(tmp_path, monkeypatch):
    monkeypatch.setattr(SCH, "JOBS", tmp_path / "sched.json")
    monkeypatch.setattr(SCH, "LOG", tmp_path / "log.jsonl")
    assert SCH.define("坏的", 60, handler="不存在")["ok"] is False, "处理器不在册必须拒收"
    assert SCH.define("太快", 1, handler="stigmergy_decay")["ok"] is False, "间隔太短要拒"
    r = SCH.define("信息素", 5, handler="stigmergy_decay", first_delay_s=0)
    assert r["ok"] is True
    time.sleep(0.05)
    got = SCH.tick(limit=3)
    assert got["跑了"], "到期任务必须真跑（不是假装跑了）"
    st = SCH.status()
    assert st["任务数"] == 1 and st["任务"][0]["上次结果"], "跑完要留结果"


# ── 主动汇报 ──
def test_proactive_score_gate_and_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(PA, "FEED", tmp_path / "p.jsonl")
    # 冻结"现在"到白天：否则本地时间落在静默时段（01:00–07:00）时，本测试会在凌晨必挂（踩过）
    day = time.mktime((2026, 10, 7, 15, 0, 0, 0, 0, -1))
    monkeypatch.setattr(PA, "_now", lambda: day)
    assert PA.score(相关=1, 紧急=1, 可信=1, 价值=1) == 1.0
    assert PA.score(相关=0, 紧急=0, 可信=0, 价值=0) == 0.0
    # 不及格：不说，且记下"为什么没说"
    low = PA.consider("小事一桩", kind="新奇发现", 相关=0.1, 紧急=0.1, 可信=0.1, 价值=0.1)
    assert low["说"] is False and "及格线" in low["拦下"]
    # 静默时段：不说
    deep = PA.gate(综合分=0.9, kind="主人需要", now=time.mktime((2026, 10, 7, 3, 0, 0, 0, 0, -1)))
    assert deep["过门"] is False and "静默" in deep["拦下"]
    # 过了门就说；同 key 再来被去重
    ok = PA.consider("四视图重建需要充值", kind="主人需要", 相关=1, 紧急=0.9, 可信=1, 价值=0.9,
                     key="云额度", evidence="余额=10")
    assert ok["说"] is True
    again = PA.consider("四视图重建需要充值（第二次）", kind="主人需要", 相关=1, 紧急=0.9,
                        可信=1, 价值=0.9, key="云额度")
    assert again["说"] is False and "已经说过" in again["拦下"]
    # 预算：一小时最多 3 条
    for i in range(4):
        PA.consider(f"第{i}条", kind="新奇发现", 相关=0.9, 紧急=0.9, 可信=0.9, 价值=0.9,
                    key=f"b{i}")
    said = [r for r in PA.feed(limit=99, only_said=True)]
    assert len(said) <= PA.HOURLY_BUDGET, "一小时预算必须生效"


# ── 长任务 ──
def test_longrun_lease_checkpoint_resume_and_escalate(tmp_path, monkeypatch):
    monkeypatch.setattr(LR, "TASKS", tmp_path)
    monkeypatch.setattr(LR, "INBOX", tmp_path / "inbox.jsonl")
    t = LR.submit("跑四视图重建", steps=("备四视图", "云端重建", "本地精修"))
    tid = t["任务"]
    assert t["下一步"] == "备四视图"
    LR.checkpoint(tid, "备四视图", evidence="四张图就位")
    r = LR.resume(tid)
    assert r["复用已完成"] == ["备四视图"] and r["只重跑"] == ["云端重建", "本地精修"]
    assert LR.checkpoint(tid, "云端重建")["ok"] is True
    assert LR.checkpoint(tid, "不在计划里")["ok"] is False, "计划外的步不许记"
    assert LR.heartbeat(tid)["ok"] is True
    assert LR.escalate(tid, "余额不够")["状态"] == "等主人"
    # 租约过期：如实判"判活失败"，不假装还在跑
    t2 = LR.submit("另一件", steps=("a", "b"), lease_s=0.01)   # 显式传：默认值在定义时已绑定
    time.sleep(0.05)
    hb = LR.heartbeat(t2["任务"])
    assert hb["ok"] is False and "租约过期" in hb["状态"]


# ── 镜像排练 ──
def test_mirror_requires_real_rehearsal_before_promote(tmp_path, monkeypatch):
    monkeypatch.setattr(MR, "SPACES", tmp_path)
    sp = MR.open_space("要不要充值", 约束=("预算有限",))
    sid = sp["空间"]
    assert MR.promote(sid)["ok"] is False, "空排练不许落地"
    MR.propose(sid, "先充 200 试一轮", 依据="余额仅 10", 信心=0.7)
    MR.step(sid, "充完跑四视图", 代价="约 65 credits", 风险="仍非像素级")
    d = MR.digest(sid)
    assert "候选假设 1 条" in d["结论"]
    ok = MR.promote(sid, evidence="主人点头")
    assert ok["ok"] is True and ok["状态"] == "已落地"
    st = MR.status(sid)
    assert st["落地"] and st["落地"]["证据"] == "主人点头"


# ── 数据中枢 ──
def test_console_hub_aggregates_real_readings():
    p = HUB.pages()
    assert p["页面数"] >= 19, "在册页面必须都能读到"
    assert "指挥" in p["分组"]
    w = HUB.widgets()
    assert w["部件数"] >= 8
    for name, item in w["部件"].items():
        assert "读数" in item, f"{name} 缺读数格"
        if item["读数"] is None:
            assert item.get("原因"), f"{name} 取不到要写原因，不能空着"
    lay = HUB.layout()
    assert lay.get("行"), "布局必须有行结构"
    assert HUB.save_layout({"行": []})["ok"] is False, "空布局要拒收"
    sc = HUB.scan()
    assert "盲区" in sc and sc["盲区"]
    snap = HUB.snapshot()
    assert set(snap) >= {"pages", "graph", "widgets", "layout", "scan"}


# ── 接口与页面 ──
def test_organs_endpoints_and_page():
    from fastapi.testclient import TestClient
    from panel.server import app
    c = TestClient(app)
    assert c.get("/hub").status_code == 200
    html = c.get("/hub").text
    assert "总控台数据中枢" in html and "/api/hub/snapshot" in html
    for path in ("/api/hub/snapshot", "/api/hub/pages", "/api/hub/widgets",
                 "/api/hub/scan", "/api/hub/layout", "/api/sched/status",
                 "/api/proactive/feed", "/api/longrun/status", "/api/mirror/status",
                 "/api/stigmergy/status"):
        assert c.get(path).status_code == 200, path
    r = c.post("/api/sched/tick?limit=1")
    assert r.status_code == 200 and r.json().get("ok") is True
    r2 = c.post("/api/mirror/step", json={"op": "promote", "空间": "不存在"})
    assert r2.status_code == 200 and r2.json().get("ok") is False, "空/不存在空间不许落地"


def test_hub_page_registered_in_nav():
    from core import page_registry as PR
    cat = PR.catalog()
    assert "hub" in cat
    assert cat["hub"]["路由"] == "/hub"
    assert cat["hub"]["分组"] == "指挥"
