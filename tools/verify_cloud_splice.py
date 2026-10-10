# tools/verify_cloud_splice.py —— 云插件大模型「能不能用」验收（真打 API，不看开关）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 判据：① plugin_key 真解析出 @cf/ id；② 预留槽/IP 待核**如实拒跑**；③ **真跑一次**要 HTTP 200 且出字；
#       ④ 拼接总览数字自洽（可拼接+缺id+预留=100）；⑤ 凭据只报掩码；⑥ HTTP 口子真的挂在面板上。
# 用法：python tools/verify_cloud_splice.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations
import sys as _sys
from pathlib import Path as _P
_ROOT = _P(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))


import sys
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
    print("== 云插件大模型拼接验收 @", ROOT, "==")
    from core import cloud_runner as CR

    r = CR.resolve("text-generation:qwen3-30b-a3b-fp8#7")
    check("plugin_key 能解析出真 @cf/ id", bool(r.get("ok") and r.get("cf_id")),
          "%s · %s · 第 %s 槽" % (r.get("cf_id"), r.get("group_cn"), r.get("slot")))
    bad = CR.resolve("不存在的插件键")
    check("乱报的插件键 → 如实说找不到（还给候选）",
          bad.get("ok") is False and bad.get("候选"), str(bad.get("reason"))[:52])
    src = (ROOT / "core" / "cloud_plugins.py").read_text(encoding="utf-8")
    got = None
    for g in ("vision", "tts", "asr", "image-generation"):
        cand = CR.resolve(g + ":reserved#10")
        if cand.get("ok") and cand.get("reserved"):
            got = CR.run_plugin(g + ":reserved#10", "x"); break
    check("预留槽 → 拒跑且如实说没有 id（绝不编造）",
          bool(got and got.get("ok") is False and "预留" in str(got.get("reason"))),
          str((got or {}).get("reason"))[:56])

    sr = CR.splice_report()
    check("拼接总览数字自洽（可拼接+缺id+预留 = 100）",
          sr.get("自洽") and sr["可拼接（有真 id）"] + sr["缺 id 待核"] + sr["预留槽"] == sr["槽"],
          "槽=%d 可拼接=%d 关着=%d 缺id=%d 预留=%d" % (sr["槽"], sr["可拼接（有真 id）"],
                                                    sr["关着的"], sr["缺 id 待核"], sr["预留槽"]))
    check("凭据只报掩码、不吐原文",
          "…" in str(sr["凭据"]["掩码"]) and len(str(sr["凭据"]["掩码"])) < 24,
          "%s · %s" % (sr["凭据"]["来源"], sr["凭据"]["掩码"]))

    if sr["凭据"]["来源"]:
        out = CR.run_plugin("text-generation:qwen3-30b-a3b-fp8#7", "用四个字回答：你在线吗")
        check("**真跑一次**：HTTP 200 且真出字",
              out.get("ok") and out.get("http") == 200 and bool(out.get("出字")),
              "HTTP %s · %sms · 出字：%s" % (out.get("http"), out.get("ms"),
                                             (out.get("出字") or "")[:40].replace(chr(10), " ")))
    else:
        print("  ℹ 本机没有云插件凭据 ⇒ 真跑这条跳过（如实标注，不假装测过）")

    page = (ROOT / "panel" / "cloud_page.py").read_text(encoding="utf-8")
    check("HTTP 口子真挂在面板上（/api/cloud/run + /api/cloud/splice）",
          "/api/cloud/run" in page and "/api/cloud/splice" in page, "两个都在")
    check("出网纪律：云插件模块自己不发请求，请求只在 runner 这层",
          "不发任何网络请求" in src and "body.net_guard" in (ROOT / "core" / "cloud_runner.py").read_text(encoding="utf-8"),
          "口径与闸都在")

    print("\n口径：能拼接 = 有真 @cf/ id + 开关开着 + 与触手有绑定；预留槽没有 id，绝不编造。")
    print("结论：" + ("✅ 云插件拼接已通" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
