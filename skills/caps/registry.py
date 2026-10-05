# skills/caps/registry.py —— 53 项离线能力注册表工厂（对齐 octop.offline.manifest.json）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
from __future__ import annotations

import json
from pathlib import Path

from skills.caps import domains_a as A
from skills.caps import domains_b as B
from skills.caps.base import CapRegistry

MANIFEST = Path(__file__).resolve().parent.parent.parent / "native-capabilities" / "octop.offline.manifest.json"

_CLASSES = (
    # brain
    A.IntentSplit, A.LlmRetry, A.RouteLaya, A.ReflectLoop, A.PromptFactory, A.ReportUp, A.MemoryView,
    # tentacle
    A.TentacleContract, A.HookFilter, A.PerLlm, A.Unbounded, A.InheritAll, A.TentacleIdentity,
    # scan
    A.ScanFullSweep, A.ScanCrossReview, A.ScanVulnRules, A.ScanCoverageReport,
    # devour
    A.DevourZeroDrop, A.DevourPrintRestore, A.DevourSegmentPack, A.DevourR2Watermark,
    A.DevourPlayback, A.DevourCacheReclaim, A.DevourGapAlert, A.DevourGapCompensate, A.DevourCompDrawer,
    # voice
    B.VoiceMicStream, B.VoiceKeyword, B.VoiceQueueDedupe, B.VoiceTts,
    # exec
    B.ExecCoder, B.ExecActuator, B.ExecPulse,
    # flow
    B.FlowDagEngine, B.FlowDagEditor, B.FlowRiskGate,
    # ledger
    B.LedgerDualBackend, B.LedgerPoolMonitor, B.LedgerAlerts, B.LedgerAutoScale,
    B.LedgerThreadSafe, B.LedgerAuditGap,
    # sched
    B.SchedQueueGpu, B.SchedMonitor, B.SchedFailRate,
    # panel
    B.PanelOverview, B.PanelDigitalHuman, B.PanelNavStack, B.PanelTrends,
    # auth
    B.AuthSessionTristate, B.AuthUserIsolated, B.AuthSharedKeyIsolated,
    # web
    B.WebScrape,
)


def build_caps_registry(ledger=None, workdir: str = ".") -> CapRegistry:
    reg = CapRegistry(ledger=ledger, workdir=workdir)
    for cls in _CLASSES:
        reg.register(cls())
    return reg


def manifest_ids() -> list[str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    return [c["id"] for c in data["capabilities"]]


def coverage_report(reg: CapRegistry) -> dict:
    """注册表 vs 清单：缺了谁、多了谁。"""
    want = manifest_ids()
    have = list(reg.caps.keys())
    return {"manifest": len(want), "registered": len(have),
            "missing": sorted(set(want) - set(have)),
            "extra": sorted(set(have) - set(want))}


__all__ = ["build_caps_registry", "manifest_ids", "coverage_report", "MANIFEST"]
