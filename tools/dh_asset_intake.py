# tools/dh_asset_intake.py —— 数字人 3D 资产接入（把踩过的那次操作固化成可复跑脚本）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 固化来源：主人 2026-10-09「调用触手接管一次做个固化，确保下次重启大模型不会又不懂」。
# 用法：python tools/dh_asset_intake.py             # 扫 → 验 → 收进仓 → 报下一步
#       python tools/dh_asset_intake.py --scan      # 只看候选（不写盘）
import json
import shutil
import struct
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "state" / "tripo" / "out3"
SEARCH = (Path.home() / "Downloads", DEST)
EXT = (".glb", ".gltf")


def probe(p: Path) -> dict:
    """解析 GLB 头里的 JSON：顶点/骨架/骨头/动画 —— 全是**真读数**，不是猜。"""
    raw = p.read_bytes()
    if raw[:4] != b"glTF":
        return {"错误": "不是 GLB"}
    jl, = struct.unpack_from("<I", raw, 12)
    J = json.loads(raw[20:20 + jl].decode("utf-8"))
    verts = 0
    for m in J.get("meshes", []):
        for pr in m.get("primitives", []):
            verts += int(J["accessors"][pr["attributes"]["POSITION"]].get("count") or 0)
    skins = J.get("skins", [])
    names = [n.get("name") or "" for n in J.get("nodes", [])]
    return {"顶点": verts, "骨架": len(skins),
            "骨头": sum(len(s.get("joints") or []) for s in skins),
            "动画": len(J.get("animations", [])),
            "动画名": [a.get("name") or "（无名）" for a in J.get("animations", [])][:8],
            "骨骼命名样例": [n for n in names if "mixamorig" in n.lower() or "bone" in n.lower()][:4],
            "MB": round(len(raw) / 1048576, 1)}


def candidates() -> list:
    out = []
    for d in SEARCH:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*")):
            if f.suffix.lower() in EXT and f.is_file():
                out.append(f)
    return out


def main() -> int:
    scan_only = "--scan" in sys.argv
    cands = candidates()
    print("== 数字人 3D 资产接入（SOP: dh_asset_intake）==")
    print("  扫到候选 %d 个（下载夹 + state/tripo/out3）" % len(cands))
    rows = []
    DEST.mkdir(parents=True, exist_ok=True)
    for f in cands:
        try:
            info = probe(f)
        except Exception as exc:                              # noqa: BLE001
            info = {"错误": type(exc).__name__}
        row = {"原件": f.name, "来源": str(f.parent), **info}
        if not info.get("错误"):
            rigged = info["骨架"] > 0 and info["骨头"] > 0
            row["有骨架"] = rigged
            if rigged and info["动画"] > 0:
                row["下一步"] = "带骨架带动画 ⇒ 直接出动作件并接面板（tools/codex-scripts/anim-*）"
            elif rigged:
                row["下一步"] = "有骨架没动画 ⇒ 用 anim-make/anim-idle 生成动作件（工具链已参数化）"
            else:
                row["下一步"] = ("无骨架 ⇒ 二选一：①Tripo 页面点「Rig 钻机」+「Text2Motion」再导出；"
                                "②本仓 autorig 链（高模有失败风险，实测 101 万顶点）")
        if not scan_only and f.parent != DEST:
            dst = DEST / f.name.replace(" ", "_")
            try:
                shutil.copy2(f, dst)
                row["收进仓"] = str(dst.relative_to(ROOT))
            except OSError as exc:
                row["收进仓"] = "失败:" + type(exc).__name__
        rows.append(row)
        print("  %-44s 顶点 %-9s 骨架 %-2s 骨头 %-3s 动画 %-2s %s" %
              (row["原件"][:44], row.get("顶点", "-"), row.get("骨架", "-"),
               row.get("骨头", "-"), row.get("动画", "-"), row.get("下一步", "")[:40]))
    if not scan_only and rows:
        (DEST / "receipt.json").write_text(
            json.dumps({"说明": "SOP dh_asset_intake 的收仓收据（骨架=0 ⇒ 未绑骨）",
                        "行": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        print("  收据:", (DEST / "receipt.json").relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
