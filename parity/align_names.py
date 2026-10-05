# parity/align_names.py —— manifest 目标 ↔ 注册表 对齐审计
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 纪律: 只出报告, 绝不自动改写 manifest; 名不对必须人工决定 rename/bind/adapter/new

from __future__ import annotations
import argparse, importlib, json, sys
from pathlib import Path

ACTIONS = {"rename", "bind", "adapter", "new"}


def load_registry_snapshot(path: Path) -> dict:
    """由 `python -m parity.snapshot_registry > registry.json` 生成（无副作用）。"""
    return json.loads(path.read_text(encoding="utf-8"))


def check_skill(target: dict, snap: dict) -> dict:
    name = target["name"]
    info = snap["skills"].get(name)
    if info is None:
        return {"status": "missing_skill", "name": name,
                "suggest": "rename|bind|adapter|new",
                "near": _near(name, snap["skills"])}
    if target.get("version") and info.get("version") != target["version"]:
        return {"status": "spec_mismatch", "name": name,
                "detail": f"version {info.get('version')} != {target['version']}"}
    if not info.get("has_spec") or not info.get("has_probe"):
        return {"status": "spec_mismatch", "name": name,
                "detail": "缺 spec() 或 probe()"}
    return {"status": "aligned", "name": name, "version": info.get("version")}


def check_subsystem(target: dict, snap: dict) -> dict:
    name = target["name"]
    info = snap["subsystems"].get(name)
    if info is None:
        return {"status": "missing_subsystem", "name": name,
                "suggest": "bind|adapter|new",
                "near": _near(name, snap["subsystems"])}
    if not info.get("probe_registered"):
        return {"status": "missing_probe", "name": name,
                "detail": "模块存在但未在 SubsystemRegistry 登记 probe"}
    if not info.get("importable"):
        return {"status": "import_error", "name": name,
                "detail": info.get("error", "")}
    return {"status": "aligned", "name": name}


def check_api(target: dict, snap: dict) -> dict:
    name = target["name"]
    if name not in snap.get("routes", []):
        return {"status": "missing_route", "name": name,
                "near": _near(name, snap.get("routes", []))}
    return {"status": "aligned", "name": name}


def _near(name: str, pool) -> list[str]:
    """给候选人，方便人工 rename：同前缀得分最高。"""
    prefix = name.rsplit(".", 1)[0] if "." in name else name
    scored = []
    for cand in pool:
        score = 0
        if cand.startswith(prefix): score += 3
        if cand.split(".")[0] == name.split(".")[0]: score += 2
        if cand.split(".")[-1] == name.split(".")[-1]: score += 2
        if score: scored.append((score, cand))
    return [c for _, c in sorted(scored, reverse=True)[:4]]


def align(manifest: dict, snap: dict) -> list[dict]:
    out = []
    for cap in manifest["capabilities"]:
        tgt = cap.get("target") or {"kind": "skill", "name": cap.get("native_skill", "")}
        kind = tgt.get("kind")
        if kind == "skill":
            res = check_skill(tgt, snap)
        elif kind == "subsystem":
            res = check_subsystem(tgt, snap)
        elif kind == "api":
            res = check_api(tgt, snap)
        else:
            res = {"status": "unknown_kind", "name": tgt.get("name", "?"),
                   "detail": kind}
        res.update({"capability_id": cap["id"], "target_kind": kind,
                    "required": cap.get("required", True),
                    "equivalence": cap.get("equivalence", "unverified")})
        out.append(res)
    return out


def render_md(rows: list[dict]) -> str:
    ok  = [r for r in rows if r["status"] == "aligned"]
    bad = [r for r in rows if r["status"] != "aligned"]
    L = ["# native_skill 对齐审计", "",
         f"- 总项 {len(rows)}｜已对齐 {len(ok)}｜待处理 {len(bad)}", "",
         "## 待处理", "",
         "| 能力 | kind | 目标名 | 状态 | 候选/说明 | 建议动作 |", "|---|---|---|---|---|---|"]
    for r in sorted(bad, key=lambda x: (x["target_kind"], x["status"])):
        cand = ", ".join(r.get("near", [])) or r.get("detail", "")
        L.append(f"| `{r['capability_id']}` | {r['target_kind']} | `{r['name']}` "
                 f"| **{r['status']}** | {cand} | {r.get('suggest','人工判定')} |")
    L += ["", "## 已对齐", ""]
    for r in ok:
        L.append(f"- `{r['capability_id']}` → `{r['name']}` ({r['target_kind']})")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--registry", required=True)
    ap.add_argument("--out-dir", default="parity-reports")
    a = ap.parse_args()

    manifest = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    snap = load_registry_snapshot(Path(a.registry))
    rows = align(manifest, snap)

    out = Path(a.out_dir); out.mkdir(parents=True, exist_ok=True)
    (out / "align.json").write_text(
        json.dumps({"rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "align.md").write_text(render_md(rows), encoding="utf-8")

    bad_required = [r for r in rows if r["status"] != "aligned" and r["required"]]
    print(f"[align] 对齐 {len(rows) - len([r for r in rows if r['status']!='aligned'])}"
          f"/{len(rows)}；必需未对齐 {len(bad_required)}")
    print(f"[align] 报告: {out / 'align.md'}")
    return 1 if bad_required else 0


if __name__ == "__main__":
    raise SystemExit(main())
