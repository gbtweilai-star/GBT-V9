# tests/test_hooks_identity_expand.py —— 防偷懒钩子 / 触手身份金库 / 自动扩容 / 可插拔配备 / 3D 蓝图 / 语音操控
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）：每个要点都要有钩子防偷懒跳过与无脑操作；一根触手一个账户、主脑可查；
#   加装/连接插件自动化扩容；新用户下载即自动配备；项目要有 3D 蓝图上帝视角；数字人语音操控中心。
import pytest


# ─────────── 钩子：跳过 / 空转 / 缺步 / 重复 一律拦 ───────────
def test_hooks_block_noop_skip_missing_and_duplicate():
    from core import hooks as H
    g = H.Guard("正常", must_steps=("甲", "乙"))
    with g.step("甲") as s:
        s.evidence(x=1, fingerprint=H.fingerprint(1))
    with g.step("乙") as s:
        s.evidence(y=2, fingerprint=H.fingerprint(2))
    assert g.finish(record=False)["通过"] is True

    g2 = H.Guard("空转")
    with pytest.raises(H.HookError):
        with g2.step("什么都没干"):
            pass

    g3 = H.Guard("缺步", must_steps=("甲", "乙"))
    with g3.step("甲") as s:
        s.evidence(a=1)
    with pytest.raises(H.HookError):
        g3.finish(record=False)

    g4 = H.Guard("跳过")
    with g4.step("甲") as s:
        s.evidence(a=1)
    with g4.step("乙") as s:
        s.skip("不想做")
    with pytest.raises(H.HookError):
        g4.finish(record=False)

    g5 = H.Guard("重复")
    with g5.step("甲") as s:
        s.evidence(a=1)
    with pytest.raises(H.HookError):
        with g5.step("甲") as s:
            s.evidence(b=2)


def test_hooks_evidence_is_required():
    from core import hooks as H
    g = H.Guard("无证据")
    with pytest.raises(H.HookError):
        with g.step("声称做了") as s:
            s.evidence()          # 什么都没留 = 不许声称做完


# ─────────── 身份金库：一根触手一个位、唯一约束、主脑全量可查 ───────────
@pytest.fixture()
def vault(tmp_path, monkeypatch):
    from core import tentacle_identity as TI
    monkeypatch.setattr(TI, "DB", tmp_path / "ident.sqlite3")
    return TI


def test_one_identity_per_tentacle_per_service(vault):
    TI = vault
    assert TI.claim("t007", "邮箱", handle="a@b.com")["ok"] is True
    again = TI.claim("t007", "邮箱", handle="second@b.com")
    assert again["ok"] is False, "一根触手不能同时占两个账户"
    assert "只注册一个" in again["reason"]
    assert TI.claim("t007", "开源仓库", handle="repo")["ok"] is True   # 不同服务可以


def test_identity_requires_credentials_from_env_only(vault, monkeypatch):
    TI = vault
    TI.claim("t001", "邮箱")
    assert TI.provision("t001", "邮箱")["ok"] is False, "没凭据不许说已配"
    monkeypatch.setenv(TI.env_key("t001", "邮箱"), "secret")
    r = TI.provision("t001", "邮箱")
    assert r["ok"] and r["状态"] == "已配"
    assert "secret" not in str(r), "金库里不存明文凭据"


def test_main_brain_sees_every_tentacle(vault):
    TI = vault
    for t in ("t001", "t002", "t003"):
        TI.claim(t, "邮箱", handle=f"{t}@x.org")
    rows = TI.rows()                      # 不传 tentacle = 主脑全量视图
    assert {r["tentacle"] for r in rows} == {"t001", "t002", "t003"}
    assert len(TI.rows(tentacle="t002")) == 1
    s = TI.summary(n=3)
    assert s["身份位总数"] == 12 and "主脑" in s["主脑视图"]


def test_identity_local_attach_counts_as_ready(vault):
    TI = vault
    TI.claim("t001", "邮箱")
    TI.attach("t001", "邮箱", handle="t001.邮箱@local", state="已配")
    s = TI.summary(n=1)
    assert s["就绪"] == 1 and s["分层"]["已配"] == 1


def test_bind_is_bidirectional_recorded(vault):
    TI = vault
    TI.claim("t003", "开源仓库", handle="repo/x")
    r = TI.bind("t003", "开源仓库", "https://repo/x")
    assert r["ok"] and "↔" in r["双向"]
    assert TI.rows(tentacle="t003")[0]["bound_target"] == "https://repo/x"


# ─────────── 可插拔配备：本地自签保底、外部通道有则用、缺则如实 ───────────
def test_provision_adapters_local_first_then_external(vault, tmp_path, monkeypatch):
    from core import provision as P
    monkeypatch.setattr(P, "LOCAL_DIR", tmp_path / "local")
    r = P.provision_one("t005", "邮箱")
    assert r["ok"], "本地自签必须保底可用（下载即可用）"
    names = [x["适配器"] for x in r["尝试明细"]]
    assert names == ["本地自签", "凭据注入", "官方API", "外部脚本槽"]
    assert any(x["ok"] for x in r["尝试明细"] if x["适配器"] == "本地自签")
    # 没凭据时如实说明缺哪个环境变量
    env_try = next(x for x in r["尝试明细"] if x["适配器"] == "凭据注入")
    assert "V9ID_MAIL_T005" in env_try["detail"]


def test_provision_never_claims_to_open_accounts(vault):
    """边界写死在代码里：V9 不向第三方批量开户，只认官方 API / 凭据 / 用户脚本槽。"""
    from core import provision as P
    import inspect
    src = inspect.getsource(P)
    assert "不实现向第三方批量开户" in src
    assert "selenium" not in src and "playwright" not in src and "captcha" not in src.lower()


# ─────────── 自动扩容：扫描 → 计划 → 执行（钩子） → 复扫验证 ───────────
def test_expand_scan_plan_run_with_hook_verification(vault, tmp_path, monkeypatch):
    from core import provision as P
    from core import expand as E
    monkeypatch.setattr(P, "LOCAL_DIR", tmp_path / "local")
    s = E.scan(n=2)
    assert s["云插件"] == 100 and s["库槽"] == 100
    assert s["缺口"] > 0, "还没配备时缺口应大于 0"
    d = E.run(n=2, dry_run=True)
    assert d["dry_run"] and d["计划"]["步"]
    live = E.run(n=2, dry_run=False)
    assert live["钩子"]["通过"] is True, "扩容必须整单过钩子"
    assert live["扫描后"]["缺口"] <= live["扫描前"]["缺口"]


def test_expand_first_boot_is_idempotent(vault, tmp_path, monkeypatch):
    from core import provision as P
    from core import expand as E
    monkeypatch.setattr(P, "LOCAL_DIR", tmp_path / "local")
    a = E.first_boot(n=2)
    assert a["占位"] > 0 and a["钩子"]["通过"] is True
    b = E.first_boot(n=2)
    assert b["占位"] == 0, "首启必须幂等：重复跑不再多占"
    assert isinstance(b["缺凭据待办"], int), "缺凭据待办是计数"
    assert all("缺" in x for x in (b["待办样例"] or [])), "待办样例要逐条说缺什么"


# ─────────── 3D 蓝图：四层 + 关卡 + 无死角 ───────────
def test_blueprint_has_layers_gates_and_no_blind_spots():
    from core import blueprint as BP
    b = BP.build()
    assert len(b["层"]) == 4
    assert [L["名称"] for L in b["层"]] == ["指挥层", "能力层", "编队层", "地基"]
    for L in b["层"]:
        assert L["来源"], f"{L['名称']} 没标来源（数字不对就是图纸不对）"
        assert L["统计"], f"{L['名称']} 没有真读数"
    assert len(b["关卡"]) >= 3, "关卡层要有关卡（闸门/就绪/按键/钩子）"
    st = BP.status()
    assert st["无死角"] is True and st["缺面"] == []


def test_blueprint_page_renders_3d_without_external_assets():
    import re
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.blueprint_page import router
    app = FastAPI(); app.include_router(router); c = TestClient(app)
    r = c.get("/blueprint")
    assert r.status_code == 200
    for marker in ("3D 蓝图", "上帝视角", "preserve-3d", "关卡层", "无死角检查"):
        assert marker in r.text, f"蓝图页缺 {marker}"
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', r.text)
    assert "http://" not in r.text.replace("http://127.0.0.1", ""), "蓝图不许引外部资源"
    assert c.get("/api/blueprint/status").json()["层数"] == 4


# ─────────── 语音操控中心：分意图给依据、执行、留痕 ───────────
def test_voice_center_classifies_with_reason_and_executes():
    from core import voice_center as VC
    cases = [("你好", "寒暄"), ("记一下明天交报告", "记录"),
             ("妈妈生日要买什么", "提问"), ("打开蓝牙扫描", "命令")]
    for text, want in cases:
        got = VC.classify(text)
        assert got["intent"] == want, f"{text} → {got['intent']}"
        assert got["why"], "分意图必须给依据"
    r = VC.command("记一下：明天上午交季度报告", speak_reply=False)
    assert r["ok"] and r["意图"] == "记录" and r["钩子"]["通过"] is True
    assert VC.command("", speak_reply=False)["ok"] is False


def test_voice_center_page_and_apis():
    import re
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from panel.voice_page import router
    app = FastAPI(); app.include_router(router); c = TestClient(app)
    r = c.get("/voice")
    assert r.status_code == 200
    for marker in ("数字人语音对讲", "按住", "退出语音对讲", "允许她操作页面", "ring r1"):
        assert marker in r.text
    assert not re.search(r'href="https?://(?!127\.0\.0\.1)', r.text)
    d = c.post("/api/voice/command", json={"text": "你好", "speak": False}).json()
    assert d["意图"] == "寒暄" and d["回话"]
    assert c.get("/api/voice/center/status").status_code == 200


# ─────────── Octop 能力接入台账仍在（回归） ───────────
def test_octop_intake_still_complete():
    from core import octop_intake as OI
    from core import octop_fusion as OF
    it = OI.intake()
    assert it["原生页面数"] == len(OF.PAGES) == 64
    assert it["状态分布"].get("原生实现", 0) >= 16


# ─────────── 开户脚本槽：接口约定（V9 调它，它回句柄） ───────────
def _hook_path() -> str:
    from pathlib import Path
    return str(Path(__file__).resolve().parent.parent / "tools" / "provision_hook.py")


def test_provision_hook_contract():
    """槽的约定：给 <触手号> <服务>，stdout 最后一行回句柄；未接入如实 rc≠0；参数校验。

    这是 V9 与「用户自己的开户自动化」之间唯一的接口 —— 约定错了整条流水线就断，
    所以用真进程跑一遍（不是 mock）。每条调用的参数表都是**字面量列表**（shell=False）。
    """
    import os
    import subprocess
    import sys as _s
    hook = _hook_path()
    env = dict(os.environ)
    r = subprocess.run([_s.executable, hook, "t001", "邮箱"], capture_output=True,
                       timeout=60, env=env, shell=False)
    assert r.returncode != 0, "未接入开户通道时必须如实失败（不许假装开了号）"
    assert not (r.stdout or b"").decode("utf-8", "replace").strip()
    r2 = subprocess.run([_s.executable, hook, "t003", "邮箱"], capture_output=True,
                        timeout=60, env={**env, "V9ACCT_MAIL_T003": "t003@example.org"},
                        shell=False)
    assert r2.returncode == 0
    last = (r2.stdout or b"").decode("utf-8", "replace").strip().splitlines()[-1]
    assert last == "t003@example.org", "stdout 最后一行必须是句柄"
    rbad = subprocess.run([_s.executable, hook, "bad", "邮箱"], capture_output=True,
                          timeout=60, shell=False)
    assert rbad.returncode == 2
    rsvc = subprocess.run([_s.executable, hook, "t001", "不存在的服务"], capture_output=True,
                          timeout=60, shell=False)
    assert rsvc.returncode == 2


def test_hook_script_is_idempotent_by_contract():
    """幂等是约定的一部分：同参数重复调用必须给同一个句柄（不许再开一个）。"""
    import os
    import subprocess
    import sys as _s
    hook = _hook_path()
    env = {**os.environ, "V9ACCT_DB_T009": "db-account-t009"}
    outs = []
    for _i in range(2):
        r = subprocess.run([_s.executable, hook, "t009", "数据库"], capture_output=True,
                           timeout=60, env=env, shell=False)
        outs.append((r.stdout or b"").decode("utf-8", "replace").strip())
    assert outs[0] == outs[1] == "db-account-t009"


def test_provision_one_prefers_account_pool_over_local(vault, tmp_path, monkeypatch):
    """外部通道有货就用外部句柄（本地自签只是保底，不该顶掉真账号）。"""
    from core import provision as P
    monkeypatch.setattr(P, "LOCAL_DIR", tmp_path / "local")
    monkeypatch.setenv("V9ACCT_CLOUD_T004", "cf-account-t004")
    r = P.provision_one("t004", "云插件")
    assert "t004" in (r["handle"] or "")
    rows = [x for x in vault.rows(tentacle="t004") if x["service"] == "云插件"]
    assert rows and rows[0]["handle"] == "cf-account-t004"


def test_plan_covers_all_slots_not_just_a_sample(vault):
    """计划必须按**全量**待办排 —— 早期只按 8 条样例排，结果永远只配得动几根触手。"""
    from core import expand as E
    p = E.plan(n=100, limit=400)
    assert p["计划步数"] == 400, f"100 触手 × 4 服务应排 400 步，实得 {p['计划步数']}"
