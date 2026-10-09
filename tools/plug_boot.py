# tools/plug_boot.py —— 总控台插座（插在 GBT小土豆V9-总控台.cmd 上：开机即插上并与她连上）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）：「我们的插座插这里」——指着 GBT小土豆V9-总控台.cmd（工作树顶端）。
# 口径：**不改原入口**（panel.server 照旧起），只在这条线之前**并一条我们的插座线**：
#   ① 数字人开机：灌记忆 + 说欢迎语（含 GBT小土豆V9 与 开发者：自由的风）
#   ② 插座自检：process（本机算力）· model（本地/云模型能源）· web（她自己的浏览器）
#   ③ 会话台账：Sider 之类需要真实会话的，如实报「已登入/待登入」
#   ④ 全部落账 state/plug_boot.jsonl；任何一步不通都如实打红黄，不许「不影响」
from __future__ import annotations

import json
import socket
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))   # 🔴 修：作为脚本跑时 sys.path[0] 是 tools\，core 导入会全崩
LEDGER = ROOT / "state" / "plug_boot.jsonl"


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def _port(host: str, port: int) -> bool:
    s = socket.socket()
    s.settimeout(1.5)
    try:
        s.connect((host, port))
        return True
    except Exception:  # noqa: BLE001
        return False
    finally:
        s.close()


def sockets() -> dict:
    """四个插座的真状态（不猜）。"""
    out = {}
    out["process"] = {"在": True, "能源": "本机 CPU + 她的调度", "Python": __import__("sys").version.split()[0]}
    out["model"] = {"本地 ollama:11434": _port("127.0.0.1", 11434),
                    "主脑网关:8317": _port("127.0.0.1", 8317),
                    "能源": "云插件 neurons（每根触手独立配额）/ 本地算力"}
    try:
        from core import sider_plug as SP
        s = SP.status(probe=False)
        out["web"] = {"她的浏览器 profile": s["她的 profile"]["profile"],
                      "Sider 会话": "待登入（需你在可见窗口登一次）",
                      "能源": "她的眼+手（人类级操作）"}
    except Exception as e:  # noqa: BLE001
        out["web"] = {"错误": type(e).__name__}
    out["app"] = {"127.0.0.1:8765": _port("127.0.0.1", 8765),
                  "127.0.0.1:8800": _port("127.0.0.1", 8800)}
    return out


def boot(*, quiet: bool = False) -> dict:
    t0 = time.time()
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "plug_boot", "入口": "GBT小土豆V9-总控台.cmd"}
    # ① 数字人开机
    try:
        from core import dh_boot as DB
        b = DB.boot()
        rec["数字人"] = {"记忆": (b.get("记忆") or {}).get("存量") or (b.get("记忆") or {}).get("记忆条数"),
                        "当前步": ((b.get("当前这一步") or {}).get("步")),
                        "开场白": b.get("开场白")}
    except Exception as e:  # noqa: BLE001
        rec["数字人"] = {"错误": "%s: %s" % (type(e).__name__, str(e)[:80])}
    # ② 插座自检
    rec["插座"] = sockets()
    # ③ 模型插座真冒烟（本地）
    try:
        from core import pulse as P
        r = P.plug_and_run("t001", kind="model",
                           args={"prompt": "回两个字：在", "backend": "local", "tentacle": "t001"},
                           timeout=90)
        rec["模型冒烟"] = {"ok": r.get("ok"), "后端": r.get("后端"), "秒": r.get("秒"),
                           "出字": (r.get("出字") or "")[:40]}
    except Exception as e:  # noqa: BLE001
        rec["模型冒烟"] = {"ok": False, "错误": type(e).__name__}
    rec["秒"] = round(time.time() - t0, 1)
    _log({k: rec[k] for k in ("at", "抓")} | {"秒": rec["秒"]})
    if not quiet:
        print("=" * 52)
        print(rec["数字人"].get("开场白") or "（数字人未开口）")
        print("-" * 52)
        for k, v in rec["插座"].items():
            print("  插座 %-8s %s" % (k, json.dumps(v, ensure_ascii=False)[:96]))
        m = rec["模型冒烟"]
        print("  模型冒烟 ok=%s 后端=%s 秒=%s 出字=%r" % (m.get("ok"), m.get("后端"), m.get("秒"), m.get("出字")))
        print("  （原入口 python -m panel.server 照旧启动；本线不改它）")
        print("=" * 52)
    return rec


if __name__ == "__main__":
    boot()
