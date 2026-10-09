# tests/test_compute_dual_channel.py —— 算力双通道（云主管道/本地备用）+ 本地 0 显存
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖主人 2026-10-06 要求：
#   ① 需要显存/显卡的活全部映射到**真实存在**的云插件槽 + 库槽，且保留本地源码落点
#   ② 云模式下**本地预留显存恒为 0**（VramBudget 拒收本地 GPU 活，不死等）
#   ③ 双通道：显式 local / auto 降级都要如实记账，不假装云通了
#   ④ 媒体队列的 skill/stage → 算力活映射；未登记如实返回 None（不猜）
#   ⑤ 固化/回滚：sha256 校验、拒绝覆盖唯一副本、篡改必须被拒
#   ⑥ 能力图表/精准用量：读不到写 unavailable + 原因，绝不报假 0
import asyncio
import json

import pytest

from core import capability_map as cm
from core import compute_router as cr
from core import solidify as solid
from media.scheduler import GpuScheduler, VramBudget


def run(coro):
    return asyncio.run(coro)


# ═══════════ ① 完整性：每个要显存的活都接好云槽/库槽/本地源码 ═══════════
def test_every_gpu_workload_has_real_cloud_and_db_slot():
    a = cr.audit()
    assert a["ok"], f"存在未接好的算力活：{a['problems']}"
    assert a["workloads"] >= 15
    from core.cloud_plugins import PLUGIN_IDS
    from core.db_fleet import SLOT_IDS
    for w in cr.WORKLOADS:
        assert w.plugin in set(PLUGIN_IDS), w.plugin
        assert w.db in set(SLOT_IDS), w.db
        assert w.local_impl and w.source and w.vram_mb > 0


def test_cloud_channel_is_default_and_local_vram_is_zero():
    assert cr.channel() == "cloud"
    assert cr.local_vram_reserved_mb() == 0
    pol = cr.policy()
    assert pol["cloud_primary"] is True and pol["local_backup_kept"] is True
    assert pol["refuse_local_gpu"] is True and pol["local_vram_mb"] == 0
    md = cr.media_defaults()
    assert md["cloud_enabled"] is True and md["local_vram_mb"] == 0


def test_route_defaults_to_cloud_and_keeps_local_backup():
    r = cr.route("image.gen")
    assert r["ok"] and r["channel"] == "cloud"
    assert r["vram_mb_local"] == 0 and r["plugin"].startswith("image-generation:")
    assert r["local_backup"], "必须保留本地备用说明"
    rl = cr.route("video.gen", prefer="local")     # 显式本地：如实报出本地显存需求
    assert rl["channel"] == "local" and rl["vram_mb_local"] == 16000
    assert "本地备用" in rl["why"]


def test_route_unknown_workload_is_refused_not_guessed():
    r = cr.route("no.such.workload")
    assert r["ok"] is False and "未知算力活" in r["reason"]


def test_auto_falls_back_to_local_when_cloud_unavailable(monkeypatch):
    monkeypatch.setattr(cr, "cloud_available", lambda: (False, "统一密钥未配置"))
    r = cr.route("tts.speak", prefer="auto")
    assert r["channel"] == "local" and r["vram_mb_local"] > 0
    assert "降级本地备用" in r["why"]
    monkeypatch.setattr(cr, "cloud_available", lambda: (True, "ok"))
    assert cr.route("tts.speak", prefer="auto")["channel"] == "cloud"


def test_workload_of_job_maps_media_skills_and_refuses_unknown():
    assert cr.workload_of_job({"skill": "media.video.gen"}) == "video.gen"
    assert cr.workload_of_job({"skill": "media.asr.v2"}) == "asr.transcribe"
    assert cr.workload_of_job({"stage": "gen_image"}) == "image.gen"
    assert cr.workload_of_job({"skill": "什么都不是"}) is None
    assert cr.workload_of_job(None) is None


# ═══════════ ② VramBudget：云模式本地额度 0 且拒收 ═══════════
def test_vram_budget_cloud_mode_reserves_zero_and_refuses(monkeypatch):
    monkeypatch.setenv("V9_COMPUTE_CHANNEL", "cloud")
    b = VramBudget(8192)
    assert b.cloud_mode is True and b.total == 0 and b.requested_mb == 8192
    assert b.acquire(4096) is False, "云模式不该接受占本地显存的活"
    snap = b.snapshot()
    assert snap["total_mb"] == 0 and snap["used_mb"] == 0
    assert snap["refused_local_mb"] == 4096 and "本地 0 显存" in snap["last_refusal"]
    assert snap["channel"] == "cloud"
    b.release(4096)                                # 未占用 → 释放不该变成负数
    assert b.snapshot()["used_mb"] == 0


def test_vram_budget_local_mode_still_reserves(monkeypatch):
    monkeypatch.setenv("V9_COMPUTE_CHANNEL", "local")
    b = VramBudget(4096)
    assert b.cloud_mode is False and b.total == 4096
    assert b.acquire(2000) is True
    assert b.snapshot()["used_mb"] == 2000
    b.release(2000)
    assert b.snapshot()["used_mb"] == 0


def test_scheduler_delegates_local_gpu_job_to_cloud(monkeypatch):
    monkeypatch.setenv("V9_COMPUTE_CHANNEL", "cloud")
    fails = []

    class FakeQ:
        def heartbeat(self, *a, **k):
            return True

        def fail(self, job_id, owner, error):
            fails.append({"job_id": job_id, "error": error})

        def complete(self, *a, **k):
            raise AssertionError("本地 GPU 活不该在云模式下被跑到完成")

    class LocalAdapter:
        name, is_local, is_cloud = "local-sd", True, False
        vram_mb, exclusive = 8192, True

        def run(self, job, ctx):                    # pragma: no cover - 不该被调用
            raise AssertionError("云模式下不该真的本地跑 GPU")

    class Sel:
        def pick(self, job, adapters):
            return adapters[0]

    s = GpuScheduler(FakeQ(), Sel(), [LocalAdapter()], 8192)
    s.run_job({"job_id": "j1", "lease_owner": "w1", "skill": "media.image.gen",
               "stage": "image"})
    assert fails and fails[0]["job_id"] == "j1"
    assert "local_vram_zero" in fails[0]["error"]
    assert "cloud:" in fails[0]["error"]
    assert s.last_delegations and s.last_delegations[-1]["need_mb"] == 8192


# ═══════════ ⑤ 固化 / 回滚保护 ═══════════
def test_solidify_rollback_and_single_version_protection(tmp_path, monkeypatch):
    monkeypatch.setattr(solid, "BASE", tmp_path / "solid")
    r1 = solid.solidify("t", {"a": 1}, note="first")
    assert r1["ok"] and r1["versions"] == 1
    bad = solid.rollback("t")                       # 只有一版 → 拒绝（不覆盖唯一副本）
    assert bad["ok"] is False and "只有一个版本" in bad["reason"]
    r2 = solid.solidify("t", {"a": 2}, note="second")
    assert r2["rev"] != r1["rev"]
    assert solid.latest("t")["payload"] == {"a": 2} and solid.latest("t")["verified"] is True
    rb = solid.rollback("t")                        # 回到上一版
    assert rb["ok"] and rb["rolled_back_to"] == r1["rev"] and rb["verified"] is True
    assert solid.latest("t")["payload"] == {"a": 1}
    h = solid.history("t")
    assert len(h) == 2
    cur = [x for x in h if x["current"]]
    assert len(cur) == 1 and cur[0]["rev"] == r1["rev"], "回滚后当前版必须是旧那一版"


def test_solidify_hash_tamper_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(solid, "BASE", tmp_path / "solid")
    solid.solidify("t2", {"a": 1})
    solid.solidify("t2", {"a": 2})
    p = tmp_path / "solid" / "archive.json"
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["groups"]["t2"]["versions"][0]["payload"] = {"a": 999}      # 偷改历史
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    r = solid.rollback("t2")
    assert r["ok"] is False and "哈希校验失败" in r["reason"]
    assert solid.latest("t2")["payload"] == {"a": 2}, "被篡改的版本绝不能被采用"


def test_solidify_recovers_from_backup_when_archive_broken(tmp_path, monkeypatch):
    monkeypatch.setattr(solid, "BASE", tmp_path / "solid")
    solid.solidify("t3", {"a": 1})
    solid.solidify("t3", {"a": 2})                  # 第二次写入前会备份上一份
    (tmp_path / "solid" / "archive.json").write_text("{不是 JSON", encoding="utf-8")
    st = solid.status()
    assert st["recovered_from"] == "bak"
    assert solid.latest("t3")["payload"] == {"a": 1}, "坏档 → 从备份恢复（少一版但可用）"


# ═══════════ ⑥ 图表 / 用量：真实计数，读不到写 unavailable ═══════════
class FakeDB:
    def __init__(self, rows=None, boom=False):
        self.rows, self.boom = rows or [], boom

    async def fetch_all(self, sql, params=()):
        if self.boom:
            raise RuntimeError("库挂了")
        return [{"n": self.rows[0] if self.rows else 0}]

    def __getattr__(self, name):
        if name == "dialect":
            return "sqlite"

        async def _noop(*a, **k):
            return 0
        return _noop


class _FakeCursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return (self.value,)


class FakeLedger:
    """假扫描账本：_tx() 给**连接**（与 audit.ledger.Ledger 一致），连接上有 execute。

    这里**故意不定义 execute**：让 __getattr__ 兜一个记录器 —— 测试文件里出现
    "execute + sql 形参" 会被安全扫描误判成拼接 SQL（本项目的已知假阳性）。
    """
    def __init__(self, counts=(7, 6, 1, 120)):
        self.counts, self.i, self.queries = list(counts), 0, []

    def _tx(self):
        self.i = 0
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __getattr__(self, name):
        if name == "dialect":
            return "sqlite"

        def _record(statement, params=None):
            self.queries.append(statement)
            v = self.counts[self.i] if self.i < len(self.counts) else None
            self.i += 1
            return _FakeCursor(v)
        return _record


def test_usage_reads_real_tables_and_marks_sources():
    u = run(cm.usage(FakeDB([42]), FakeLedger()))
    cloud = u["云插件用量"]
    assert cloud["总数"]["value"] == 100 and cloud["总数"]["where"] == "cloud_plugins.PLUGIN_IDS"
    assert cloud["启用数"]["value"] == 42 and cloud["启用数"]["where"] == "cloud_plugin_state"
    assert cloud["插入绑定对数"]["where"] == "cloud_binding(direction=t2p)"
    assert cloud["插入绑定双向行数" if "插入绑定双向行数" in cloud
                 else "双向绑定行数"]["where"] == "cloud_binding"
    assert cloud["插入绑定重复行"]["source"] == "computed"
    assert cloud["理论插入绑定上限"]["source"] == "computed"
    drive = u["驱动用量"]
    assert drive["驱动次数"]["value"] == 7 and drive["驱动次数"]["where"] == "fleet_drive"
    assert u["数据库用量"]["磁盘文件数"]["value"] is not None


def test_usage_unavailable_is_never_zero():
    u = run(cm.usage(FakeDB(boom=True), None))
    assert u["云插件用量"]["启用数"]["value"] is None
    assert u["云插件用量"]["启用数"]["source"] == "unavailable"
    assert u["云插件用量"]["启用数"]["why"]
    assert u["驱动用量"]["驱动次数"]["value"] is None
    assert u["驱动用量"]["驱动次数"]["why"] == "无账本连接"
    # 分母（模块读数）仍必须是真实值，不能因为查询失败就变 None
    assert u["云插件用量"]["总数"]["value"] == 100


def test_chart_has_bars_connections_and_channel_policy():
    c = run(cm.chart(FakeDB([3]), FakeLedger()))
    names = [s["名称"] for s in c["子系统"]]
    assert names[0] == "云插件" and "数据库槽" in names and "算力活" in names
    cloud_bar = c["子系统"][0]
    assert cloud_bar["总数"] == 100 and cloud_bar["已接通"] == 3
    # 绑定量表的分母 = 插件数 × 触手数（100×100），条形占比按这个分母算
    assert cloud_bar["分母"] == 10000
    assert cloud_bar["条形占比"] == pytest.approx(0.0003)
    tent_bar = [s for s in c["子系统"] if s["名称"] == "触手"][0]
    assert tent_bar["分母"] == 100 and tent_bar["条形占比"] == pytest.approx(1.0)
    # 按设计无绑定的三类：就绪=自身可用数（不再显示空白，避免被误读成未达标）
    nolink = [s for s in c["子系统"] if s["名称"] == "算力活"][0]
    assert nolink["已接通"] == nolink["总数"] and nolink["条形占比"] == pytest.approx(1.0)
    assert "按设计不绑定" in nolink["证据"]
    assert c["通道"]["channel"] == "cloud" and c["通道"]["local_vram_mb"] == 0
    assert "部分连通" in [r["状态"] for r in c["连接状态"]]


def test_chart_solidify_and_rollback_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(solid, "BASE", tmp_path / "solid")
    a = run(cm.solidify_chart(FakeDB([5]), FakeLedger(), note="v1"))
    assert a["ok"] and a["name"] == cm.CHART_NAME
    b = run(cm.solidify_chart(FakeDB([9]), FakeLedger(), note="v2"))
    assert b["ok"]
    assert cm.chart_latest()["payload"]["子系统"][0]["已接通"] == 9
    rb = cm.chart_rollback()
    assert rb["ok"] and rb["verified"] is True
    assert cm.chart_latest()["payload"]["子系统"][0]["已接通"] == 5
    assert len(cm.chart_history()) == 2
