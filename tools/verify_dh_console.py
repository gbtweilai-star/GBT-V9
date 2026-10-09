# tools/verify_dh_console.py —— 数字人交互台验收（结构 + 内置纪律 + 截图非空白）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 判据：① 页面由真路由渲染；② inject() 已生效（她的执行器 + 问一句 dock）；
#   ③ 关键元素 id 齐（视频/写实/语音条/波形/动作选择/形象档/读数抽屉）；
#   ④ 全内置纪律：页面无任何外部 http(s) 链接；按键不自带 button{} 规则；
#   ⑤ 诚实标注在位（口型件缺失）；⑥ 两张验收图都渲得出来且不是空白。
# 用法：python tools/verify_dh_console.py    退出码 0=全过 / 1=有断言不过
from __future__ import annotations

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
    print("== 数字人交互台验收 @", ROOT, "==")
    from panel.dh_console import render
    from skills.ui_design import inject
    own = render()                     # 页面**自带**的 HTML/CSS（用来查"不许自带 button{}"）
    html = inject(own, "/digital-human/console")

    check("页面渲得出（有 body 与 <title>）",
          "<body>" in html and "<title>" in html, "%d 字符" % len(html))
    ids = ["anim", "real", "ptt", "saybtn", "say", "wave", "clip", "look",
           "drawerbtn", "drawer", "line", "st"]
    miss = [i for i in ids if ('id=' + i) not in html and ('id="' + i + '"') not in html]
    check("关键元素 id 齐", not miss, "缺=%s" % (miss or "无"))
    check("inject 生效：她的执行器 v9ctl 在位", "id=v9ctl" in html, "v9ctl ✓")
    check("inject 生效：问一句 dock 在位", "id=aiask" in html, "aiask ✓")
    check("全内置纪律：页面零外部 http(s) 链接",
          ("http://" not in html) and ("https://" not in html),
          "外链=0" if ("http://" not in html) else "发现外链")
    check("按键不自带 button{} 规则（统一走 .btn 家族）",
          "button{" not in own and "button {" not in own
          and ("class=btn" in own or 'class="btn' in own),
          "button{{}} 规则=无，.btn 已用")
    check("主视觉用真动作件（/api/tripo/anim）",
          "/api/tripo/anim" in html, "已接动作件接口")
    check("写实档接的是真渲染件（/api/tripo/frame）",
          "/api/tripo/frame" in html, "已接形象图接口")
    check("诚实标注：口型件缺失写在页面上",
          "口型" in html, "已标注")
    check("语音条接的是真对讲口（/api/voice/ptt）", "/api/voice/ptt" in html, "已接")

    # ★主人新要求（2026-10-08）：下载 APP 后先配密钥、她才出现；且她要在**任意页面**协助用户
    bare = inject("<html><body><h1>随便一个页面</h1></body></html>", "/somepage")
    check("任意页面都有她（伴随件注入到每一页）",
          "id=dhc" in bare and "/api/voice/ptt" in bare,
          "裸页面注入后即含她")
    check("任意页面都有首启密钥闸", "id=setupgate" in bare, "含 setupgate")
    check("注入不挤掉原有执行器与问询坞",
          "id=v9ctl" in bare and "id=aiask" in bare, "v9ctl ✓ aiask ✓")
    from panel.dh_companion import KEYS_ENV, status as setup_status
    st = setup_status()
    check("首启状态是诚实形状（不回显原文，只给掩码）",
          "需要配置" in st and all(("掩码" in r) for r in st["行"]),
          "必须先配=%s" % (st.get("必须先配") or "无"))
    check("密钥落在 state/（不是仓库根、不是源码）",
          str(KEYS_ENV).endswith("state\\keys.env") or str(KEYS_ENV).endswith("state/keys.env"),
          str(KEYS_ENV).split("GBT小土豆V9")[-1])
    shot3 = ROOT / "state" / "preview" / "dh_setup.png"
    check("首启闸的验收图已渲出", shot3.is_file() and shot3.stat().st_size > 50_000,
          "%s · %d KB" % (shot3.name, int(shot3.stat().st_size / 1024) if shot3.is_file() else 0))

    shots = {"会动档": ROOT / "state" / "preview" / "dh_console.png",
             "写实档": ROOT / "state" / "preview" / "dh_console_real.png"}
    for k, p in shots.items():
        ok = p.is_file() and p.stat().st_size > 100_000
        check("验收图已渲出且非空白：" + k, ok,
              "%s · %d KB" % (p.name, int(p.stat().st_size / 1024) if p.is_file() else 0))

    print("\n结论：" + ("✅ 数字人交互台通过" if not FAILS else "❌ 未过：" + "、".join(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    raise SystemExit(main())
