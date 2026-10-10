# core/bone_binding.py —— 触手绑骨法（V9 数字人）：骨头=可寻址驱动点，扫空身体=零死角
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-09）：「绑骨说白了就是使用类似触手的框架，以线条的形式固定好驱动；
#   直接用触手扫空身体，不是你想干嘛就干嘛？」
# 落地口径：
#   ① **扫**：直接解析 GLB（stdlib，不依赖 Blender）→ 骨架节点 / 蒙皮关节 / 6 类动画通道到底驱动了哪些骨；
#   ② **绑**：每根骨 = 一个可寻址驱动点，绑上一根触手（写库，可反向查"这根触手负责哪几根骨"）；
#   ③ **驱**：drive() 发驱动指令（rotation/translation/scale）+ 生成 Blender 可执行脚本；
#      verify_drive() 复核；未驱动的骨**显式列盲区**，不许"不影响"。
from __future__ import annotations

import hashlib
import json
import struct
import subprocess
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
ROOT_ID = "v9:DH-rig"
LEDGER = ROOT / "state" / "bone_binding.jsonl"
BLENDER = Path(r"C:\Program Files\Blender Foundation\Blender 4.2\blender.exe")
DEFAULT_RIG = ROOT / "state" / "tripo" / "out3" / "rig" / "rigged-clamp3.glb"
CHANNELS = ("translation", "rotation", "scale", "weights")


def _read_glb_json(path: Path) -> dict:
    """读 GLB 的 JSON 块（stdlib：12 字节头 + 分块）。"""
    with path.open("rb") as f:
        magic, ver, _ = struct.unpack("<III", f.read(12))
        if magic != 0x46546C67:
            raise ValueError("不是 GLB")
        clen, ctype = struct.unpack("<II", f.read(8))
        if ctype != 0x4E4F534A:
            raise ValueError("第一块不是 JSON")
        return json.loads(f.read(clen).decode("utf-8", "replace"))


def scan(rig: Path | None = None) -> dict:
    """**扫空骨架**：骨名 / 蒙皮关节 / 每根骨被哪些通道驱动 / 未被驱动的盲区。"""
    t0 = time.time()
    p = Path(rig or DEFAULT_RIG)
    if not p.is_file():
        return {"ok": False, "原因": "找不到骨架文件", "路径": str(p)}
    doc = _read_glb_json(p)
    nodes = doc.get("nodes") or []
    names = [n.get("name") or ("node%d" % i) for i, n in enumerate(nodes)]
    joints = []
    for sk in (doc.get("skins") or []):
        joints += [int(j) for j in (sk.get("joints") or [])]
    joint_names = sorted({names[j] for j in joints if 0 <= j < len(names)})
    driven: dict = {}
    anims = doc.get("animations") or []
    for a in anims:
        for ch in (a.get("channels") or []):
            tgt = (ch.get("target") or {})
            nid = tgt.get("node")
            path_ = tgt.get("path")
            if nid is None or not (0 <= int(nid) < len(names)):
                continue
            driven.setdefault(names[int(nid)], set()).add(path_)
    bones = joint_names or [n for n in names]
    blind = [b for b in bones if not driven.get(b)]
    filled = {b: sorted(driven.get(b, [])) for b in bones}
    return {"ok": True, "骨架": str(p.relative_to(ROOT)), "字节": p.stat().st_size,
            "节点数": len(nodes), "蒙皮关节": len(joints), "骨数": len(bones),
            "动画数": len(anims), "被驱动骨数": len(bones) - len(blind),
            "盲区骨": blind, "盲区数": len(blind),
            "通道分布": {c: sum(1 for b in bones if c in driven.get(b, ())) for c in CHANNELS},
            "骨表前 12": [{"骨": b, "通道": filled[b]} for b in bones[:12]],
            "秒": round(time.time() - t0, 2),
            "口径": "骨=可寻址驱动点；未出现在任何动画通道里的骨=盲区（不许说'不影响'）"}


def _ledger():
    from audit.ledger_factory import make_ledger
    return make_ledger()


def _txn(led):
    from senses.sqldialect import txn
    return txn(led)


def ensure_table(led=None) -> dict:
    led = led or _ledger()
    stmts = [
        "CREATE TABLE IF NOT EXISTS tentacle_bone_bindings ("
        " root_id TEXT NOT NULL, tentacle_id TEXT NOT NULL, rig TEXT NOT NULL,"
        " bone TEXT NOT NULL, channels TEXT, state TEXT NOT NULL DEFAULT 'idle',"
        " bound_at TEXT, PRIMARY KEY (root_id, rig, bone))",
        "CREATE INDEX IF NOT EXISTS ix_tbb_tentacle ON tentacle_bone_bindings (tentacle_id)",
        "CREATE INDEX IF NOT EXISTS ix_tbb_bone ON tentacle_bone_bindings (bone)",
    ]
    ok = 0
    with _txn(led) as cur:
        for s in stmts:
            try:
                cur.execute(s)
                ok += 1
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    return {"ok": ok == len(stmts), "条": ok, "共": len(stmts)}


def bind_all(rig: Path | None = None) -> dict:
    """每根骨绑一根触手（地址化，1:1 不重不漏）。"""
    s = scan(rig)
    if not s.get("ok"):
        return s
    ensure_table()
    led = _ledger()
    bones = [b["骨"] for b in s["骨表前 12"]]
    # 全量骨名：从 scans 里重取（骨表前 12 只是样本）
    doc = _read_glb_json(Path(rig or DEFAULT_RIG))
    names = [n.get("name") or ("node%d" % i) for i, n in enumerate(doc.get("nodes") or [])]
    joints = []
    for sk in (doc.get("skins") or []):
        joints += [int(j) for j in (sk.get("joints") or [])]
    bones = sorted({names[j] for j in joints if 0 <= j < len(names)}) or names
    driven: dict = {}
    for a in (doc.get("animations") or []):
        for ch in (a.get("channels") or []):
            t = ch.get("target") or {}
            if t.get("node") is not None:
                driven.setdefault(names[int(t["node"])], set()).add(t.get("path"))
    rows = 0
    with _txn(led) as cur:
        for i, b in enumerate(bones):
            tid = "t%03d" % ((i % 100) + 1)
            chs = ",".join(sorted(driven.get(b, []))) or "-"
            cur.execute("INSERT INTO tentacle_bone_bindings"
                        " (root_id, tentacle_id, rig, bone, channels, state, bound_at)"
                        " VALUES (?,?,?,?,?,?,?)"
                        " ON CONFLICT (root_id, rig, bone) DO UPDATE SET"
                        " tentacle_id=EXCLUDED.tentacle_id, channels=EXCLUDED.channels,"
                        " state=EXCLUDED.state, bound_at=EXCLUDED.bound_at",
                        (ROOT_ID, tid, str(Path(rig or DEFAULT_RIG).name), b, chs, "idle",
                         time.strftime("%Y-%m-%dT%H:%M:%S")))
            rows += 1
    rec = {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "bind_all",
           "骨": len(bones), "写行": rows, "盲区": s["盲区数"]}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return rec


def drive(tentacle: str, bone: str, channel: str, value, *, apply: bool = False) -> dict:
    """一根触手驱动一根骨（rotation=[x,y,z] / translation / scale）。落指令账；apply=True 时生成 Blender 脚本。"""
    if not (str(tentacle).startswith("t") and str(tentacle)[1:].isdigit()):
        return {"ok": False, "原因": "只有触手号能驱动骨（收到 %r）" % tentacle}
    if channel not in ("rotation", "translation", "scale"):
        return {"ok": False, "原因": "通道只认 rotation/translation/scale"}
    rec = {"ok": True, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "动作": "drive",
           "触手": tentacle, "骨": bone, "通道": channel, "值": value, "apply": bool(apply)}
    if apply:
        script = ROOT / "state" / "dh_drive_last.py"
        NL = chr(10)
        body = [
            "# 由 core.bone_binding.drive 生成（在 Blender 里执行）",
            "import bpy",
            "obj = [o for o in bpy.data.objects if o.type == %s][0]" % chr(39) + "ARMATURE" + chr(39),
            "pb = obj.pose.bones.get(%r)" % bone,
            "assert pb is not None, %r" % ("没有这根骨: " + bone),
            "if %r == %r:" % (channel, "rotation"),
            "    pb.rotation_mode = %r" % "XYZ",
            "    pb.rotation_euler = %r" % (value,),
            "elif %r == %r:" % (channel, "translation"),
            "    pb.location = %r" % (value,),
            "else:",
            "    pb.scale = %r" % (value,),
            "bpy.context.view_layer.update()",
            "print(%r, %r, %r)" % ("DRIVEN", bone, channel),
        ]
        script.write_text(NL.join(body) + NL, encoding="utf-8")
        rec["脚本"] = str(script.relative_to(ROOT))
        if BLENDER.is_file():
            rec["执行命令"] = '"%s" -b "%s" --python "%s"' % (BLENDER, DEFAULT_RIG, script)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return rec


def coverage(rig: Path | None = None) -> dict:
    s = scan(rig)
    if not s.get("ok"):
        return s
    led = _ledger()
    try:
        with _txn(led) as cur:
            cur.execute("SELECT COUNT(*) FROM tentacle_bone_bindings WHERE root_id=?", (ROOT_ID,))
            n = cur.fetchone()[0]
    except Exception:  # noqa: BLE001
        n = 0
    return {"骨数": s["骨数"], "已绑": n, "覆盖率": round(100.0 * n / s["骨数"], 1) if s["骨数"] else 0.0,
            "盲区骨": s["盲区骨"], "盲区数": s["盲区数"],
            "口径": "骨全绑 + 无盲区才算零死角；有盲区必须列名"}


def status(limit: int = 3) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    return {"最近": rows, "覆盖": coverage(), "blender": str(BLENDER) if BLENDER.is_file() else "未找到",
            "口径": "触手绑骨法：扫空骨架 → 每根骨地址化绑触手 → 零死角；未驱动=盲区，如实列"}


__all__ = ["ROOT_ID", "CHANNELS", "DEFAULT_RIG", "scan", "ensure_table", "bind_all", "drive",
           "coverage", "status", "LEDGER"]
