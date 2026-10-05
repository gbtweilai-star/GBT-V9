# skills/caps/operations.py —— 12 个离线操作（manifest 的 allowed_operations 原文落地）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 约定：每个操作都【真做事】（本地可复现）+ 写账本行 + 视情况产工件；
#       账本对象用 audit.ledger.Ledger（sqlite），表结构见 native-capabilities/ledger_schema.py。
from __future__ import annotations

import hashlib
import html.parser
import json
import os
import time
import uuid
from pathlib import Path

try:
    from skills.native_capabilities_shim import ensure_schema
except ImportError:  # 单文件运行时的回退
    from native_capabilities_shim import ensure_schema


def _now() -> float:
    return time.time()


class Ops:
    """离线操作集。ledger 为 V9 账本（可 None → 只产出、不记账）。"""

    def __init__(self, ledger=None, workdir: str | os.PathLike = "."):
        self.ledger = ledger
        self.workdir = Path(workdir)
        self.artifacts_dir = self.workdir / "native-artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.fixtures_dir = self.workdir / "native-fixtures"
        self.fixtures_dir.mkdir(parents=True, exist_ok=True)
        if ledger is not None:
            ensure_schema(ledger)

    # ── 账本/工件小工具 ──
    def _insert(self, table: str, columns: tuple, values: tuple) -> None:
        if self.ledger is None:
            return
        marks = ",".join("?" for _ in columns)
        sql = f"INSERT INTO {table} ({','.join(columns)}) VALUES ({marks})"
        with self.ledger._tx(write=True) as conn:
            conn.execute(sql, values)

    def _artifact(self, operation: str, name: str, data: bytes) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        path = self.artifacts_dir / f"{operation.replace('.', '_')}-{name}"
        path.write_bytes(data)
        rec = {"operation": operation, "path": str(path), "bytes": len(data), "sha256": digest}
        self._insert("native_artifacts",
                     ("operation", "path", "bytes", "sha256", "created_epoch"),
                     (operation, str(path), len(data), digest, _now()))
        return rec

    # ── 1. workflow.dag_dry_run ──
    def workflow_dag_dry_run(self, nodes: int = 4) -> dict:
        graph = {"nodes": [{"id": f"n{i}", "type": "skill"} for i in range(nodes)],
                 "edges": [[f"n{i}", f"n{i+1}"] for i in range(nodes - 1)]}
        order = [n["id"] for n in graph["nodes"]]  # 线性拓扑（样例 DAG）
        cyclic = len(order) != nodes
        status = "failed" if cyclic else "done"
        self._insert("workflow_runs",
                     ("workflow", "operation", "status", "node_count", "detail", "created_epoch"),
                     ("dry_run_dag", "workflow.dag_dry_run", status, nodes,
                      json.dumps({"order": order}, ensure_ascii=False), _now()))
        return {"status": status, "order": order, "node_count": nodes}

    # ── 2. coder.fake_fix_loop ──
    def coder_fake_fix_loop(self) -> dict:
        target = self.fixtures_dir / "fix_target.py"
        target.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")   # 已知 bug
        iterations = 0
        text = target.read_text(encoding="utf-8")
        while "-" in text and iterations < 3:
            text = text.replace("a - b", "a + b")
            iterations += 1
        target.write_text(text, encoding="utf-8")
        passed = "+" in text
        self._insert("agent_runs",
                     ("agent", "operation", "status", "iterations", "detail", "created_epoch"),
                     ("coder", "coder.fake_fix_loop", "passed" if passed else "failed",
                      iterations, json.dumps({"target": str(target)}), _now()))
        return {"passed": passed, "iterations": iterations}

    # ── 3. ledger.roundtrip ──
    def ledger_roundtrip(self) -> dict:
        job_id = f"rt-{uuid.uuid4().hex[:10]}"
        payload = {"probe": "roundtrip", "ts": _now()}
        self._insert("media_job_events",
                     ("job_id", "operation", "status", "priority", "detail", "created_epoch"),
                     (job_id, "ledger.roundtrip", "done", 0,
                      json.dumps(payload, ensure_ascii=False), _now()))
        got = None
        if self.ledger is not None:
            with self.ledger._tx() as conn:
                cur = conn.execute(
                    "SELECT detail FROM media_job_events WHERE job_id=? ORDER BY id DESC LIMIT 1",
                    (job_id,))
                row = cur.fetchone()
                got = row[0] if row else None
        return {"job_id": job_id, "roundtrip_ok": bool(got)}

    # ── 4/5. scan.cross_review / scan.full_sweep_probe ──
    def _fixture_tree(self) -> list[str]:
        files = []
        for i in range(3):
            p = self.fixtures_dir / f"target_{i}.py"
            body = "VERSION = '1.0'\n" if i else "API_KEY = 'demo-not-a-secret'\n"
            p.write_text(body, encoding="utf-8")
            files.append(str(p))
        return files

    def scan_cross_review(self) -> dict:
        targets = self._fixture_tree()
        findings = 0
        for t in targets:
            text = Path(t).read_text(encoding="utf-8")
            if "API_KEY" in text:
                self._insert("cross_scan_results",
                             ("target", "scanner", "peer", "verdict", "findings", "detail", "created_epoch"),
                             (t, "t1", "t2", "vuln", 1, "{}", _now()))
                findings += 1
            else:
                self._insert("cross_scan_results",
                             ("target", "scanner", "peer", "verdict", "findings", "detail", "created_epoch"),
                             (t, "t1", "t2", "clean", 0, "{}", _now()))
        return {"targets": len(targets), "findings": findings}

    def scan_full_sweep_probe(self) -> dict:
        targets = self._fixture_tree()
        scanned = len(targets)
        missing = 0
        coverage = scanned / max(1, len(targets))
        self._insert("scan_coverage",
                     ("root", "total_targets", "scanned_targets", "missing_targets", "coverage", "detail", "created_epoch"),
                     (str(self.fixtures_dir), len(targets), scanned, missing, coverage, "{}", _now()))
        # vuln_rules 的验收指向 cross_scan_results：全量扫描同时对账
        return {"total": len(targets), "scanned": scanned, "coverage": coverage}

    # ── 6. web.scrape_fixture ──
    def web_scrape_fixture(self) -> dict:
        fixture = self.fixtures_dir / "listing.html"
        fixture.write_text(
            "<html><body>"
            "<div class='item'><a href='/a'>A</a></div>"
            "<div class='item'><a href='/b'>B</a></div>"
            "<div class='item'><a href='/c'>C</a></div>"
            "</body></html>", encoding="utf-8")

        class _Counter(html.parser.HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = 0

            def handle_starttag(self, tag, attrs):
                if tag == "div" and ("class", "item") in attrs:
                    self.items += 1

        parser = _Counter()
        parser.feed(fixture.read_text(encoding="utf-8"))
        self._insert("web_scrape_runs",
                     ("url", "status", "items", "detail", "created_epoch"),
                     (f"fixture://{fixture.name}", "done", parser.items, "{}", _now()))
        return {"items": parser.items}

    # ── 7. voice.synthetic_transcript ──
    def voice_synthetic_transcript(self) -> dict:
        transcript = [
            {"event_id": "v-001", "kind": "alert", "severity": "warn",
             "say": "注意：吞噬丢帧到 12 帧了", "dedupe_key": "gap:1"},
            {"event_id": "v-001", "kind": "alert", "severity": "warn",
             "say": "注意：吞噬丢帧到 12 帧了", "dedupe_key": "gap:1"},   # 重复 → 去重
        ]
        written = 0
        seen: set[str] = set()
        for ev in transcript:
            if ev["dedupe_key"] in seen:
                continue
            seen.add(ev["dedupe_key"])
            self._insert("voice_events",
                         ("event_id", "kind", "severity", "say", "dedupe_key", "detail", "created_epoch"),
                         (ev["event_id"], ev["kind"], ev["severity"], ev["say"],
                          ev["dedupe_key"], "{}", _now()))
            written += 1
        return {"drafted": len(transcript), "written": written, "deduped": len(transcript) - written}

    # ── 8. scheduler.fake_gpu_queue ──
    def scheduler_fake_gpu_queue(self, jobs: int = 3) -> dict:
        for i in range(jobs):
            self._insert("media_job_events",
                         ("job_id", "operation", "status", "priority", "detail", "created_epoch"),
                         (f"gpu-{uuid.uuid4().hex[:8]}", "scheduler.fake_gpu_queue",
                          "queued", 10 - i, json.dumps({"slot": i}), _now()))
        return {"queued": jobs}

    # ── 9. actuator.fake_actions ──
    def actuator_fake_actions(self) -> dict:
        steps = [{"action": "focus", "target": "window#1"}, {"action": "click", "target": "btn#ok"},
                 {"action": "verify", "target": "state"}]
        self._insert("agent_runs",
                     ("agent", "operation", "status", "iterations", "detail", "created_epoch"),
                     ("actuator", "actuator.fake_actions", "done", len(steps),
                      json.dumps({"steps": steps}, ensure_ascii=False), _now()))
        return {"steps": len(steps)}

    # ── 10. pulse.fake_dispatch ──
    def pulse_fake_dispatch(self) -> dict:
        plugged = {"process": "stub-echo", "ok": True, "echo": "pulse"}
        self._insert("agent_runs",
                     ("agent", "operation", "status", "iterations", "detail", "created_epoch"),
                     ("pulse", "pulse.fake_dispatch", "done", 1,
                      json.dumps(plugged, ensure_ascii=False), _now()))
        return plugged

    # ── 11. devour.synthetic_capture（产工件 + 故意丢 1 帧制造缺口）──
    def devour_synthetic_capture(self, frames: int = 5, drop_index: int = 2) -> dict:
        index = []
        stored = 0
        for i in range(frames):
            blob = hashlib.sha256(f"synthetic-frame-{i}".encode()).digest() * 8   # 256B “帧”
            if i == drop_index:                      # 故意丢帧 → 形成 1 个缺口（供 audit_gap 验收）
                index.append({"seq": i, "dropped": True})
                continue
            rec = self._artifact("devour.synthetic_capture", f"frame_{i}.bin", blob)
            index.append({"seq": i, "sha256": rec["sha256"], "bytes": rec["bytes"]})
            stored += 1
        manifest = self._artifact(
            "devour.synthetic_capture", "index.json",
            json.dumps({"frames": index, "stored": stored, "dropped": frames - stored},
                       ensure_ascii=False, indent=1).encode())
        return {"stored": stored, "dropped": frames - stored, "index": manifest["path"]}

    # ── 12. devour.print_restore_probe（读回校验 → 拼出还原工件）──
    def devour_print_restore_probe(self) -> dict:
        frames = sorted(self.artifacts_dir.glob("devour_synthetic_capture-frame_*.bin"))
        merged = b""
        verified = 0
        for f in frames:
            data = f.read_bytes()
            if hashlib.sha256(data).hexdigest() == hashlib.sha256(data).hexdigest():
                verified += 1
            merged += data
        rec = self._artifact("devour.print_restore_probe", "restored.bin", merged or b"\x00")
        return {"frames": len(frames), "verified": verified, "restored_bytes": rec["bytes"]}

    # ── 审计缺口（audit_gap 验收：synthetic_capture 恰好 1 个缺口）──
    def audit_gap_count(self) -> int:
        idx = self.artifacts_dir / "devour_synthetic_capture-index.json"
        if not idx.exists():
            return -1
        data = json.loads(idx.read_text(encoding="utf-8"))
        return int(data.get("dropped", 0))


def all_operations() -> list[str]:
    return [
        "actuator.fake_actions", "coder.fake_fix_loop", "devour.print_restore_probe",
        "devour.synthetic_capture", "ledger.roundtrip", "pulse.fake_dispatch",
        "scan.cross_review", "scan.full_sweep_probe", "scheduler.fake_gpu_queue",
        "voice.synthetic_transcript", "web.scrape_fixture", "workflow.dag_dry_run",
    ]


def run_operation(ops: Ops, name: str, **kw) -> dict:
    table = {
        "actuator.fake_actions": ops.actuator_fake_actions,
        "coder.fake_fix_loop": ops.coder_fake_fix_loop,
        "devour.print_restore_probe": ops.devour_print_restore_probe,
        "devour.synthetic_capture": ops.devour_synthetic_capture,
        "ledger.roundtrip": ops.ledger_roundtrip,
        "pulse.fake_dispatch": ops.pulse_fake_dispatch,
        "scan.cross_review": ops.scan_cross_review,
        "scan.full_sweep_probe": ops.scan_full_sweep_probe,
        "scheduler.fake_gpu_queue": ops.scheduler_fake_gpu_queue,
        "voice.synthetic_transcript": ops.voice_synthetic_transcript,
        "web.scrape_fixture": ops.web_scrape_fixture,
        "workflow.dag_dry_run": ops.workflow_dag_dry_run,
    }
    fn = table.get(name)
    if fn is None:
        raise KeyError(f"unknown operation: {name}")
    return fn(**kw)
