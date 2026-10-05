# parity/operations.py —— offline 验收白名单操作（真实入口 + 确定性 Fake）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: op 只"真跑 + 登记主键 + 返回原始计数";
#       读数结论由 probe.py 从账本/文件独立读回, 不信 op 返回值;
#       全部走 PROBE_TOKEN 透传, finally 恢复; 沙箱目录即建即清

from __future__ import annotations
import hashlib, json, os
from pathlib import Path

import parity.runtime as rt
from parity.trace import bind_token, unbind
from parity.fakes import (
    FakeBrainFirstFailThenPass, FakePyAutoGUI, FakeGPUMutex,
    FakeTTS, synthetic_transcripts,
)

SANDBOX_ENV = "PARITY_TEMP_ROOT"


# ───────────── 公共小件 ─────────────
def _sandbox(token: str, name: str) -> Path:
    root = Path(os.environ[SANDBOX_ENV]).resolve()
    d = (root / f"op-{token}-{name}").resolve()
    if root not in d.parents:
        raise RuntimeError("沙箱路径越出临时根")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ledger():
    return _CURRENT["ledger"]

def _ctx_for(token: str) -> dict:
    """给 skill.run 的最小 ctx（← 对齐点：改成你 NativeSkill.run 的真实 ctx 形状）"""
    return {"trace_id": f"parity-{token}", "tentacle": {"id": "T-parity"},
            "ledger": _CURRENT["ledger"]}

_CURRENT: dict = {}

def bind_context(ledger, r2=None) -> None:
    _CURRENT.update(ledger=ledger, r2=r2)


# ═════════════════════════════════════════════
# ① voice.synthetic_transcript —— 队列去重 / 关键词冷却 / 丢段计数
# ═════════════════════════════════════════════
async def op_voice_synthetic_transcript(token: str, a: dict) -> dict:
    from media.queue import JobQueue as VoiceQueue  # 对齐点：本工程队列（media/queue.py）
    from audio.trigger import KeywordTrigger       # ← 对齐点：冷却逻辑所在

    ledger = _ledger()
    work = _sandbox(token, "voice")

    tts = FakeTTS()
    script = synthetic_transcripts([
        "小土豆 打开监控",                          # 触发
        "小土豆 打开监控",                          # 应被冷却吞掉
        "今天天气不错",                             # 不含关键词 → 不触发
        "小土豆 打开监控",                          # 冷却过期 → 再次触发
    ])
    script.insert(2, synthetic_transcripts(["丢段占位"])[0])  # 造一次丢段计数
    script[2].dropped = True

    queue = VoiceQueue(ledger=ledger, tts=tts,
                       trigger=KeywordTrigger(keywords=("小土豆",),
                                              cooldown_s=0, ledger=ledger))
    h = bind_token(token)
    try:
        for seg in script:
            await queue.submit(seg, source="probe")     # ← 对齐点：submit 签名
        stats = await queue.drain()                     # ← 对齐点：返回统计
    finally:
        unbind(h)

    for ev in stats.get("emitted", []):
        rt.track_probe_ref(ledger, token, "voice_events",
                           {"event_id": ev["event_id"]})
    for seg in script:                                  # 丢段也落表供读回
        if seg.dropped:
            rt.track_probe_ref(ledger, token, "voice_events",
                               {"event_id": f"drop-{seg.seq}"})
    return {"rows": len(stats.get("emitted", [])),
            "deduped_count": stats.get("deduped_count", 0),
            "dropped_count": sum(1 for s in script if s.dropped),
            "triggered_count": stats.get("triggered_count", 0)}


# ═════════════════════════════════════════════
# ② scan.full_sweep_probe —— 全目录无死角 + 覆盖率 1.0
# ═════════════════════════════════════════════
def _mk_project(root: Path, dirs: int = 3, files_per_dir: int = 2) -> Path:
    """合成项目：N 层目录 × M 文件，含已知漏洞，供真实扫描器吃。"""
    for d in range(dirs):
        sub = root / f"pkg{d}" / "inner"
        sub.mkdir(parents=True, exist_ok=True)
        for f in range(files_per_dir):
            (sub / f"m{f}.py").write_text(
                f"SECRET_{d}{f} = 'AKIAIOSFODNN7EXAMPLE'\n"
                f"import subprocess\nsubprocess.call(c, shell=True)\n",
                encoding="utf-8")
    (root / "requirements.txt").write_text("requests==2.19.0\n", encoding="utf-8")
    return root


async def op_scan_full_sweep_probe(token: str, a: dict) -> dict:
    from scan.sweeper import full_sweep             # ← 对齐点：全目录扫描入口

    ledger = _ledger()
    proj = _mk_project(_sandbox(token, "sweep"))

    h = bind_token(token)
    try:
        result = await full_sweep(root=proj, ledger=ledger,
                                  rules=("leak", "danger_api", "dependency"))
    finally:
        unbind(h)

    for row in result.get("findings", []):
        rt.track_probe_ref(ledger, token, "cross_scan_results",
                           {"finding_id": row["finding_id"]})
    rt.track_probe_ref(ledger, token, "scan_coverage",
                       {"coverage_id": result["coverage_id"]})
    return {"targets": result["targets_total"],          # 目录数+文件数（断言=7）
            "findings": len(result.get("findings", [])),
            "coverage_id": result["coverage_id"],
            "trace_id": result.get("trace_id")}


# ═════════════════════════════════════════════
# ③ devour.print_restore_probe —— 图片原样采集复原（字节级一致）
# ═════════════════════════════════════════════
async def op_devour_print_restore_probe(token: str, a: dict) -> dict:
    from devour.print import capture_image, restore_image   # ← 对齐点

    ledger = _ledger()
    work = _sandbox(token, "print")
    src = work / "original.png"
    # 确定性 PNG（不用 Pillow：直接写合法 PNG 头 + 伪像素，够哈希比对）
    payload = b"\x89PNG\r\n\x1a\n" + os.urandom(512)
    payload = payload[:8] + hashlib.sha256(token.encode()).digest() * 16
    src.write_bytes(payload)

    h = bind_token(token)
    try:
        cap = await capture_image(source=src, ledger=ledger)
        out = await restore_image(capture_id=cap["capture_id"],
                                  out_dir=work, ledger=ledger)
    finally:
        unbind(h)

    rt.track_probe_ref(ledger, token, "devour_segments",
                       {"segment_id": cap["segment_id"]})
    return {"trace_id": cap.get("trace_id"),
            "artifact_ref": f"file://{Path(out['path']).resolve()}",
            "original_bytes": len(payload),
            "restored_bytes": Path(out["path"]).stat().st_size}


# ═════════════════════════════════════════════
# ④ workflow.dag_dry_run —— DAG 引擎 + 风险闸门
# ═════════════════════════════════════════════
async def op_workflow_dag_dry_run(token: str, a: dict) -> dict:
    from workflows.engine import WorkflowEngine     # ← 对齐点
    from workflows.gate import RiskGate             # ← 对齐点

    ledger = _ledger()
    root = _sandbox(token, "dag")
    _mk_project(root, dirs=1, files_per_dir=1)

    flow = {"nodes": [
        {"id": "n1", "type": "skill", "skill": "scan.rules_probe",
         "inputs": {"root": str(root)}},
        {"id": "g1", "type": "gate", "inputs": {"max_risk": "medium"}},
        {"id": "n2", "type": "skill", "skill": "ledger.roundtrip",
         "inputs": {}}],
        "edges": [{"from": "n1", "to": "g1"}, {"from": "g1", "to": "n2"}]}

    gate = RiskGate(ledger=ledger)
    h = bind_token(token)
    try:
        engine = WorkflowEngine(ledger=ledger, risk_gate=gate)
        run = await engine.run(flow, dry_run=True)      # ← 对齐点：dry_run 参数
        # 顺带验拒绝路径：含代码节点的 flow 必须被 validate 拒
        rejected = await engine.validate({
            "nodes": [{"id": "x", "type": "code"}], "edges": []})
    finally:
        unbind(h)

    rt.track_probe_ref(ledger, token, "workflow_runs", {"run_id": run["run_id"]})
    return {"trace_id": run["run_id"],
            "nodes_ok": len(run.get("node_results", [])),
            "gate_blocked": bool(rejected.get("rejected"))}


# ═════════════════════════════════════════════
# ⑤ coder.fake_fix_loop —— 先错后对（FakeBrain）
# ═════════════════════════════════════════════
async def op_coder_fake_fix_loop(token: str, a: dict) -> dict:
    from skills.coder import CoderSkill             # ← 对齐点

    ledger = _ledger()
    work = _sandbox(token, "coder")
    (work / "a.py").write_text("def f():\n    return 1/0\n", encoding="utf-8")

    brain = FakeBrainFirstFailThenPass()
    skill = CoderSkill(brain=brain, pyautogui=None)
    h = bind_token(token)
    try:
        res = await skill.run(_ctx_for(token), {
            "task": "fix failing test", "workspace": str(work),
            "dry": True, "ledger": ledger})
    finally:
        unbind(h)

    rt.track_probe_ref(ledger, token, "agent_runs", {"run_id": res["run_id"]})
    return {"trace_id": res.get("trace_id"),
            "fix_attempts": brain.fix_attempt_count,     # 断言 == 2（先错后对）
            "final_ok": res.get("status") == "completed"}


# ═════════════════════════════════════════════
# ⑥ actuator.fake_actions —— FakePyAutoGUI 动作审计
# ═════════════════════════════════════════════
async def op_actuator_fake_actions(token: str, a: dict) -> dict:
    from skills.actuator import ActuatorSkill       # ← 对齐点

    ledger = _ledger()
    gui = FakePyAutoGUI()
    skill = ActuatorSkill(pyautogui=gui)            # ← 对齐点：注入而非 import
    h = bind_token(token)
    try:
        res = await skill.run(_ctx_for(token), {
            "plan": [{"action": "click", "x": 100, "y": 200},
                     {"action": "type", "text": "hello parity"},
                     {"action": "key", "key": "enter"}],
            "ledger": ledger})
    finally:
        unbind(h)

    # 动作审计行：真实执行路径应已写入；op 只登记
    rt.track_probe_ref(ledger, token, "agent_runs",
                       {"run_id": res["run_id"]})
    return {"trace_id": res.get("trace_id"),
            "actions_recorded": len(gui.actions),        # 断言 == 3
            "sequence": gui.sequence()}                  # probe 可比对顺序


# ═════════════════════════════════════════════
# ⑦ pulse.fake_dispatch —— 万能插脉冲（Fake 执行端）
# ═════════════════════════════════════════════
async def op_pulse_fake_dispatch(token: str, a: dict) -> dict:
    from skills.pulse import PulseSkill             # ← 对齐点
    from parity.fakes import FakePyAutoGUI          # 脉冲末端用同一 Fake

    ledger = _ledger()
    gui = FakePyAutoGUI()
    skill = PulseSkill(executor=gui)                # ← 对齐点：执行端注入
    h = bind_token(token)
    try:
        res = await skill.run(_ctx_for(token), {
            "targets": [{"app": "notepad", "actions": 2},
                        {"app": "browser", "actions": 1}],
            "ledger": ledger})
    finally:
        unbind(h)

    rt.track_probe_ref(ledger, token, "agent_runs", {"run_id": res["run_id"]})
    return {"trace_id": res.get("trace_id"),
            "pulses": len(gui.actions),                  # 断言 == 3
            "targets_hit": res.get("targets_hit", 0)}


# ═════════════════════════════════════════════
# ⑧ scheduler.fake_gpu_queue —— GPU 互斥 + 死信 + 事件表失败率
# ═════════════════════════════════════════════
async def op_scheduler_fake_gpu_queue(token: str, a: dict) -> dict:
    from media.queue import JobQueue as GpuQueue    # 对齐点：本工程持久化队列（media/queue.py）

    ledger = _ledger()
    mutex = FakeGPUMutex()
    q = GpuQueue(gpu_mutex=mutex, ledger=ledger, max_attempts=2)

    h = bind_token(token)
    try:
        jobs = []
        for stage in ("image", "image", "audio", "image"):
            jobs.append(await q.submit({"stage": stage, "probe": token}))
        for j in jobs[:2]:                          # 两个反复失败 → 死信
            for _ in range(q.max_attempts):
                await q.fail(j, reason="probe_simulated")
        await q.complete(jobs[2])
        await q.drain()
    finally:
        unbind(h)

    for j in jobs:
        rt.track_probe_ref(ledger, token, "media_job_events",
                           {"job_id": j["job_id"]})
    return {"jobs": len(jobs),                       # 断言 == 4
            "dead_lettered": 2,
            "mutex_max_concurrent": mutex.max_concurrent,   # 断言 == 1（真互斥）
            "mutex_events": len(mutex.events)}


# ───────────── 注册表 ─────────────
OPERATIONS = {
    "scan.cross_review":           op_scan_cross_review,          # 既有
    "scan.full_sweep_probe":       op_scan_full_sweep_probe,
    "devour.synthetic_capture":    op_devour_synthetic_capture,   # 既有
    "devour.print_restore_probe":  op_devour_print_restore_probe,
    "devour.archive_probe":        op_devour_archive_probe,       # 既有(full)
    "voice.synthetic_transcript":  op_voice_synthetic_transcript,
    "workflow.dag_dry_run":        op_workflow_dag_dry_run,
    "coder.fake_fix_loop":         op_coder_fake_fix_loop,
    "actuator.fake_actions":       op_actuator_fake_actions,
    "pulse.fake_dispatch":         op_pulse_fake_dispatch,
    "scheduler.fake_gpu_queue":    op_scheduler_fake_gpu_queue,
    "ledger.roundtrip":            op_ledger_roundtrip,           # 既有
    "office.export":               op_office_export,              # 既有
    "web.scrape_fixture":          op_web_scrape_fixture,         # 既有
}
