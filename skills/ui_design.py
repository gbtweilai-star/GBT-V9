# skills/ui_design.py —— UI 设计 Skill：负责实现（术语 → 设计令牌 → 真 HTML/CSS）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 分工（与术语 Skill 对齐）：术语层负责"说准"，本模块负责"做出来"——
#   ① 设计令牌：配色/间距刻度/字阶/圆角/层级/动效时长（一处定义，全站复用，改一处全站一致）
#   ② 组件：卡片/按钮/表格/徽章/进度/对话气泡（都从令牌取值，不写死颜色数字）
#   ③ 术语实现：把 hover_lift_yield 这类意图真的生成 CSS（含邻位让位）
#   ④ 审美自检：对比度、间距是否在刻度、层级是否超三层 —— 可校验，不靠"我觉得好看"
#
# 纪律：审丑也审"诚实"——空态必须说清原因，读数不可用时不许显示 0（沿用全仓口径）。
from core.swallow import swallow as _swallow
import re
from dataclasses import dataclass, field

# ═══════════ ① 设计令牌（审美先说清楚：颜色/景深/节奏/层级）═══════════
# 风格：**城市未来科技感** —— 冷色霓虹（青/紫/品红）+ 城市网格地平线 + 玻璃面板 + 景深分层。
# 全部取值都在这里，页面只引用 var(--…)：改一处，全站一致。
TOKENS: dict = {
    "color": {
        "bg": "#070b14", "bg_deep": "#04060c", "bg_horizon": "#101c33",
        "surface": "#0e1624", "surface_2": "#152034", "surface_3": "#1d2c47",
        "border": "#26374f", "hairline": "#3a5a86",
        "text": "#dbe7f5", "muted": "#93a7c0",
        "accent": "#39d0ff", "accent_2": "#7c5cff", "accent_3": "#ff4d9d",
        "ok": "#3fd68a", "warn": "#f0b429", "bad": "#ff5d6c", "on_accent": "#04121b",
        "grid": "#14243d",
    },
    "space": (4, 8, 12, 16, 24, 32),          # 间距刻度：只用这些值
    "radius": {"sm": 6, "md": 12, "lg": 18, "pill": 999},
    "type": {"h1": 22, "h2": 16, "body": 14, "small": 12, "mono": 13},
    "z": {"content": 1, "sticky": 10, "dock": 100, "toast": 1000},
    "motion": {"fast": 120, "base": 180, "slow": 320},
    # 景深（立体感）：一层环境阴影 + 一层接触阴影 + 一层内高光；越深的层数越大
    "depth": {
        "d1": "0 1px 0 rgba(255,255,255,.05) inset,0 2px 6px rgba(0,0,0,.45)",
        "d2": "0 1px 0 rgba(255,255,255,.06) inset,0 10px 24px rgba(0,0,0,.55),"
              "0 2px 6px rgba(0,0,0,.5)",
        "d3": "0 1px 0 rgba(255,255,255,.08) inset,0 22px 48px rgba(0,0,0,.62),"
              "0 6px 14px rgba(0,0,0,.5)",
    },
    "glow": {"accent": "0 0 0 1px rgba(57,208,255,.35),0 0 18px rgba(57,208,255,.35)",
             "accent_2": "0 0 0 1px rgba(124,92,255,.40),0 0 22px rgba(124,92,255,.35)"},
    # ── v2 增补：更未来的霓虹边 + 更深的景深（3D 层次） ──
    "neon": {"edge": "rgba(57,208,255,.45)", "edge_2": "rgba(124,92,255,.45)",
             "violet": "#a78bfa", "ice": "#8ef0ff"},
    "depth_v2": {
        "d1": "0 1px 0 rgba(255,255,255,.06) inset,0 2px 8px rgba(0,0,0,.5)",
        "d2": "0 1px 0 rgba(255,255,255,.07) inset,0 14px 30px rgba(0,0,0,.6),"
              "0 3px 8px rgba(0,0,0,.5)",
        "d3": "0 1px 0 rgba(255,255,255,.09) inset,0 28px 60px rgba(0,0,0,.66),"
              "0 8px 18px rgba(0,0,0,.55),0 0 0 1px rgba(57,208,255,.10)",
    },
}

# 对比度下限（WCAG AA）：普通文字 4.5，大字号 3.0
CONTRAST_MIN = {"body": 4.5, "large": 3.0}


def _hex_rgb(h: str) -> tuple:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb: tuple) -> float:
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: str, bg: str) -> float:
    l1, l2 = _lum(_hex_rgb(fg)), _lum(_hex_rgb(bg))
    hi, lo = max(l1, l2), min(l1, l2)
    return round((hi + 0.05) / (lo + 0.05), 2)


# ═══════════ ② 组件：全部从令牌取值 ═══════════
def _c(k: str) -> str:
    return TOKENS["color"][k]


def css_vars() -> str:
    c = TOKENS["color"]
    lines = [f"  --{k.replace('_', '-')}: {v};" for k, v in c.items()]
    # 下划线别名：全站既有样式里有 var(--accent_2)/var(--surface_2)/var(--surface_3)
    # 这种写法，而上面只产出连字符名 —— 结果这些声明**整条失效**（当前页按键的霓虹底、
    # .btn 的渐变都因此消失，看着就不整齐）。这里两种写法都给，老样式立刻恢复正常。
    lines += [f"  --{k}: {v};" for k, v in c.items() if "_" in k]
    for k, v in TOKENS["radius"].items():
        lines.append(f"  --r-{k}: {v}px;")
    for k, v in TOKENS["type"].items():
        lines.append(f"  --fs-{k}: {v}px;")
    for k, v in TOKENS["motion"].items():
        lines.append(f"  --t-{k}: {v}ms;")
    for k, v in TOKENS["depth"].items():
        lines.append(f"  --sh-{k}: {v};")
    for k, v in TOKENS["glow"].items():
        lines.append(f"  --glow-{k.replace('_', '-')}: {v};")
    for k, v in TOKENS["depth_v2"].items():                    # v2 景深用 sh2-*，旧 sh-* 不动
        lines.append(f"  --sh2-{k}: {v};")
    for k, v in TOKENS["neon"].items():
        lines.append(f"  --neon-{k.replace('_', '-')}: {v};")
    lines.append(f"  --neon-violet: {TOKENS['neon']['violet']};")
    lines.append(f"  --neon-ice: {TOKENS['neon']['ice']};")
    for i, v in enumerate(TOKENS["space"]):
        lines.append(f"  --s{i}: {v}px;")
    return ":root{\n" + "\n".join(lines) + "\n}"


# 城市天际线/网格底纹：三组渐变叠出「未来城市」的地平线与楼体光柱
_HORIZON = """  background-image:
    radial-gradient(1100px 520px at 12% -12%, rgba(57,208,255,.16), transparent 62%),
    radial-gradient(900px 460px at 88% -6%, rgba(124,92,255,.16), transparent 62%),
    radial-gradient(700px 380px at 50% 108%, rgba(255,77,157,.10), transparent 60%),
    linear-gradient(180deg, var(--bg) 0%, var(--bg-horizon) 62%, var(--bg-deep) 100%);
  background-attachment: fixed;"""


def base_css() -> str:
    """基础样式：城市未来科技感 —— 分层背景/网格地平线/玻璃面板/景深/霓虹强调。

    规则：颜色、圆角、间距、动效、阴影**全部**走令牌；不写死色值。
    """
    return f"""{css_vars()}
*{{box-sizing:border-box}}
html{{color-scheme:dark}}
body{{margin:0;color:var(--text);
{_HORIZON}
  font:var(--fs-body)/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
  padding:var(--s4,24px);min-height:100vh}}
/* 城市网格（上密下疏的地平线感） */
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:52px 52px;opacity:.5;
  mask-image:radial-gradient(1200px 680px at 50% -8%,#000 26%,transparent 84%);
  -webkit-mask-image:radial-gradient(1200px 680px at 50% -8%,#000 26%,transparent 84%)}}
/* 细扫描线（CRT 质感，极淡，不影响阅读） */
body::after{{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:repeating-linear-gradient(180deg,transparent 0 2px,rgba(255,255,255,.014) 2px 3px);
  opacity:.6}}
main,nav.top,footer{{position:relative;z-index:0}}
h1,h2{{letter-spacing:.4px}}
h1{{font-size:var(--fs-h1);margin:0 0 var(--s3,16px);
  background:linear-gradient(92deg,var(--text),var(--accent) 55%,var(--accent-2));
  -webkit-background-clip:text;background-clip:text;color:transparent;
  text-shadow:0 0 26px rgba(57,208,255,.18)}}
h2{{font-size:var(--fs-h2);margin:var(--s4,24px) 0 var(--s2,8px);
  color:var(--text);border-left:3px solid var(--accent);padding-left:var(--s2,8px)}}
a{{color:var(--accent)}}
.muted{{color:var(--muted)}}
.ok{{color:var(--ok)}} .warn{{color:var(--warn)}} .bad{{color:var(--bad)}}
/* 玻璃面板 + 景深 + 悬浮微 3D（立体感） */
.card{{position:relative;padding:var(--s3,16px);margin:var(--s2,8px) 0;
  border-radius:var(--r-lg);border:1px solid rgba(255,255,255,.075);
  background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.012));
  backdrop-filter:blur(10px) saturate(125%);-webkit-backdrop-filter:blur(10px) saturate(125%);
  box-shadow:var(--sh-d2);transform-style:preserve-3d;
  transition:transform var(--t-base) ease,box-shadow var(--t-base) ease,
    border-color var(--t-base) ease}}
.card::after{{content:"";position:absolute;left:0;right:0;top:0;height:1px;border-radius:var(--r-lg);
  background:linear-gradient(90deg,transparent,rgba(57,208,255,.65),transparent);opacity:.75}}
.card:hover{{transform:perspective(900px) rotateX(1.1deg) rotateY(-1.1deg) translateY(-2px);
  box-shadow:var(--sh-d3);border-color:rgba(57,208,255,.34)}}
.row{{display:flex;gap:var(--s3,16px);flex-wrap:wrap}}
.badge{{display:inline-block;padding:4px 8px;border-radius:var(--r-pill);
  border:1px solid var(--border);font-size:var(--fs-small);background:rgba(255,255,255,.03)}}
/* ── 按键：全站**唯一**一套（尺寸/圆角/描边/悬停/按下统一；页面不许各自发明） ── */
.btn{{display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-height:34px;box-sizing:border-box;
  background:linear-gradient(180deg,var(--surface_2),var(--surface));
  color:var(--text);border:1px solid var(--border);border-radius:var(--r-sm);
  padding:0 14px;cursor:pointer;font:inherit;font-size:var(--fs-small);line-height:1;
  box-shadow:var(--sh-d1);white-space:nowrap;
  transition:transform var(--t-fast) ease,box-shadow var(--t-fast) ease,
    color var(--t-fast) ease,border-color var(--t-fast) ease,background var(--t-fast) ease}}
.btn:hover{{color:var(--accent);border-color:var(--hairline);box-shadow:var(--sh-d2)}}
.btn:active{{transform:translateY(1px);box-shadow:none}}
.btn:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
.btn:disabled{{opacity:.45;cursor:not-allowed;transform:none;box-shadow:none}}
.btn.primary{{background:linear-gradient(180deg,var(--accent),var(--accent_2));
  color:var(--on_accent);border-color:transparent;box-shadow:var(--glow-accent)}}
.btn.primary:hover{{color:var(--on_accent);border-color:transparent}}
.btn.ghost,.ghost{{background:transparent;box-shadow:none;color:var(--muted)}}
.btn.ghost:hover,.ghost:hover{{color:var(--accent);border-color:var(--hairline)}}
.btn.danger{{color:var(--bad);border-color:rgba(248,81,73,.5)}}
.btn.on,.btn.active{{background:linear-gradient(180deg,var(--accent_3),var(--accent_2));
  color:var(--on_accent);border-color:transparent;box-shadow:var(--glow-accent)}}
.btn.sm{{min-height:28px;padding:0 10px;font-size:12px}}
.btn.lg{{min-height:40px;padding:0 18px;font-size:var(--fs-body)}}
/* 控制条：一排按键/下拉统一间距与换行（页面别再用 div 堆） */
.btnbar,.toolbar{{display:flex;gap:var(--s2,8px);flex-wrap:wrap;align-items:center;
  margin:var(--s2,8px) 0}}
.btnbar .sep,.toolbar .sep{{width:1px;height:22px;background:var(--border);margin:0 2px}}
select,input[type=text],input[type=search],textarea{{box-sizing:border-box;min-height:34px;
  background:linear-gradient(180deg,var(--surface_2),var(--surface));color:var(--text);
  border:1px solid var(--border);border-radius:var(--r-sm);padding:0 10px;font:inherit;
  font-size:var(--fs-small);transition:border-color var(--t-fast) ease,box-shadow var(--t-fast) ease}}
select:focus,input:focus,textarea:focus{{outline:none;border-color:var(--accent);
  box-shadow:var(--glow-accent)}}
textarea{{padding:8px 10px;min-height:88px;line-height:1.5}}
/* 标签/徽标：一个口径（旧页的 .pill/.kind 都归到这里） */
.badge,.pill,.kind,.chip{{display:inline-flex;align-items:center;gap:4px;
  padding:2px 8px;border-radius:var(--r-pill);border:1px solid var(--border);
  background:rgba(255,255,255,.03);font-size:var(--fs-small);line-height:1.5}}
/* 裸元素（没写 class 的 <button>/<select>/<input>）也统一 —— 走 Page.render() 的新页面
   只有这份 base_css（没有 skin_css 的 !important 覆盖层），所以这里必须自带归一，
   否则同一个按钮在两种页面上长得不一样。 */
button{{display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-height:34px;box-sizing:border-box;line-height:1;
  background:linear-gradient(180deg,var(--surface_2),var(--surface));
  color:var(--text);border:1px solid var(--border);border-radius:var(--r-sm);
  padding:0 14px;font:inherit;font-size:var(--fs-small);cursor:pointer;white-space:nowrap;
  box-shadow:var(--sh-d1);
  transition:transform var(--t-fast) ease,box-shadow var(--t-fast) ease,
    color var(--t-fast) ease,border-color var(--t-fast) ease}}
button:hover{{color:var(--accent);border-color:var(--hairline);box-shadow:var(--sh-d2)}}
button:active{{transform:translateY(1px);box-shadow:none}}
button:disabled{{opacity:.45;cursor:not-allowed;transform:none}}
button.primary{{background:linear-gradient(180deg,var(--accent),var(--accent_2));
  color:var(--on_accent);border-color:transparent;box-shadow:var(--glow-accent)}}
button.ghost{{background:transparent;box-shadow:none;color:var(--muted)}}
select{{min-height:34px;box-sizing:border-box;border-radius:var(--r-sm);
  background:linear-gradient(180deg,var(--surface_2),var(--surface));
  color:var(--text);border:1px solid var(--border);padding:0 10px;font:inherit;
  font-size:var(--fs-small)}}
select:focus,input:focus,textarea:focus{{outline:none;border-color:var(--accent);
  box-shadow:var(--glow-accent)}}
table{{width:100%;border-collapse:collapse}}
th,td{{border-bottom:1px solid var(--border);padding:8px;text-align:left;
  font-size:var(--fs-mono)}}
thead th{{position:sticky;top:0;background:rgba(10,18,30,.86);backdrop-filter:blur(6px);
  color:var(--muted);font-weight:500}}
tbody tr:hover td{{background:rgba(57,208,255,.06)}}
code,.num{{font-variant-numeric:tabular-nums;color:var(--accent)}}
pre{{background:rgba(4,7,13,.6);border:1px solid var(--border);border-radius:var(--r-md);
  padding:var(--s2,8px)}}
.progress{{height:8px;background:rgba(255,255,255,.06);border-radius:var(--r-pill);overflow:hidden;
  box-shadow:0 1px 0 rgba(255,255,255,.05) inset}}
.progress>i{{display:block;height:100%;border-radius:var(--r-pill);
  background:linear-gradient(90deg,var(--accent),var(--accent-2));
  box-shadow:0 0 14px rgba(57,208,255,.5)}}
.bubble{{background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.02));
  border:1px solid var(--border);border-radius:var(--r-lg);
  padding:var(--s2,8px) var(--s3,16px);max-width:68%}}
.bubble.me{{background:linear-gradient(180deg,var(--accent),var(--accent-2));
  color:var(--on-accent);margin-left:auto;border:0}}
.dock{{position:fixed;right:var(--s4,24px);bottom:var(--s4,24px);z-index:{TOKENS['z']['dock']};
  border-radius:var(--r-lg);padding:var(--s2,8px);display:flex;gap:var(--s2,8px);align-items:center;
  border:1px solid rgba(255,255,255,.09);
  background:linear-gradient(180deg,rgba(21,32,52,.92),rgba(7,11,20,.92));
  backdrop-filter:blur(12px);box-shadow:var(--sh-d3)}}
.dock input{{background:rgba(4,7,13,.7);border:1px solid var(--border);color:var(--text);
  border-radius:var(--r-sm);padding:8px 12px;width:min(46vw,420px);font:inherit}}
.dock input:focus{{outline:none;border-color:var(--accent);box-shadow:var(--glow-accent)}}
nav.top{{display:flex;gap:var(--s2,8px);flex-wrap:wrap;align-items:center;position:sticky;top:0;
  z-index:{TOKENS['z']['sticky']};border-radius:var(--r-md);
  padding:var(--s2,8px) var(--s3,16px);margin-bottom:var(--s3,16px);
  border:1px solid rgba(255,255,255,.08);
  background:linear-gradient(180deg,rgba(21,32,52,.86),rgba(7,11,20,.86));
  backdrop-filter:blur(12px) saturate(130%);box-shadow:var(--sh-d2)}}
nav.top a{{color:var(--text);text-decoration:none;padding:4px 4px;border-bottom:1px solid transparent;
  transition:color var(--t-fast) ease,border-color var(--t-fast) ease}}
nav.top a:hover{{color:var(--accent);border-bottom-color:var(--hairline)}}
nav.top a.on{{color:var(--accent);border-bottom-color:var(--accent);
  text-shadow:0 0 12px rgba(57,208,255,.55)}}
nav.top a.brand{{display:inline-flex;align-items:center;gap:8px;font-weight:600;
  padding-right:var(--s2,8px);border-right:1px solid var(--border)}}
nav.top a.brand img{{border-radius:50%;background:rgba(4,7,13,.7);object-fit:contain;
  box-shadow:var(--glow-accent)}}
/* ── 12 个按键：立体按钮 + 当前页霓虹 + 分组标签（全部内置，无外跳） ── */
nav.top .navbtns{{display:flex;gap:8px;flex-wrap:wrap;align-items:center}}
nav.top .navgrp{{color:var(--muted);font-size:var(--fs-small);margin:0 4px 0 8px;
  padding-left:8px;border-left:1px solid var(--border);opacity:.85}}
nav.top a.navbtn{{display:inline-flex;align-items:center;gap:4px;padding:6px 12px;
  border-radius:var(--r-pill);border:1px solid var(--border);
  background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.015));
  box-shadow:var(--sh-d1);white-space:nowrap;
  transition:transform var(--t-fast) ease,box-shadow var(--t-fast) ease,
    color var(--t-fast) ease,border-color var(--t-fast) ease}}
nav.top a.navbtn:hover{{transform:translateY(-1px);color:var(--accent);
  border-color:var(--hairline);box-shadow:var(--sh-d2)}}
nav.top a.navbtn:active{{transform:translateY(1px);box-shadow:none}}
nav.top a.navbtn.on{{color:var(--on-accent);border-color:transparent;
  background:linear-gradient(180deg,var(--accent),var(--accent_2));
  box-shadow:var(--glow-accent);font-weight:600}}
nav.top a.navbtn:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
/* 大号 KPI（城市大屏那种数字质感） */
.kpi{{font-size:32px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1.1;
  background:linear-gradient(180deg,var(--text),var(--accent));
  -webkit-background-clip:text;background-clip:text;color:transparent;
  text-shadow:0 0 24px rgba(57,208,255,.22)}}
/* HUD 角标：四角描边的"终端窗口"感 */
.hud{{position:relative}}
.hud::before,.hud::after{{content:"";position:absolute;width:14px;height:14px;
  border:1px solid var(--accent);opacity:.55}}
.hud::before{{left:6px;top:6px;border-right:0;border-bottom:0;border-radius:var(--r-sm) 0 0 0}}
.hud::after{{right:6px;bottom:6px;border-left:0;border-top:0;border-radius:0 0 var(--r-sm) 0}}
::-webkit-scrollbar{{width:10px;height:10px}}
::-webkit-scrollbar-track{{background:var(--bg-deep)}}
::-webkit-scrollbar-thumb{{background:linear-gradient(180deg,var(--surface_3),var(--surface_2));
  border-radius:var(--r-pill);border:2px solid var(--bg-deep)}}
::-webkit-scrollbar-thumb:hover{{background:var(--hairline)}}
@media (prefers-reduced-motion:reduce){{
  .card,.card:hover,.btn{{transition:none;transform:none}}
}}
{_v2_css()}"""


def _v2_css(*, force: bool = False) -> str:
    """v2 视觉层：更未来 + 立体（3D 抬升 / 霓虹描边 / HUD 角标 / 呼吸地平线）。

    force=True 时给"老页面"用（带 !important，压过页面自带样式）；
    force=False 时给 Page.render() 新页面用（纯净规则即可）。
    两条路径都吃同一份，保证同一个控件在哪种页面上长得一样。
    """
    i = "!important" if force else ""

    def r(sel: str, body: str) -> str:
        return f"{sel}{{{body}}}"

    out = [
        # 呼吸地平线：极慢的位移，制造"城市夜景在流"的感觉（可被 reduced-motion 关掉）
        "@keyframes v9drift{0%{background-position:0 0,0 0,0 0}"
        "100%{background-position:0 -52px,52px 0,0 0}}",
        r("body::before", "animation:v9drift 26s linear infinite"),
        # 卡片：3D 抬升 + 顶部高光 + 四角 HUD
        r(".card,[class*=card]", f"position:relative;border-radius:var(--r-lg){i};"
          f"box-shadow:var(--sh2-d2){i};transform-style:preserve-3d;"
          f"transition:transform var(--t-base) ease,box-shadow var(--t-base) ease,"
          f"border-color var(--t-base) ease"),
        r(".card:hover,[class*=card]:hover",
          "transform:perspective(1100px) rotateX(1.4deg) rotateY(-1.2deg) translateY(-3px) "
          "scale(1.004);box-shadow:var(--sh2-d3);border-color:var(--neon-edge)"),
        # HUD 角标：给 .hud 或加了 data-hud 的卡片四角描边
        r(".hud::before,.hud::after,[data-hud]::before,[data-hud]::after",
          "content:'';position:absolute;width:16px;height:16px;pointer-events:none;"
          "border:1px solid var(--neon-edge);opacity:.7"),
        r(".hud::before,[data-hud]::before",
          "left:7px;top:7px;border-right:0;border-bottom:0;border-radius:var(--r-md) 0 0 0"),
        r(".hud::after,[data-hud]::after",
          "right:7px;bottom:7px;border-left:0;border-top:0;border-radius:0 0 var(--r-md) 0"),
        # 导航：图标芯片 + 立体胶囊 + 当前页霓虹
        # ★ 布局规则必须在这里也有一份：总控台这类页面走 inject→skin_css，
        #   而 .navbtns{display:flex} 原先只在 base_css 里 → 胶囊没有 flex 容器就挤成一坨
        #   （真机表现：导航变成一行粘连的纯文本，完全不成按键）。
        r("nav.top .navbtns", f"display:flex;gap:8px;flex-wrap:wrap;align-items:center{i}"),
        r("nav.top .navgrp", f"color:var(--muted);font-size:var(--fs-small);"
          f"margin:0 4px 0 8px;padding-left:8px;border-left:1px solid var(--border);"
          f"opacity:.85{i}"),
        r("nav.top a.navbtn", f"display:inline-flex;align-items:center;gap:5px;"
          f"padding:6px 12px;border-radius:var(--r-pill);border:1px solid var(--border);"
          f"background:linear-gradient(180deg,rgba(255,255,255,.06),rgba(255,255,255,.015)){i};"
          f"color:var(--text);text-decoration:none;white-space:nowrap;"
          f"box-shadow:0 1px 0 rgba(255,255,255,.07) inset,0 3px 10px rgba(0,0,0,.45)"),
        r("nav.top a.brand", f"display:inline-flex;align-items:center;gap:8px;font-weight:600;"
          f"color:var(--text);text-decoration:none;padding-right:var(--s2,8px);"
          f"border-right:1px solid var(--border){i}"),
        r("nav.top a.navbtn:hover",
          "transform:translateY(-2px);box-shadow:0 1px 0 rgba(255,255,255,.1) inset,"
          "var(--sh2-d2)"),
        r("nav.top a.navbtn.on",
          "background:linear-gradient(180deg,var(--accent),var(--accent_2));"
          "box-shadow:var(--glow-accent),0 6px 18px rgba(57,208,255,.28);border-color:transparent"),
        # 按钮：立体倒角（顶高光 + 底阴影 + 按下真实下沉）
        r(".btn,button", "box-shadow:0 1px 0 rgba(255,255,255,.08) inset,0 3px 8px rgba(0,0,0,.45)"),
        r(".btn:hover,button:hover",
          "box-shadow:0 1px 0 rgba(255,255,255,.12) inset,0 6px 16px rgba(0,0,0,.5),"
          "0 0 0 1px var(--neon-edge)"),
        r(".btn:active,button:active",
          "transform:translateY(2px);box-shadow:0 1px 0 rgba(0,0,0,.5) inset"),
        r(".btn.primary,button.primary,button.prim",
          "box-shadow:var(--glow-accent),0 1px 0 rgba(255,255,255,.25) inset"),
        # 表格：斑马 + 悬浮抬升 + 数字等宽
        r("tbody tr:nth-child(even) td", "background:rgba(255,255,255,.022)"),
        r("tbody tr:hover td", "background:rgba(57,208,255,.07)"),
        r("td.num,td .num", "font-variant-numeric:tabular-nums"),
        # KPI / 大数字：立体渐变字
        r(".kpi,.numbig", "background:linear-gradient(180deg,#fff,var(--accent) 78%);"
          "-webkit-background-clip:text;background-clip:text;color:transparent;"
          "text-shadow:0 1px 0 rgba(0,0,0,.35)"),
        # 分区标题：左侧霓虹条 + 渐变字
        r("h2", "position:relative;padding-left:12px;border-left:3px solid var(--accent);"
          "background:linear-gradient(90deg,var(--text),var(--muted) 70%,transparent);"
          "-webkit-background-clip:text;background-clip:text;color:transparent"),
        # 终端（AI 终端对话面板）
        r(".term", "font-family:ui-monospace,Consolas,'Cascadia Mono',monospace;font-size:13px;"
          "background:radial-gradient(120% 100% at 20% 0%,rgba(57,208,255,.06),transparent 60%),"
          "rgba(3,6,12,.92);border:1px solid var(--border);border-radius:var(--r-md);"
          "padding:12px;min-height:320px;max-height:60vh;overflow:auto;line-height:1.55"),
        r(".term .p", "color:var(--accent)"), r(".term .ok", "color:var(--ok)"),
        r(".term .warn2", "color:var(--warn)"), r(".term .err", "color:var(--bad)"),
        r(".term .dim", "color:var(--muted)"),
        r(".term .cursor", "display:inline-block;width:8px;height:14px;background:var(--accent);"
          "vertical-align:-2px;animation:v9blink 1.05s steps(1) infinite"),
        "@keyframes v9blink{0%,49%{opacity:1}50%,100%{opacity:0}}",
        r(".termline", "display:flex;gap:8px;align-items:center;margin-top:6px"),
        r(".termline input", "flex:1;background:rgba(4,7,13,.7);border:1px solid var(--border);"
          "color:var(--accent);font-family:inherit;font-size:13px;border-radius:var(--r-sm);"
          "padding:7px 10px"),
        # 对话（APP 多功能对话面板）
        r(".chatwrap", "display:grid;grid-template-columns:240px 1fr;gap:12px"),
        r(".sesslist", "max-height:62vh;overflow:auto;padding-right:4px"),
        r(".sess", "padding:8px 10px;border-radius:var(--r-md);border:1px solid var(--border);"
          "margin-bottom:6px;cursor:pointer;background:rgba(255,255,255,.02)"),
        r(".sess.on", "border-color:transparent;"
          "background:linear-gradient(180deg,rgba(57,208,255,.18),rgba(124,92,255,.14));"
          "box-shadow:var(--glow-accent)"),
        r(".msgs", "max-height:58vh;overflow:auto;padding:6px 2px"),
        r(".bubble", "box-shadow:var(--sh2-d1);backdrop-filter:blur(6px)"),
        r(".bubble.me", "box-shadow:var(--glow-accent)"),
        r(".role", "font-size:11px;color:var(--muted);margin:10px 0 2px"),
        # 分段控件（模式切换）
        r(".seg", "display:inline-flex;gap:2px;padding:3px;border-radius:var(--r-pill);"
          "border:1px solid var(--border);background:rgba(4,7,13,.6)"),
        r(".seg button", "min-height:28px;padding:0 12px;border:0;border-radius:var(--r-pill);"
          "background:transparent;box-shadow:none"),
        r(".seg button.on", "background:linear-gradient(180deg,var(--accent),var(--accent_2));"
          "color:var(--on_accent)"),
        # 状态点
        r(".dot", "display:inline-block;width:8px;height:8px;border-radius:50%;"
          "box-shadow:0 0 8px currentColor"),
        "@media (prefers-reduced-motion:reduce){"
        "body::before{animation:none}"
        ".card:hover,[class*=card]:hover{transform:none}"
        ".term .cursor{animation:none}}",
    ]
    return "\n".join(out)


def nav_html(current: str = "/", extra: tuple = ()) -> str:
    """统一导航（全站内置、**不做外跳**）：12 个按键全部设计好 —— 图标 + 分组 + 当前页高亮。

    按键来源是 core.page_registry.PAGES（页面注册表）：按键与页面/资源是双向绑定的，
    改动注册表即改动导航，不会出现"导航有按钮但页面不存在"的错位。
    """
    try:
        from core.page_registry import PAGES
        items = [(p.路由, f"{p.图标} {p.标题}", p.分组) for p in PAGES]
    except Exception:                                     # noqa: BLE001
        items = [(h, lbl, "") for h, lbl in
                 (("/", "总控台"), ("/command", "AI 指挥中心"), ("/docs", "API 文档"))]
    groups: dict = {}
    for href, label, grp in items:
        groups.setdefault(grp or "其他", []).append((href, label))
    parts = ['<nav class=top aria-label="主站导航">'
             '<a class=brand href="/" title="GBT小土豆V9"><img src="/logo.png" '
             'alt="GBT小土豆V9 徽标" width="26" height="26"><b>GBT小土豆V9</b></a>'
             '<div class=navbtns role=menubar>']
    for grp, entries in groups.items():
        if grp:
            parts.append(f'<span class=navgrp>{grp}</span>')
        for href, label in entries:
            cls = " class='navbtn on'" if href == current else " class=navbtn"
            parts.append(f'<a href="{href}"{cls} role=menuitem>{label}</a>')
    parts.append("</div>")
    for href, label in extra:
        parts.append(f'<a class=navbtn href="{href}">{label}</a>')
    return "".join(parts) + "</nav>"


# ═══════════ ③ 术语 → 实现（由术语 Skill 交过来的意图在这里落地）═══════════
def implement(intent: str, params: dict | None = None, *, selector: str = ".card") -> dict:
    """把术语意图生成真 CSS/HTML。未知意图 → 明确拒绝（不硬编一个效果糊弄）。"""
    p = dict(params or {})
    if intent == "hover_lift_yield":
        lift = int(p.get("lift_px", 4))
        dur = int(p.get("duration_ms", TOKENS["motion"]["base"]))
        return {"css": f"""{selector}{{transition:transform {dur}ms ease,box-shadow {dur}ms ease}}
{selector}:hover{{transform:translateY(-{lift}px);
  box-shadow:0 10px 26px rgba(0,0,0,.45);z-index:{TOKENS['z']['sticky']}}}
/* 邻位让位：用 gap 过渡而不是撑开邻位（撑开会抖） */
.row{{transition:gap {dur}ms ease}} .row:has({selector}:hover){{gap:calc(var(--s3,16px) + {lift}px)}}""",
                "html": "", "notes": "抬起用 transform（不触发重排）；让位用 row 的 gap 过渡",
                "tokens_used": ["motion.base", "z.sticky", "space"]}
    if intent == "hover_outline":
        w = int(p.get("width", 1))
        return {"css": f"{selector}:hover{{outline:{w}px solid var(--accent);"
                       "outline-offset:2px}}", "html": "", "notes": "描边不占位、不改尺寸",
                "tokens_used": ["color.accent"]}
    if intent == "empty_state_honest":
        return {"html": ('<div class="card muted" role="status">'
                         '暂时没有数据 —— 说明：{原因}（不是 0，是不确定）</div>'),
                "css": "", "notes": "空态必须写原因（沿用全仓「不报假 0」的口径）",
                "tokens_used": ["color.muted"]}
    if intent == "loading_progress":
        return {"html": '<div class="progress" role="progressbar"><i style="width:40%"></i></div>',
                "css": "", "notes": "等待必须有可见进度，不要静默",
                "tokens_used": ["color.accent"]}
    if intent == "read_snapshot":
        return {"html": '<div class="card"><b>只读读数</b><div id=snap class=muted>加载中…</div></div>',
                "css": "", "notes": "读 body_read_snapshots，stale 时必须说「无法确认」",
                "tokens_used": []}
    return {"error": f"未知意图：{intent}", "hint": "先过术语 Skill 的 translate()"}


# ═══════════ ④ 审美自检（可校验，不靠主观）═══════════
def audit(css: str = "", html: str = "") -> dict:
    """检查：颜色是否走令牌、间距是否在刻度、层级是否 ≤3、对比度是否达标。"""
    issues: list = []
    raw_hex = re.findall(r"#[0-9a-fA-F]{6}", css + html)
    if raw_hex:
        issues.append({"level": "warn", "kind": "hardcoded_color",
                       "detail": f"写死颜色 {sorted(set(raw_hex))[:6]}（应从令牌取 var(--…)）"})
    for m in re.finditer(r"(padding|margin|gap)\s*:\s*(\d+)px", css):
        v = int(m.group(2))
        if v not in TOKENS["space"] and v % 4 != 0:
            issues.append({"level": "warn", "kind": "off_scale_spacing",
                           "detail": f"{m.group(1)}:{v}px 不在刻度 {TOKENS['space']}"})
    zs = [int(z) for z in re.findall(r"z-index\s*:\s*(\d+)", css)]
    if len(set(zs)) > 3:
        issues.append({"level": "warn", "kind": "too_many_z_layers",
                       "detail": f"层级 {sorted(set(zs))} 超过 3 层，容易打架"})
    ratio_body = contrast_ratio(_c("text"), _c("bg"))
    ratio_muted = contrast_ratio(_c("muted"), _c("surface"))
    if ratio_body < CONTRAST_MIN["body"]:
        issues.append({"level": "bad", "kind": "contrast",
                       "detail": f"正文对比度 {ratio_body} < {CONTRAST_MIN['body']}"})
    if ratio_muted < CONTRAST_MIN["body"]:
        issues.append({"level": "warn", "kind": "contrast_muted",
                       "detail": f"次要文字对比度 {ratio_muted} 偏低（{CONTRAST_MIN['body']} 为达标线）"})
    return {"ok": not [i for i in issues if i["level"] == "bad"], "issues": issues,
            "contrast": {"text_on_bg": ratio_body, "muted_on_surface": ratio_muted,
                         "min_body": CONTRAST_MIN["body"]}}


@dataclass
class Page:
    title: str
    body: str
    current: str = "/"
    extra_css: str = ""
    extra_js: str = ""
    dock: bool = True

    def render(self) -> str:
        dock = ('<div class=dock><input id=aiask placeholder="问一句，她照实回答（不编数）">'
                '<button class="btn primary" onclick="aiAsk()">问</button>'
                '<span id=aistate class=muted></span></div>'
                if self.dock else "")
        js = ("""async function aiAsk(){
  var q=document.getElementById('aiask').value.trim(); if(!q) return;
  var st=document.getElementById('aistate'); st.textContent='…';
  try{ var r=await fetch('/api/ai/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:q})}); var d=await r.json();
    st.innerHTML = d.say ? ('<span class=ok>'+d.say.slice(0,60)+'</span>') : '<span class=warn>没懂</span>';
    document.getElementById('aiout') && (document.getElementById('aiout').textContent =
      JSON.stringify(d,null,1));
  }catch(e){ st.innerHTML='<span class=bad>失败</span>'; }
}
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&document.activeElement.id==='aiask')aiAsk();});"""
              if self.dock else "")          # 没有停靠坞就不发这段死代码
        return (f"<!doctype html><html lang=zh><meta charset=utf-8>"
                f"<link rel=icon type=image/x-icon href=/favicon.ico>"
                f"<link rel=apple-touch-icon href=/logo.png><title>{self.title}</title>\n"
                f"<style>{base_css()}{self.extra_css}</style>\n"
                f"{nav_html(self.current)}\n<h1>{self.title}</h1>\n{self.body}\n{dock}\n"
                f"<script>{js}\n{self.extra_js}</script>\n{page_control_js()}\n</html>")


def skin_css() -> str:
    """统一「皮」：把城市未来科技感套到**既有老页面**上（不改它们结构，只覆盖视觉）。

    注入位置放在文档尾部，确保压过老页面自己 <head> 里的样式（同优先级后者胜）。
    """
    return f"""<style id=v9-skin>{css_vars()}
body{{color:var(--text);
{_HORIZON}
  background-attachment:fixed;min-height:100vh;
  font:var(--fs-body)/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),
    linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:52px 52px;opacity:.45;
  mask-image:radial-gradient(1200px 680px at 50% -8%,#000 26%,transparent 84%);
  -webkit-mask-image:radial-gradient(1200px 680px at 50% -8%,#000 26%,transparent 84%)}}
main,div,section,table,pre{{position:relative;z-index:1}}
.wrap,.container,main{{max-width:1240px;margin:0 auto}}
h1{{background:linear-gradient(92deg,var(--text),var(--accent) 55%,var(--accent-2));
  -webkit-background-clip:text;background-clip:text;color:transparent}}
h2{{border-left:3px solid var(--accent);padding-left:var(--s2,8px)}}
.card,[class*="card"],.panel,.box{{border-radius:var(--r-lg)!important;
  border:1px solid rgba(255,255,255,.075)!important;
  background:linear-gradient(180deg,rgba(255,255,255,.055),rgba(255,255,255,.012))!important;
  backdrop-filter:blur(10px) saturate(125%);box-shadow:var(--sh-d2);
  transition:transform var(--t-base) ease,box-shadow var(--t-base) ease}}
.card:hover,[class*="card"]:hover{{transform:perspective(900px) rotateX(1deg) rotateY(-1deg)
  translateY(-2px);box-shadow:var(--sh-d3)}}
table{{width:100%;border-collapse:collapse}}
th,td{{border-bottom:1px solid var(--border)}}
tbody tr:hover td{{background:rgba(57,208,255,.06)}}
pre{{background:rgba(4,7,13,.6)!important;border:1px solid var(--border);
  border-radius:var(--r-md)}}
/* ── 按键归一：老页面各自写过 button{{}}（8 种长相、3 种悬停）。这里在文档尾部
   统一压成同一套；元素选择器同优先级后者胜，类名选择器用 !important 保证压得住。 ── */
button,input,select,textarea{{font:inherit}}
button{{display:inline-flex;align-items:center;justify-content:center;gap:6px;
  min-height:34px;box-sizing:border-box;line-height:1;
  background:linear-gradient(180deg,var(--surface_2),var(--surface))!important;
  color:var(--text)!important;border:1px solid var(--border)!important;
  border-radius:var(--r-sm)!important;padding:0 14px!important;
  font-size:var(--fs-small)!important;cursor:pointer;white-space:nowrap;
  box-shadow:var(--sh-d1);margin:0;
  transition:transform var(--t-fast) ease,box-shadow var(--t-fast) ease,
    color var(--t-fast) ease,border-color var(--t-fast) ease}}
button:hover{{color:var(--accent)!important;border-color:var(--hairline)!important;
  box-shadow:var(--sh-d2)}}
button:active{{transform:translateY(1px);box-shadow:none}}
button:disabled{{opacity:.45;cursor:not-allowed;transform:none}}
button.ghost,button.ghost:hover{{background:transparent!important;box-shadow:none}}
button.ghost{{color:var(--muted)!important}}
button.prim,button.primary,button.on{{color:var(--on_accent)!important;
  border-color:transparent!important;
  background:linear-gradient(180deg,var(--accent),var(--accent_2))!important;
  box-shadow:var(--glow-accent)}}
select{{min-height:34px!important;border-radius:var(--r-sm)!important;
  background:linear-gradient(180deg,var(--surface_2),var(--surface));
  color:var(--text);border:1px solid var(--border);padding:0 10px}}
input,textarea{{border-radius:var(--r-sm)!important;color:var(--text);
  background:rgba(4,7,13,.6);border:1px solid var(--border)}}
/* 旧页的标签样式（.pill/.kind/.plug）与开关芯片，统一到同一口径 */
.pill,.kind{{border-radius:var(--r-pill)!important;padding:2px 8px!important;
  border:1px solid var(--border);background:rgba(255,255,255,.03);
  font-size:var(--fs-small);display:inline-block}}
.plug{{border-radius:var(--r-pill)!important;border:1px solid var(--border);
  background:rgba(255,255,255,.03)}}
/* 导航按键：等高对齐，当前页霓虹底现在真的能生效（--accent_2 已补定义） */
nav.top a.navbtn{{min-height:34px;box-sizing:border-box;justify-content:center}}
::-webkit-scrollbar{{width:10px;height:10px}}
::-webkit-scrollbar-track{{background:var(--bg-deep)}}
::-webkit-scrollbar-thumb{{background:linear-gradient(180deg,var(--surface_3),var(--surface_2));
  border-radius:var(--r-pill);border:2px solid var(--bg-deep)}}
@media (prefers-reduced-motion:reduce){{.card:hover,[class*="card"]:hover{{transform:none}}}}
{_v2_css(force=True)}
</style>"""


def page_control_js() -> str:
    """她操作页面的**执行器**：注入到每一个页面，收到指令就在本页动手并回传结果。

    主人要求："数字人 AI 可以操控任何一个页面，只要用户同意，没有她不能操控的东西。"
      · 指令来自 /api/control/next（服务端队列）；执行完 POST /api/control/result；
      · 动作白名单：go/click/fill/read/press —— 没有 eval、没有任意脚本；
      · 页面上**看得见她正在做什么**（顶部一条状态条），不做暗箱操作；
      · 未授权时服务端不会下发指令，这里也就永远收不到东西。
    """
    return """<div id=v9ctl style="position:fixed;left:50%;transform:translateX(-50%);top:6px;z-index:99999;
  display:none;padding:6px 14px;border-radius:var(--r-pill);font-size:12px;
  background:linear-gradient(180deg,rgba(57,208,255,.22),rgba(124,92,255,.18));
  border:1px solid var(--neon-edge);box-shadow:var(--glow-accent);color:var(--text)"></div>
<script>
function v9ctlSay(t){ var e=document.getElementById('v9ctl'); if(!e) return;
  e.style.display='block'; e.textContent='她在操作：'+t; }
function v9ctlHide(){ var e=document.getElementById('v9ctl'); if(e) e.style.display='none'; }
function v9find(target){
  // 先按选择器找，找不到按可见文字找（用户口中说的"那个按钮"）
  try{ var el=document.querySelector(target); if(el) return el; }catch(e){}
  var want=String(target||'').trim(); if(!want) return null;
  var all=document.querySelectorAll('button,a,input,select,textarea,[onclick]');
  for(var i=0;i<all.length;i++){
    var t=(all[i].textContent||all[i].value||all[i].placeholder||'').trim();
    if(t && (t===want || t.indexOf(want)>=0)) return all[i];
  }
  return null;
}
async function v9exec(c){
  var a=c.action, g=c.args||{}, ok=false, detail='';
  try{
    if(a==='go'){ v9ctlSay('前往 '+g.path); location.href=g.path; ok=true; detail='已跳转 '+g.path; }
    else if(a==='click'){
      var el=v9find(g.target);
      if(el){ v9ctlSay('点击 '+(g.target)); el.click(); ok=true; detail='已点 '+g.target; }
      else detail='没找到「'+g.target+'」';
    }
    else if(a==='fill'){
      var el2=v9find(g.target)||document.querySelector('input,textarea');
      if(el2){ v9ctlSay('填写 '+g.target); el2.value=g.value;
        el2.dispatchEvent(new Event('input',{bubbles:true}));
        el2.dispatchEvent(new Event('change',{bubbles:true})); ok=true; detail='已填 '+g.value.slice(0,40); }
      else detail='没找到输入框';
    }
    else if(a==='read'){
      v9ctlSay('读取本页'); ok=true;
      detail=(document.querySelector('h1,h2')?document.querySelector('h1,h2').textContent:'')+' | '+
        (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,600);
    }
    else if(a==='press'){
      document.dispatchEvent(new KeyboardEvent('keydown',{key:g.key,bubbles:true})); ok=true; detail='已发送 '+g.key;
    }
    else detail='未知动作';
  }catch(e){ detail='执行出错：'+(e&&e.message||e); }
  try{ await fetch('/api/control/result',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({id:c.id,ok:ok,detail:detail,page:location.pathname})}); }catch(e){}
  setTimeout(v9ctlHide, 1600);
  return ok;
}
let V9CTL_WAIT=1200;
async function v9poll(){
  // ★真机教训：多个页面同时开着、后台标签页也在轮询 → 单 worker 被打满，其它请求全排队。
  //   所以：后台标签页**不轮询**；空闲时**退避**到 5 秒，只有真收到指令才回到 1.2 秒。
  if(document.hidden) return;
  try{
    var d=await (await fetch('/api/control/next')).json();
    var n=(d && d.commands && d.commands.length)||0;
    if(n){ V9CTL_WAIT=1200; for(var i=0;i<n;i++){ await v9exec(d.commands[i]); } }
    else { V9CTL_WAIT=Math.min(5000, Math.round(V9CTL_WAIT*1.35)); }
  }catch(e){ V9CTL_WAIT=Math.min(6000, Math.round(V9CTL_WAIT*1.5)); }
  clearTimeout(window.__v9ctlT);
  window.__v9ctlT=setTimeout(v9poll, V9CTL_WAIT);
}
document.addEventListener('visibilitychange', function(){
  if(!document.hidden){ V9CTL_WAIT=1200; clearTimeout(window.__v9ctlT); v9poll(); }
});
setTimeout(v9poll, 1200);
</script>"""


def inject(html: str, current: str = "/") -> str:
    """把统一导航 + AI 停靠坞**注入**已有页面（不改原页面结构，纯追加）。

    这是"AI 穿透所有页面"的落地方式：老页面不用重写，注入后每页都能直接问一句。
    """
    if not isinstance(html, str) or not html.strip():
        return html
    out = html
    # 城市未来科技感的统一皮：注入到文档尾部，压过老页面自己的样式
    if "v9-skin" not in out:
        skin = skin_css()
        if "</body>" in out:
            out = out.replace("</body>", skin + "\n</body>", 1)
        else:
            out = out + skin
    # 全站 favicon + 品牌图标（老页面没有 Logo 位，这里统一补上）
    if "favicon.ico" not in out:
        links = ('<link rel=icon type=image/x-icon href=/favicon.ico>'
                 '<link rel=apple-touch-icon href=/logo.png>')
        if "<head>" in out:
            out = out.replace("<head>", "<head>" + links, 1)
        elif "<meta charset" in out:
            k = out.find(">", out.find("<meta charset")) + 1
            out = out[:k] + links + out[k:]
        else:
            out = links + out
    if "class=top" not in out and "<nav class=top>" not in out:
        nav = nav_html(current)
        if "</h2>" in out:                                  # 有标题就插在标题后
            out = out.replace("</h2>", "</h2>\n" + nav, 1)
        elif "<body" in out:
            i = out.find(">", out.find("<body")) + 1
            out = out[:i] + "\n" + nav + out[i:]
        else:
            out = nav + out
    if "id=v9ctl" not in out:            # 她操作页面的执行器：每个页面都能被她动手
        ctl = page_control_js()
        out = out.replace("</body>", ctl + "\n</body>", 1) if "</body>" in out else out + ctl
    if 'id=aiask' not in out and "id=\"aiask\"" not in out:
        # 紧凑停靠坞 + 可展开结果面板：答复**不铺在页面上**（之前那一版把整段话印进正文了）
        dock = ('<div class=dock id=aidock>'
                '<input id=aiask placeholder="问一句（回车）">'
                '<button class="btn primary" onclick="aiAsk()">问</button>'
                '<span id=aistate class=muted></span>'
                '<button class=btn onclick="aiToggle()" id=aitoggle>详情</button></div>'
                '<div id=aioutwrap style="display:none;position:fixed;right:24px;bottom:calc(24px + 46px);'
                'z-index:1000;width:min(52vw,560px);max-height:46vh;overflow:auto;'
                'background:var(--surface);border:1px solid var(--border);border-radius:var(--r-md);'
                'padding:12px"><pre id=aiout class=muted style="margin:0;white-space:pre-wrap;'
                'font-size:12px"></pre></div>')
        js = """<script>
function aiToggle(){var w=document.getElementById('aioutwrap');
  w.style.display=(w.style.display==='none')?'block':'none';}
async function aiAsk(){
  var q=document.getElementById('aiask').value.trim(); if(!q) return;
  var st=document.getElementById('aistate'); st.textContent='…';
  try{
    var r=await fetch('/api/ai/ask',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({text:q})}); var d=await r.json();
    // 停靠坞只显示**一句**（截断），完整结果进可展开面板
    st.innerHTML = d.say ? '<span class=ok>'+String(d.say).slice(0,42)+
      (String(d.say).length>42?'…':'')+'</span>' : '<span class=warn>没懂</span>';
    document.getElementById('aiout').textContent = JSON.stringify(d,null,1);
    if(d.say){try{var u=new SpeechSynthesisUtterance(d.say);u.lang='zh-TW';u.rate=0.9;
      u.pitch=1.05;speechSynthesis.speak(u);}catch(e){}}
  }catch(e){ st.innerHTML='<span class=bad>失败</span>'; }
}
document.addEventListener('keydown',function(e){
  if(e.key==='Enter'&&document.activeElement&&document.activeElement.id==='aiask')aiAsk();});
</script>"""
        if "</body>" in out:
            out = out.replace("</body>", dock + "\n" + js + "\n</body>", 1)
        else:
            out = out + dock + js
    # ★每个页面都带上她（主人 2026-10-08：「数字人可以出现在任意页面协助用户」）。
    #   同一处还带「首次启动先配密钥」的闸 —— 顺序就是主人要的那句：先配密钥，她才出现。
    #   注：这里从 skills 反向 import panel 只为少改 20 个页面；panel.dh_companion 不 import
    #   本模块，所以不成环。挂了也不许拖垮页面渲染（包在 try 里）。
    try:
        from panel.dh_companion import widget as _dh_widget
        _w = _dh_widget()
        if "id=dhc" not in out:
            out = (out.replace("</body>", _w + "\n</body>", 1)
                   if "</body>" in out else out + _w)
    except Exception as e:
        _swallow(__file__, e)
    # ★ 项目最底下的署名（主人令 2026-10-10：品牌 + Logo + 开发者：自由的风）
    if "v9-credit" not in out:
        _credit = (
            '<div id="v9-credit" style="max-width:1000px;margin:28px auto 20px;padding:16px 18px;'
            'border-top:1px solid #1e2635;color:#8fb2d9;text-align:center;'
            'font:12.5px/1.95 system-ui;line-height:1.95">'
            '<img src="/logo.png" alt="GBT小土豆V9" style="height:44px;vertical-align:middle;'
            'margin-right:10px;filter:drop-shadow(0 0 10px #2a6cb055)">'
            '<b style="color:#a5d6ff;font-size:15px">GBT小土豆V9</b>'
            '<div style="margin-top:8px">开发者：自由的风</div>'
            '<div>个人独立开发者，喜欢研究和设计智能体，希望有专业人士指点，谢谢。</div>'
            '<div>我不懂代码，但我能解决代码问题，也能设计出让市面上那些中间商「送温暖」的能力。</div>'
            '</div>')
        out = (out.replace("</body>", _credit + "\n</body>", 1)
               if "</body>" in out else out + _credit)
    if 'dh/overlay.js' not in out:
        _ov = "<script src='/api/dh/overlay.js' defer></script>"
        out = (out.replace('</body>', _ov + chr(10) + '</body>', 1) if '</body>' in out else out + _ov)
    return out


__all__ = ["TOKENS", "css_vars", "base_css", "nav_html", "implement", "audit",
           "contrast_ratio", "Page", "inject", "CONTRAST_MIN"]
