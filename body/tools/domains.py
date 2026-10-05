# body/tools/domains.py —— 数字人可问询的四个只读域工具
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 与见证工具同一套模式 —— 只读快照、不重算、stale 即"无法确认"；
#       数字一律来自 body_read_snapshots / witness_snapshot，工具内不做聚合猜测。
from __future__ import annotations
import json
import os
import time

from body.tools.base import (ReadTool, ToolResult, register, unknown_sentence,
                             _age_text)


def _parse_iso(value) -> float:
    from common.timeutil import to_epoch
    try:
        return float(to_epoch(value))
    except Exception:
        return 0.0


# ═══════════ ① 吞噬能（采到多少帧 / 有没有断点 / 归档了几段）═══════════
@register
class DevourTool(ReadTool):
    name = "media.capture"
    domain = "devour"
    description = "读取吞噬能采集现状：已存帧数、时间跨度、断点、已封装帧段数"
    intent_hint = "采集/录像/帧段/丢帧/断点/归档/缓存"
    params = {}

    def sentence(self, facts: dict) -> str:
        if facts.get("coverage") != "observed":
            return unknown_sentence("吞噬能采样不可用")
        seg = facts.get("segments")
        seg_text = f"已封装 {seg} 个帧段" if seg is not None else "帧段数暂不可知"
        tail = (f"检测到 {facts['gaps']} 个断点（最近序列 {facts['gap_seqs']}）"
                if facts.get("gaps") else "没有断点")
        return (f"吞噬能已存 {facts.get('frames', 0)} 帧，时间跨度 {facts.get('span', 0)}，"
                f"{seg_text}；{tail}。")


# ═══════════ ② 扫描覆盖（登记链页数 / 索引状态 / 触手覆盖）═══════════
@register
class ScanCoverageTool(ReadTool):
    name = "scan.coverage"
    domain = "scan"
    description = "读取扫描覆盖：登记链页数、事件分布、文件索引状态、覆盖触手数"
    intent_hint = "扫描/覆盖/漏扫/漏洞/触手覆盖/责任页"
    params = {"tentacle_id": {"type": "string",
                              "description": "可选：只看某个触手负责的页数"}}

    def sentence(self, facts: dict) -> str:
        if facts.get("coverage") != "observed":
            return unknown_sentence("扫描覆盖采样不可用")
        idx = facts.get("index") or {}
        return (f"登记链共 {facts.get('total', 0)} 页，链头第 {facts.get('head_seq', 0)} 页；"
                f"其中扫描 {facts.get('n_scan', 0)}、变更 {facts.get('n_change', 0)}、"
                f"修复 {facts.get('n_fix', 0)}、加固 {facts.get('n_harden', 0)}。"
                f"文件索引：干净 {idx.get('clean', 0)}、脏 {idx.get('dirty', 0)}、"
                f"缺失 {idx.get('missing', 0)}。"
                f"共 {facts.get('tentacles', 0)} 个触手有覆盖记录。")

    async def run(self, ledger, *, params=None, now_fn=time.time, ttl_mult=3):
        """基类取数 + 可选单触手下钻；无状态，绝不在类上挂调用期数据。"""
        res = await super().run(ledger, now_fn=now_fn, ttl_mult=ttl_mult)
        want = (params or {}).get("tentacle_id")
        if want and not res.stale:
            pages = (res.facts.get("per_tentacle") or {}).get(want)
            res.safe_sentence += (f"触手 {want} 负责 {pages} 页。" if pages is not None
                                  else f"触手 {want} 没有覆盖记录。")
        return res


# ═══════════ ③ 队列状态（深度 / 等待 / 失败率 / 显存）═══════════
@register
class QueueTool(ReadTool):
    name = "media.queue"
    domain = "queue"
    description = "读取生成队列状态：深度、等待、失败率、显存"
    intent_hint = "队列/等待/失败率/死信/显存/排队"
    params = {}

    def sentence(self, facts: dict) -> str:
        if facts.get("coverage") != "observed":
            return unknown_sentence("队列采样不可用")
        d = facts.get("depth") or {}
        w = facts.get("wait") or {}
        fail = (f"窗口内失败 {facts['failed_count']} 次、成功 {facts['success_count']} 次，"
                f"失败率 {facts['failure_rate']}"
                if facts.get("failure_rate_kind") == "observed" else "窗口内没有完成尝试，失败率无法计算")
        vram = facts.get("vram") or {}
        vtext = (f"显存 {vram['used_mb']}/{vram['total_mb']} 兆"
                 if vram.get("available") else "显存读数不可用")
        return (f"队列：排队 {d.get('queued', 0)}、运行 {d.get('running', 0)}、"
                f"死信 {d.get('dead', 0)}；最久可执行已等 {w.get('oldest_runnable_age', 0)} 秒，"
                f"退避 {w.get('backoff_count', 0)} 个。{fail}；{vtext}。")


# ═══════════ ④ 见证（有效票 / 证据等级 / 掉票原因）═══════════
@register
class WitnessTool(ReadTool):
    name = "get_witness_snapshot"
    domain = "witness"
    description = "读取当前见证快照：有效见证数、要求数、每个见证的身份状态与证据等级"
    intent_hint = "见证/有效票/在岗/证据等级/掉票"
    params = {"witness_id": {"type": "string", "description": "可选：只看某个见证"}}

    def sentence(self, facts: dict) -> str:
        from body.witness_voice import render_witness_sentence
        return render_witness_sentence(facts, status=facts.get("status"))

    async def run(self, ledger, *, params=None, now_fn=time.time,
                  ttl_mult=3) -> ToolResult:
        """见证域的唯一读取路径：读 witness_snapshot 单行，不重算、不触发探测。"""
        snap = await ledger.fetch_one("SELECT * FROM witness_snapshot WHERE id=1")
        if snap is None:
            return ToolResult(tool=self.name, domain=self.domain,
                              unknown_reason="no_snapshot",
                              safe_sentence=unknown_sentence("见证还没有任何快照"))
        ttl = int(os.getenv("BODY_WITNESS_PROBE_TTL", 60)) * ttl_mult
        age = max(0.0, now_fn() - _parse_iso(snap["observed_at"]))
        stale = age > ttl
        states = json.loads(snap["states_json"] or "{}")
        want = (params or {}).get("witness_id")
        if want:
            states = {k: v for k, v in states.items() if k == want}
        facts = {"valid_count": snap["valid_count"], "required": snap["required"],
                 "status": snap["status"], "states": states,
                 "coverage": "stale" if stale else "observed"}
        sentence = (unknown_sentence("见证读数已超出有效期", _age_text(int(age)))
                    if stale else self.sentence(facts))
        ev = [{"witness_id": w, "evidence_level": s.get("evidence_level"),
               "identity_status": s.get("identity_status"),
               "reason_code": s.get("reason_code")} for w, s in sorted(states.items())]
        return ToolResult(tool=self.name, domain=self.domain, revision=snap["revision"],
                          observed_at=snap["observed_at"], stale=stale, facts=facts,
                          evidence_ref=ev,
                          unknown_reason=("stale" if stale else None),
                          safe_sentence=sentence)


__all__ = ["DevourTool", "ScanCoverageTool", "QueueTool", "WitnessTool"]
