# tools/clean_junk.py —— 垃圾清理（走回收站，可还原；不永久删）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）：「把临时文件和垃圾清理干净」。
# 口径：**一律走 core.retire 的送回收站**（可还原），不永久删；只动这两类：
#   ① 本项目 state/ 下已知垃圾类（retire.JUNK_PATTERNS 真扫盘）；
#   ② 明确无主的中间件：state 下的 pytest 临时目录、语音中间 wav、本次会话的 %TEMP% 脚本。
# 纪律：源码、data/、_archive/、别的项目（V8）一根汗毛都不碰。
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))          # 直接跑脚本时也要能 import core（别的 tools 都这么做）
STATE = ROOT / "state"


def _mine() -> list:
    out = []
    for p in STATE.glob("pytest-*-tmp"):            # 测试临时目录（retire 的模式名对不上）
        out.append(p)
    for n in ("voice_ptt_in.wav", "voice_in.wav", "voice_asr_16k.wav",
              "_voice_verify.wav"):                  # 语音链的中间件（每次对讲会重建）
        p = STATE / n
        if p.exists():
            out.append(p)
    tmp = Path(tempfile.gettempdir())
    out += [p for p in tmp.glob("v9_*")]               # 本次会话在 %TEMP% 留下的脚本/日志
    return out


def main() -> int:
    from core import retire
    print("== 垃圾清理（送回收站）==")
    dry = retire.sweep(dry_run=True)
    print("项目自扫（retire 模式表）: 候选 %s 件 / %s MB" % (dry.get("候选"), dry.get("总MB")))
    mine = [p for p in _mine() if p.exists()]
    print("无主中间件: %d 件" % len(mine))
    for p in mine[:20]:
        print("   -", p.name if p.parent != Path(tempfile.gettempdir()) else ("%TEMP%/" + p.name))

    paths = [str(p.resolve()) for p in mine]
    if dry.get("候选"):
        r = retire.sweep(dry_run=False)
        print("退休表清扫: 件数=%s 成功=%s 失败=%s" % (r.get("件数"), r.get("成功"), r.get("失败")))
    if paths:
        r2 = retire._recycle(paths, note="无主中间件清理（可还原）")
        print("中间件清理: 成功=%s/%s 失败=%s" % (r2.get("成功"), r2.get("总数"), r2.get("失败")))
    left = [str(p) for p in _mine() if p.exists()]
    print("清理后残留:", len(left), left[:5])
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
