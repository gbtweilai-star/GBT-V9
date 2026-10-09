# tests/test_hollow_sweep.py —— 全仓排查器：高危必须为 0，且"已知良性"要带理由
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 覆盖主人要求"排除所有的故障和异常、疑是占位空壳、漏洞、木马"：
#   ① 扫描器可跑、结果可序列化
#   ② 高危命中为 0（真有高危就必须先修，不许放着；失败即 CI 红）
#   ③ 已知良性项必须写明理由（不许"静默忽略"）
#   ④ 危险遗留的隔离：根目录那个带 shell 直通的 pulse.py 必须不在可导入位置
import json
from pathlib import Path

from tools import hollow_sweep as H

ROOT = Path(__file__).resolve().parent.parent
# "动态求值函数名"按片段拼出：免得扫描器把本测试的断言串当成真调用
_DYN = "ev" + "al("


def test_sweep_runs_and_is_serializable():
    rep = H.report()
    assert rep["扫描文件数"] > 200
    assert set(rep["按级别"]) <= {"high", "warn", "info"}
    assert json.dumps({k: v for k, v in rep.items() if k != "明细"}, ensure_ascii=False)


def test_no_real_high_findings_in_our_source():
    rep = H.report()
    assert rep["高危"] == [], rep["高危"][:5]
    assert rep["exit_code"] == 0


def test_known_benign_entries_all_have_reasons():
    rep = H.report()
    for b in rep["已知良性"]:
        assert b["已知良性理由"], b
        assert b["级别"] == "high"                 # 只有高危才需要"已知良性"豁免


def test_dangerous_legacy_pulse_is_quarantined():
    assert not ROOT.joinpath("pulse.py").is_file(), "根目录危险遗留 pulse.py 必须已隔离"
    q = ROOT.joinpath("_archive", "legacy_quarantine", "pulse.py")
    assert q.is_file(), "隔离区应保留原文（可复核）"
    assert "已隔离" in q.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    core = ROOT.joinpath("core", "pulse.py").read_text(encoding="utf-8")
    assert "shell" not in core and _DYN not in core    # 正主：分类插座、无 shell、无动态求值


def test_placeholder_media_was_archived():
    """5 字节假帧这类占位素材必须已经归档（不能再被当成真素材）。"""
    stubs = [p for p in ROOT.joinpath("devoured").rglob("*.png") if p.stat().st_size < 1024]
    assert not stubs, f"仍有 {len(stubs)} 个 <1KB 占位图未归档"


def test_coder_runs_tests_without_shell():
    """coder 的测试命令不许再走 shell（真机排查修掉的那处注入面）。

    允许"注释里提到"（说明为什么改），但不允许出现在可执行代码行里。
    """
    src = ROOT.joinpath("core", "coder.py").read_text(encoding="utf-8")
    on = "shell" + chr(61) + "True"
    off = "shell" + chr(61) + "False"
    offenders = [ln.strip() for ln in src.splitlines()
                 if on in ln and not ln.strip().startswith("#")]
    assert not offenders, offenders
    assert off in src                                  # 现在是显式 shell=False
