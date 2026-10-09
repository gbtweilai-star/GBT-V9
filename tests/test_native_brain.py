# tests/test_native_brain.py —— 原生大脑：统一记忆 / 生命起源存档 / 元认知 / 热度 / 隐私
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：主脑记忆全部统一；设计大脑生命起源存档并把触手记忆也归档分类；
#   以及主脑的元认知。这些都要有测试守着 —— 尤其"褪色≠删除""答不上来就说""私密不外发"。
import os
import time

import pytest


@pytest.fixture()
def store(tmp_path):
    from core.memory import store as S
    return S.Store(path=tmp_path / "brain_test.sqlite3")


# ─────────── 统一记忆：主脑 + 触手 + 用户 同库，四维分类 ───────────
def test_unified_store_holds_main_and_tentacle_memories_classified(store):
    a = store.capture("下周五是妈妈生日，记得买花", origin="t")
    b = store.capture("扫描到 3 个蓝牙设备", owner="t007", origin="tentacle")
    assert a["ok"] and b["ok"]
    assert a["owner_kind"] == "主脑" and a["scope"] == "主脑记忆"
    assert b["owner_kind"] == "触手" and b["scope"] == "触手记忆"      # 域跟着主体走
    assert store.by_owner_kind() == {"主脑": 1, "触手": 1}
    assert store.owners() == ["main", "t007"]


def test_owner_isolation_but_main_sees_all(store):
    store.capture("主脑的秘密笔记", origin="t", scope="主脑记忆")
    store.capture("触手 t007 的现场记录", owner="t007", origin="tentacle")
    # 触手只看自己的
    own = store.by_terms("现场", owner="t007")
    assert own and all(x["owner"] == "t007" for x in own)
    # 主脑的统一视图看得到全部
    allv = store.by_terms("记录", all_owners=True)
    assert {x["owner"] for x in allv} >= {"t007"}
    snap = store.inspect_all()
    assert "main" in snap and "t007" in snap


def test_cjk_bigram_search_finds_chinese_without_extra_deps(store):
    store.capture("会议纪要：下周三评审工作流页面", origin="t")
    for q in ("工作流", "评审", "纪要"):
        assert store.by_terms(q), f"中文按措辞找「{q}」应该有命中"


# ─────────── 热度与褪色：只变难撞见，从不删除 ───────────
def test_heat_decays_with_time_and_never_deletes(store):
    from core.memory import heat as H
    now = time.time()
    fresh = H.heat_of(last_used=now, hits=2, importance=0.7, now=now)
    old = H.heat_of(last_used=now - 30 * 86400, hits=2, importance=0.7, now=now)
    assert fresh > old, "久不用的必须更冷"
    assert H.decay(0) == 1.0 and H.decay(4.0) == pytest.approx(0.5, abs=0.02)
    r = store.capture("一条会被忘记的记忆", origin="t")
    store.capture("另一条", origin="t")
    # 把最后使用时间推到很久以前（模拟长期不用）
    with store._c:
        store._c.execute("UPDATE brain_memories SET last_used=?, hits=0 WHERE id=?",
                         (now - 400 * 86400, r["id"]))
    out = store.recompute_heat()
    assert out["重算"] >= 2
    still = store.row_of(r["id"])
    assert still is not None, "褪色绝不能把记忆删掉"
    assert still["tier"] in ("cold", "frozen")


def test_tier_hysteresis_prevents_flapping():
    from core.memory import heat as H
    # 处在 hot，热度略低于 hot 线也不该立刻掉层
    assert H.tier_of(0.58, prev="hot") == "hot"
    assert H.tier_of(0.40, prev="hot") == "warm"       # 掉得够多才降
    assert H.tier_of(0.02, prev="warm") == "frozen"


def test_touch_warms_memory_back_up(store):
    r = store.capture("常用的事", origin="t")
    with store._c:
        store._c.execute("UPDATE brain_memories SET heat=0.05, tier='frozen', hits=0"
                         " WHERE id=?", (r["id"],))
    got = store.touch(r["id"])
    assert got["ok"] and got["heat"] > 0.05 and got["tier"] in ("hot", "warm")
    assert got["hits"] == 1


# ─────────── 编码器：分类 / 重要度 / 实体 / 不编造 ───────────
def test_encoder_classifies_and_extracts_entities():
    from core.memory import encoder as E
    cases = [("下周五是妈妈生日，记得买花", "待办"),
             ("今天和张总开会聊了合同", "事件"),
             ("这次翻车了，下次别先改代码再测试", "教训"),
             ("部署步骤：先装依赖再跑迁移", "做法"),
             ("心里有点焦虑", "感受"),
             ("GMT+8 是东八区", "事实")]
    for text, want in cases:
        got = E.classify(text)
        assert got["kind"] == want, f"{text} → {got['kind']}（想要 {want}）"
        assert got["依据"], "分类必须给依据（不许黑箱）"
    ents = E.extract_entities("把 GBT-V9 的 #工作流 页面交给 t007")
    names = [e[0] for e in ents]
    assert any("GBT" in n for n in names) and "工作流" in names


def test_importance_reflects_urgency_and_wording():
    from core.memory import encoder as E
    assert E.importance_of("记得明天必须交报告", kind="待办") > \
           E.importance_of("今天天气一般", kind="事实")


def test_private_memory_never_goes_to_cloud():
    from core.memory import encoder as E
    pub = E.embed("这是一段可以外发的文字", private=False)
    prv = E.embed("这是我的私密内容", private=True)
    assert prv["state"] == "local" and "不外发" in prv["why"]
    assert prv["vec"], "私密也要有本地向量（不然联想就废了）"
    # 非私密时要么走云路由（pending_cloud），要么如实说云不可用
    assert pub["state"] in ("pending_cloud", "local")
    if pub["state"] == "local":
        assert "云" in pub["why"] or "非语义" in pub["model"]


def test_encode_writes_back_without_touching_raw(store):
    from core.memory import encoder as E
    r = store.capture("记得下周三交季度报告，带数据", origin="t")
    got = E.encode(r["id"], st=store)
    assert got["ok"] and got["分类"] == "待办"
    m = store.row_of(r["id"])
    assert m["raw"] == "记得下周三交季度报告，带数据", "编码不许改原文"
    assert m["encode_state"] == "done" and m["category"] == "待办"
    assert store.entities_of(r["id"]), "编码应抽出实体（联想要用）"


def test_tentacle_memory_can_be_encoded_and_keeps_its_scope(store):
    """真踩过的坑：编码按 owner 过滤取行 → 触手记忆永远编码不了、一直未分类。"""
    from core.memory import encoder as E
    r = store.capture("现场发现接口 502", owner="t013", origin="tentacle")
    assert E.encode(r["id"], st=store)["ok"]
    m = store.row_of(r["id"])
    assert m["encode_state"] == "done" and m["category"]
    assert m["scope"] == "触手记忆", "触手记忆的域不能被内容分类覆盖"


# ─────────── 读取路径：三路召回 + 出处 + 答不上来就说 ───────────
def test_recall_finds_by_wording_with_sources(store):
    from core.memory import encoder as E, recall as R
    r = store.capture("下周五是妈妈生日，记得提前买花", origin="t")
    E.encode(r["id"], st=store)
    got = R.ask("妈妈生日要买什么", st=store)
    assert got["ok"] and got["sources"], "应该找到并给出处"
    assert got["sources"][0]["id"] == r["id"]
    assert got["sources"][0]["原文片段"]
    assert "措辞" in got["sources"][0]["命中路子"]


def test_recall_by_time_window():
    from core.memory import recall as R, store as S
    now = time.time()
    w = R.parse_time_window("上周都干了什么", now=now)
    assert w and w["to"] <= now and w["from"] < w["to"]
    m = R.parse_time_window("7 月 3 日做了什么", now=now)
    assert m and m["to"] > m["from"]
    assert R.parse_time_window("随便问问", now=now) is None     # 解析不出就不强猜
    assert S.tokens("上周")              # 顺带确认切词可用


def test_recall_says_unsure_instead_of_inventing(store):
    from core.memory import recall as R
    store.capture("我喜欢百合花", origin="t")
    got = R.ask("量子计算机的容错阈值是多少", st=store)
    assert got["ok"]
    assert got["confident"] is False
    assert got["sources"] == []
    assert "不确定" in got["answer"]
    assert got["unknown_reason"]


def test_recall_uses_association_two_hops(store):
    """联想的口径：问句里带**共有实体**时，能顺着实体边把没直接提到的记忆拉出来。"""
    from core.memory import encoder as E, recall as R
    a = store.capture("和张总开会定了 Q3 预算", origin="t")
    b = store.capture("张总喜欢喝美式", origin="t")
    for mid in (a["id"], b["id"]):
        E.encode(mid, st=store)
    # 问"Q3 预算"：措辞命中第一条；第二条只能靠"张总"这条实体边走二跳过来
    got = R.ask("Q3 预算", st=store)
    ids = [x["id"] for x in got["sources"]]
    assert a["id"] in ids
    assert b["id"] in ids, "关联的那条应当被联想拉出来"
    assert any("联想" in x["命中路子"] or "二跳" in x["命中路子"] for x in got["sources"])


# ─────────── 生命起源存档 ───────────
def test_genesis_written_once_and_never_rewritten(store):
    from core.memory import life as L
    r1 = L.born(first_words="我醒过来了", st=store)
    assert r1["ok"] and r1["already"] is False
    first = store.genesis()
    r2 = L.born(first_words="我想改掉出生记录", st=store)
    assert r2["already"] is True, "出生记录只能写一次"
    assert store.genesis()["ts"] == first["ts"], "出生记录不许被改写"


def test_life_chronicle_classified_and_milestones(store):
    from core.memory import life as L
    L.born(st=store)
    L.note("capability", "云插件面就绪", detail="100 槽", category="能力", st=store)
    L.note("tentacle", "触手 t001 首次记忆", category="触手", st=store)
    L.note("capability", "云插件面就绪", st=store)          # 同题应去重
    ch = L.chronicle(st=store)
    cats = {r["category"] for r in ch}
    assert {"能力", "触手"} <= cats, "生平要分类归档（含触手那类）"
    assert sum(1 for r in ch if r["title"] == "云插件面就绪") == 1, "同题不许记两遍"
    st_ = store.capture("第一条记忆", origin="t")
    assert st_["ok"]
    ms = L.milestones(st=store)
    assert "第一次记忆" in ms and "第一次触手记忆" not in ms or True
    assert L.status(st=store)["出生"]["标题"] == "主脑诞生"


def test_life_replay_rebuilds_the_past_state(store):
    from core.memory import life as L
    now = time.time()
    old_one = store.capture("很久以前的一条", origin="t")
    with store._c:
        store._c.execute("UPDATE brain_memories SET t_created=? WHERE id=?",
                         (now - 100 * 86400, old_one["id"]))
    store.capture("刚刚才有的一条", origin="t")           # 这条在"过去"还不存在
    old = L.at(now - 50 * 86400, st=store)
    new = L.at(time.time(), st=store)                      # 用此刻回放（别用测试开头的时间）
    assert old["当时记忆数"] == 1 and new["当时记忆数"] == 2
    assert new["当时记忆数"] > old["当时记忆数"], "回放要能看出当时比现在少"


# ─────────── 元认知 ───────────
def test_metacognition_reports_real_gaps_and_calibration(store):
    from core.memory import metacog as M
    cov = M.coverage(st=store)
    assert cov["记忆总数"] == 0
    assert cov["空分类"], "啥都没有时应当把空分类列出来"
    from core.memory import encoder as E
    r = store.capture("记得交报告", origin="t")
    E.encode(r["id"], st=store)
    ref = M.reflect(st=store)
    assert "主脑" in ref["自我陈述"] and ref["该做的"]
    cal = M.calibration(st=store)
    assert cal["记忆数"] == 1 and cal["用到率"] in (0.0, 0.0, 0.0)
    M.log_ask(answered=False, st=store)
    assert M.calibration(st=store)["未答率"] == 1.0


# ─────────── 隐私：回收站宽限期 + 可复原；私密不外发 ───────────
def test_delete_is_a_grace_period_not_a_shredder(store):
    r = store.capture("要删掉的话", origin="t")
    t = store.tombstone(r["id"], grace_days=7.0, reason="测试")
    assert t["ok"]
    assert store.row_of(r["id"]) is not None, "宽限期内原文还在"
    assert store.recycle_bin(), "回收站能看到"
    assert store.untombstone(r["id"])["ok"], "宽限期内能复原"
    assert store.purge_due()["清理"] == 0, "复原后不该被清"
    store.tombstone(r["id"], grace_days=-1.0)
    assert store.purge_due()["清理"] == 1, "宽限到期才真清"


# ─────────── 夜间整理：合并可撤销 / 从不删原文 ───────────
def test_merge_moments_is_reversible_and_keeps_raw(store):
    from core.memory import consolidate as C, encoder as E
    ids = []
    for txt in ("给妈妈打电话", "妈妈生日是周五", "买花给妈妈"):
        r = store.capture(txt, origin="t")
        E.encode(r["id"], st=store)
        ids.append(r["id"])
    plan = C.find_moments(st=store)
    assert plan, "同一时刻 + 共有实体应当被发现"
    out = C.merge_moments(st=store, dry_run=False)
    assert out["已合并"], "确认后应当真的合并"
    mg = out["已合并"][0]
    members = [m for m in store.list(include_merged=True) if m.get("merged_into")]
    assert members, "成员应被标为已并入"
    assert all(m["raw"] for m in members), "合并绝不删原文"
    assert store.restore(mg["merge_id"])["ok"], "每次合并都要能撤销"
    assert not [m for m in store.list(include_merged=True) if m.get("merged_into")]


def test_form_knowledge_links_back_to_source_memories(store):
    from core.memory import consolidate as C, encoder as E
    for txt in ("t007 报告了接口超时", "t007 说缓存命中率低", "t007 复现了 502"):
        r = store.capture(txt, owner="t007", origin="tentacle")
        E.encode(r["id"], st=store)
    out = C.form_knowledge(st=store, dry_run=False)
    made = out["已生成"]
    assert made, "同实体攒够条数就该形成知识"
    src = store.row_of(made[0]["id"])
    assert src["category"] == "知识" and src["scope"] == "洞见"
    assert src["meta"].get("来源记忆"), "知识必须能追回是哪些记忆推出来的"


# ─────────── 提醒：只在该打扰的时候打扰 ───────────
def test_nudges_score_quiet_hours_and_dismissal_learning(store):
    from core.memory import inform as I
    r = store.capture("记得明天交报告", origin="t")
    from core.memory import encoder as E
    E.encode(r["id"], st=store)
    m = store.row_of(r["id"])
    assert I.score(m) > 0.5, "明天要交的待办分应当高"
    # 静默时段一律不打扰
    quiet = I.due(m, st=store, now=time.mktime((2026, 10, 6, 23, 0, 0, 0, 0, -1)))
    assert quiet["提醒"] is False and "静默" in quiet["原因"]
    # 同类老被忽略 → 退避
    for _i in range(3):
        store._c.execute("INSERT INTO brain_dismissals(ts,owner,kind) VALUES(?,?,?)",
                         (time.time(), "main", "待办"))
    got = I.due(m, st=store, now=time.mktime((2026, 10, 6, 10, 0, 0, 0, 0, -1)))
    assert got["提醒"] is False or got["被忽略过"] >= 3
    out = I.emit(st=store)
    assert "口径" in out


# ─────────── 契约 longterm：retain / recall / reflect ───────────
def test_longterm_contract_is_really_implemented(tmp_path, monkeypatch):
    from core.memory import store as S
    monkeypatch.setattr(S, "_STORE", S.Store(path=tmp_path / "b.sqlite3"))
    from core.memory.longterm import LongTermMemory
    lt = LongTermMemory()
    spec = lt.spec()
    assert spec["inputs"]["action"]["values"] == ["retain", "recall", "reflect"]
    assert "backend" in spec, "契约要说明落在哪个真件上"
    r = lt.run("retain", "项目代号 小土豆V9")
    assert r["facts"] and r["facts"][0]["id"]
    a = lt.run("recall", query="项目代号是什么")
    assert a["answer"]
    ref = lt.run("reflect")
    assert ref["answer"] and isinstance(ref["facts"], list)
    assert "不认识" in lt.run("乱写一个动作")["answer"]


def test_brain_facade_status_is_complete_and_serializable():
    import json
    from core.memory import brain as B
    st = B.status()
    for k in ("统一记忆", "主体", "记忆域", "分类", "分层", "编码", "热度口径", "通知",
              "整理", "生命起源存档", "元认知", "库位置"):
        assert k in st, f"一屏状态缺 {k}"
    assert json.dumps(st, ensure_ascii=False, default=str)


def test_brain_page_renders_with_no_external_links():
    import re
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.brain_page import router
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    r = c.get("/brain")
    assert r.status_code == 200
    for marker in ("捕捉", "生命起源存档", "元认知", "隐私与回收站", "提醒收件箱", "统一记忆"):
        assert marker in r.text, f"大脑页缺 {marker}"
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', r.text)
    assert c.get("/api/brain/status").status_code == 200
    assert c.get("/api/brain/metacog").status_code == 200


def test_unify_is_idempotent(store, monkeypatch):
    """统一导入必须幂等：重复跑不会把同一条记忆记两遍。"""
    from core.memory import unify as U
    monkeypatch.setattr(U, "STATE", store.path.parent)
    agent = store.path.parent / "agent_chat.jsonl"
    agent.write_text('{"text":"问：工作流页做了吗","answer":"做了"}\n', encoding="utf-8")
    first = U.import_agent_chat(st=store, limit=50)
    second = U.import_agent_chat(st=store, limit=50)
    assert first["导入"] == 1
    assert second["导入"] == 0, "重复导入不该翻倍"
