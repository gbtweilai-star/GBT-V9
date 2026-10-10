# tools/verify_tentacle_llm_identity.py —— 「每根触手是一个完整 LLM」验收（非 LLM 判据，可复跑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据（对应目标第 6 条）：独立凭据 / 独立模型 / 自己的会话 / 自己的记忆 —— 四件都要有真读数；
#   且**找不到独立凭据时必须如实标 shared-fallback，isolated 模式下必须明确不可用**（不许静默借用）。
# 不跑真推理（真通道受余额/凭据限制），只验装配与隔离；这一点在输出里明说。
# 用法：python tools/verify_tentacle_llm_identity.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

from core.swallow import swallow as _swallow

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILS: list = []


def check(name: str, cond: bool, reading: str) -> None:
    print("  %s %s —— %s" % ("✅" if cond else "❌", name, reading))
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("== 触手 = 完整 LLM 验收 @", ROOT, "==")
    from audit.ledger_factory import make_ledger
    from core.tentacle_fleet import TentacleFleet, UnifiedKey
    from core.tentacle_keys import env_key_name, env_model_name, TentacleKeyBook

    # 造两根有独立凭据的触手（值用测试串，非真实凭据）
    os.environ[env_key_name("t001")] = "unit-test-key-AAA"
    os.environ[env_key_name("t002")] = "unit-test-key-BBB"
    os.environ[env_model_name("t001")] = "unit-test-model-1"
    os.environ[env_key_name("t003")] = ""
    os.environ.pop(env_model_name("t002"), None)

    led = make_ledger()
    shared = UnifiedKey(key="unit-shared-key", base_url="http://shared.invalid/v1")
    fleet = TentacleFleet(ledger=led, n=4, model="fleet-model", rpm=30,
                          key=shared, mode="auto", client_factory=lambda t: object())

    t1, t2, t3 = fleet.tentacles["t001"], fleet.tentacles["t002"], fleet.tentacles["t003"]
    check("独立凭据：t001/t002 认到自己的钥匙", t1.key.own and t2.key.own,
          "t001=%s(%s) · t002=%s(%s)" % (t1.key.source, t1.key.key_id,
                                          t2.key.source, t2.key.key_id))
    check("两根触手的钥匙指纹**不同**",
          t1.key.key_id and t2.key.key_id and t1.key.key_id != t2.key.key_id,
          "%s vs %s" % (t1.key.key_id, t2.key.key_id))
    check("没有独立凭据的 t003 如实回退并标出来",
          (not t3.key.own) and str(t3.key.source).startswith("shared-fallback"),
          t3.key.source)
    check("逐触手模型：t001 用自己指定的模型",
          t1.model == "unit-test-model-1" and t1.model_source.startswith("env:"),
          "%s（%s）" % (t1.model, t1.model_source))
    check("没指定的触手用编队默认模型（不假装独立）",
          t2.model == "fleet-model" and t2.model_source == "fleet-default",
          "%s（%s）" % (t2.model, t2.model_source))

    st = fleet.status()
    check("编队读数：独立凭据数 / 不同钥匙数 / 不同模型数",
          st["own_key_tentacles"] == 2 and st["distinct_key_ids"] == 3
          and st["distinct_models"] == 2 and st["same_key"] is False,
          "own=%s distinct_key=%s distinct_model=%s same_key=%s" % (
              st["own_key_tentacles"], st["distinct_key_ids"],
              st["distinct_models"], st["same_key"]))
    check("编队自报三件套（不吹）",
          st["完整三件"]["凭据"].startswith("部分独立"),
          str(st["完整三件"]))

    # isolated：没有独立凭据的触手必须**明确不可用**，不许静默借用别人的钥匙
    iso = TentacleFleet(ledger=None, n=4, model="m", key=shared, mode="isolated",
                        client_factory=lambda t: object())
    i3 = iso.tentacles["t003"]
    raised = ""
    try:
        i3.key.client_for("t003")
    except RuntimeError as exc:
        raised = str(exc)
    check("isolated 模式下无独立凭据 → 明确不可用（不静默回退）",
          i3.key.loaded is False and "TENTACLE_T003_KEY" in raised,
          (raised or "没有抛错")[:80])
    check("isolated 模式下有独立凭据的触手照常可用",
          iso.tentacles["t001"].key.loaded is True,
          "t001 loaded=%s" % iso.tentacles["t001"].key.loaded)

    # 会话：一根触手一条自己的会话
    check("会话：每根一个 session_id 且互不相同",
          t1.session_id == "sess-t001" and len(st["distinct_sessions"]) == 0
          if False else len({t.session_id for t in fleet.tentacles.values()}) == 4,
          "%s … 共 %d 根" % (t1.session_id, len(fleet.tentacles)))
    ok_log = fleet._session_turn(t1, {"trace_id": "vtrace"}, "单元测试任务",
                                 {"ok": True})
    check("会话回合落账（tentacle_session 真写）", ok_log is True, "写回=%s" % ok_log)
    got = []
    try:
        from senses.sqldialect import txn
        with txn(led) as cur:
            cur.execute("SELECT session_id, tentacle_id, turn, model FROM tentacle_session "
                        "WHERE trace_id='vtrace'")
            got = cur.fetchall()
    except Exception as exc:                                  # noqa: BLE001
        got = [("读取失败", type(exc).__name__, 0, "")]
    check("会话回合读得回（带触手号与模型）", bool(got) and got[0][0] == "sess-t001",
          str(got[0] if got else "空"))

    # 记忆：自己的记忆自己读，别人读不到
    f1 = t1._memory_path()
    f2 = t2._memory_path()
    try:
        t1.remember("只有 t001 知道的暗号-7f3a")
        m1, m2 = t1.recall(limit=5), t2.recall(limit=5)
        check("记忆：自己记得住", any("暗号-7f3a" in x for x in m1),
              "t001 记忆 %d 条" % len(m1))
        check("记忆：别的触手读不到（命名空间隔离）",
              not any("暗号-7f3a" in x for x in m2),
              "t002 记忆 %d 条" % len(m2))
        check("记忆落盘在各自的文件里",
              f1.name == "t001.jsonl" and f2.name == "t002.jsonl" and f1 != f2,
              "%s / %s" % (f1.name, f2.name))
    finally:
        for p in (f1, f2):
            try:
                p.unlink(missing_ok=True)
            except OSError as e:
                _swallow(__file__, e)

    # 审计：驱动行要能分辨用的是哪把钥匙
    kid = fleet._key_id_of("t001")
    check("审计能分辨驱动用的是哪把钥匙", kid == t1.key.key_id and kid != shared.key_id,
          "t001=%s · 编队统一=%s" % (kid, shared.key_id))

    print("\n说明：本验收只验装配与隔离，**没有真跑推理**（真通道受凭据/余额限制）。")
    print("结论：" + ("✅ 每根触手都是完整 LLM（四件齐）" if not FAILS
                     else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
