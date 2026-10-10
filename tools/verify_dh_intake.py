# tools/verify_dh_intake.py —— 「数字人资产接入」固化验收（SOP 在册 + 触手可用 + 成果入脑）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据（主人 2026-10-09：「调用触手接管一次做个固化，确保下次重启大模型不会又不懂」）：
#   ① SOP dhui_asset_intake 真在 playbooks 册里，且每阶段有**可判定**验收；
#   ② 固化脚本能跑出**真读数**（顶点/骨架/骨头/动画），且对无骨架件**如实说缺哪步**；
#   ③ 收据落盘（下次不用重新摸）；
#   ④ 触手的眼（gui_perception.capture）**真能截屏**（这是"接管"的前提）；
#   ⑤ 这次做法**已入原生大脑**（重启后仍可召回）。
# 用法：python tools/verify_dh_intake.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import subprocess
import sys
import time
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
    print("== 数字人资产接入 · 固化验收 @", ROOT, "==")
    from core import playbooks as PB
    ids = [f.id for f in PB.PLAYBOOKS]
    f = next((x for x in PB.PLAYBOOKS if x.id == "dh_asset_intake"), None)
    check("SOP 真在册（dh_asset_intake）", f is not None, "册里现有：%s" % "、".join(ids))
    if f:
        ok_stage = all(len(s.验收) >= 2 for s in f.阶段)
        check("每个阶段都有 ≥2 条可判定验收", ok_stage,
              "%d 个阶段 · 验收条数 %s" % (len(f.阶段), [len(s.验收) for s in f.阶段]))

    r = subprocess.run([sys.executable, str(ROOT / "tools" / "dh_asset_intake.py"), "--scan"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    out = r.stdout or ""
    check("固化脚本能跑出真读数（--scan）", "顶点" in out and "骨架" in out,
          (out.strip().splitlines()[1] if len(out.strip().splitlines()) > 1 else out[:60]))
    check("对无骨架件如实说缺哪步（不假装能出动作件）",
          "无骨架" in out and ("Rig 钻机" in out or "autorig" in out), "已给出两条出路")
    rec = ROOT / "state" / "tripo" / "out3" / "receipt.json"
    check("收据落盘（下次不用重新摸）", rec.is_file(),
          "%s · %d 字节" % (rec.name, rec.stat().st_size if rec.is_file() else 0))

    try:
        from core import gui_perception as GP
        got = GP.capture()          # ★返回的是 PIL Image（不是路径）——当路径用会 TypeError，踩过
        out = ROOT / "state" / "preview" / "tentacle_capture.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(got, (str, Path)):
            Path(got).replace(out) if Path(got).is_file() else None
        else:
            got.save(out)
        sz = out.stat().st_size if out.is_file() else 0
        check("触手的眼真能截屏（接管的前提）", sz > 5000,
              "%s · %d 字节" % (out.name, sz))
    except Exception as exc:                                  # noqa: BLE001
        check("触手的眼真能截屏（接管的前提）", False, "截屏失败：%s" % type(exc).__name__)

    # 入脑分两步看：**写入**（真失败才算失败）与**召回**（编码是异步的，给重试；召不回就如实记）
    try:
        from core import memory as MEM
        w = MEM.brain.remember(
            "数字人 3D 资产接入 SOP（dh_asset_intake）：扫下载夹与 state/tripo/out3 → 解析 GLB 验骨架/骨头/动画 "
            "→ 收进仓写 receipt.json → 无骨架就如实报缺哪步（Tripo 点 Rig 钻机+Text2Motion，或本仓 autorig 链）"
            "→ 有骨架没动画用 anim-make/anim-idle → 接 /digital-human/console。",
            origin="solidify", category="系统", meta={"sop": "dh_asset_intake"})
        check("入脑写入成功（有 id 才算写进去）", bool(w.get("ok") and w.get("id")),
              "id=%s" % w.get("id"))
        hit, ans = False, ""
        for _ in range(4):
            q = MEM.brain.ask("数字人 3D 资产接入")
            ans = str(q.get("answer") or "")
            if "资产接入" in ans or "dh_asset_intake" in ans:
                hit = True
                break
            time.sleep(1.5)
        check("重启后可召回（召回实测）", bool(hit),
              ("召回：" + ans[:56].replace(chr(10), " ")) if hit else "写入成功但这一刻召不回（编码异步）——如实记，不假称")
    except Exception as exc:                                  # noqa: BLE001
        check("入脑写入成功（有 id 才算写进去）", False, type(exc).__name__)

    print("\n口径：固化 = 写进仓（SOP+脚本+收据）**并且**入脑；重启后照 SOP 走，不必再摸索。")
    print("结论：" + ("✅ 数字人资产接入已固化" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
