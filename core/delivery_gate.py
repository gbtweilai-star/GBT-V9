# core/delivery_gate.py —— 交付闸：**每个能力必须单独跑通闭环才准交付**
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人的话（2026-10-09）：「从限制开始，每一个能力必须单独跑通闭环才能交付，要不然我不管你
#   做了多久我直接丢垃圾桶不带说的。不管多大的项目，你只要设计好架构和蓝图，后面就开始使用
#   我们自己独有的模块式部署，以及每天重启之后自动排查是否有故障和报警，不管是什么报警都要
#   给我追踪根因修复好，别来一句不影响运行就跳过了，这样操作实在找死懂吗。」
#
# 本件是**硬闸**（不是文档）：
#   ① 每个能力登记：名字 · 类目 · 它的**独立验收器**（能单跑）· 绑定条 · 模块文件；
#   ② audit()：**逐个真跑**那个验收器，拿到退出码与结论 → 通过=可交付 / 失败=**禁止交付**；
#   ③ 没有验收器的能力一律 **禁止交付**（"我没给它写闭环"也是不合格）；
#   ④ 结论落台账（可复跑、可比对昨天）。
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "delivery_gate.jsonl"

# 能力登记表：能力 → 独立验收器（一条一跑，缺一不可）
CAPABILITIES: tuple = (
    ("实时视觉闭环（脑眼手）", "感知", "tools/verify_vision_loop.py"),
    ("亿万级触手（寻址/实体/钥匙派生）", "编队", "tools/verify_tentacle_scale.py"),
    ("编队交叉扫描（互相监督）", "安全", "tools/verify_cross_scan.py"),
    ("触手邮箱页 + 账户密钥页", "页面", "tools/verify_pages_mail_accounts.py"),
    ("火力全开（原样·沙盒·只从触手吐）", "模型", "tools/verify_full_power.py"),
    ("牢房与吐口（关得住·封印）", "模型", "tools/verify_sandbox_emit.py"),
    ("接手协议（护栏挂接手这一刻）", "协作", "tools/verify_takeover.py"),
    ("触手随身库（一丢即走·慢看）", "存储", "tools/verify_tentacle_store.py"),
    ("触手册子与自配装备", "编队", "tools/verify_tentacle_vaults.py"),
    ("触手职业名册", "编队", "tools/verify_tentacle_profession.py"),
    ("自配装备与账号", "编队", "tools/verify_tentacle_equip.py"),
    ("万能插执行器接线", "引擎", "tools/verify_plug.py"),
    ("能力调用闭环（9 权威能力）", "引擎", "tools/verify_capability_loop.py"),
    ("本地模型可跑性探针", "模型", "tools/verify_local_models.py"),
    ("资产净化（贴图抹线）", "资产", "tools/verify_asset_clean.py"),
    ("影视生产线（DAG 六节点）", "创作", "tools/verify_film.py"),
    ("后期层（补光/调色/混音/质检）", "创作", "tools/verify_post.py"),
    ("本地创作链（旁白/配乐/字幕）", "创作", "tools/verify_studio.py"),
    ("港式僵尸三主题", "创作", "tools/verify_themes.py"),
    ("「细节化」习惯", "纪律", "tools/verify_detail_habit.py"),
    ("不开口闸（严禁要用户做事）", "纪律", "tools/verify_no_ask.py"),
    ("交付闸（每能力单独闭环）", "闸", "tools/verify_delivery_gate.py"),
    ("停机闸（只允许部署完成/真需用户操作）", "纪律", "tools/verify_stop_policy.py"),
    ("禁求助闸（不许求用户帮忙）", "纪律", "tools/verify_no_begging.py"),
    ("四觉闭环（视觉钉死·眼脑手验+耳嘴）", "感知", "tools/verify_senses_gate.py"),
    ("编队实时面板（一页看全谁在干活）", "面板", "tools/verify_fleet_live.py"),
    ("她的脑子（Agnes 云脑优先 + 本地兜底）", "智能", "tools/verify_brain_v9.py"),
    ("她五面插座（chat/code/hand/create/wisebase·我们模型驱动）", "插座", "tools/verify_sider_sockets.py"),
    ("每页代码双向绑定（无死角穿透）", "穿透", "tools/verify_file_binding.py"),
    ("模块闭环硬规定", "闸", "tools/verify_modular_closure.py"),
    ("模块式部署（蓝图/装/验）", "部署", "tools/verify_modular_deploy.py"),
)


def run_one(verifier: str, timeout: float = 900.0) -> dict:
    p = ROOT / verifier
    if not p.is_file():
        return {"通过": False, "结论": "验收器不存在（等于没闭环）", "退出码": None}
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=str(ROOT))
        out = (r.stdout or "") + (r.stderr or "")
        last = next((l for l in reversed(out.splitlines()) if l.strip()), "")
        return {"通过": r.returncode == 0 and "✅" in last, "结论": last.strip()[:160],
                "退出码": r.returncode, "ms": int((time.time() - t0) * 1000)}
    except subprocess.TimeoutExpired:
        return {"通过": False, "结论": "验收器超时（%.0fs）" % timeout, "退出码": None}


def audit(*, only: str = "", verbose: bool = True) -> dict:
    rows = []
    for name, cat, verifier in CAPABILITIES:
        if only and only not in name:
            continue
        r = run_one(verifier)
        rows.append({"能力": name, "类目": cat, "验收器": verifier, **r,
                     "可交付": bool(r["通过"])})
    ok = sum(1 for x in rows if x["可交付"])
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "能力数": len(rows), "可交付": ok,
           "禁止交付": len(rows) - ok, "明细": rows}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    if verbose:
        print("== 交付闸：每个能力必须单独跑通闭环 ==")
        for x in rows:
            print("  %s %-30s %-6s %s" % ("✅" if x["可交付"] else "❌", x["能力"],
                                          "可交付" if x["可交付"] else "禁止交付",
                                          (x["结论"] or "")[:70]))
        print("  ⇒ 可交付 %d / %d" % (ok, len(rows)))
    return rec


def gate(name: str) -> dict:
    """交付前问一句：这个能力放行吗？（没闭环一律不放行）"""
    hit = next((c for c in CAPABILITIES if name in c[0]), None)
    if not hit:
        return {"放行": False, "原因": "能力未登记（未登记=未闭环=不放行）"}
    r = run_one(hit[2])
    return {"放行": bool(r["通过"]), "能力": hit[0], "读数": r}


def status(limit: int = 3) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"登记能力数": len(CAPABILITIES), "最近": rows,
            "口径": "每个能力一条独立验收器；缺验收器=没闭环=禁止交付"}


__all__ = ["CAPABILITIES", "run_one", "audit", "gate", "status", "LEDGER"]
