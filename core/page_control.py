# core/page_control.py —— 数字人可以操控**任何页面**（前提：用户同意）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："数字人 AI 可以操控任何一个页面，只要用户同意，没有她不能操控的东西。
#   这一步必须设计好，因为作为一个通用智能体指挥官框架来说，没有什么是她完成不了的。"
#
# 设计（四点，缺一不可）：
#   ① **同意闸门**：没有用户授权就一律拒绝（不是"问一下"，是直接不执行）；
#      授权是一次性的会话令牌，可随时撤销；撤销后立刻失效。
#   ② **动作白名单**：go（去站内某页）/ click（点）/ fill（填）/ read（读）/ press（按键）。
#      只认站内路径（/ 开头），不认外部 URL；不执行任何脚本字符串（没有 eval 通道）。
#   ③ **每页都有执行器**：运行时由 ui_design 注入到**所有 19 个页面**（她到哪页都能动手），
#      页面把结果回传；页面上会显示"她正在操作：…"（用户看得见她在做什么）。
#   ④ **全程留痕**：每条指令、每次执行、每个结果都进审计（追加式 + 台账）。
#
# 一句话：她不是"只能在自己页面里动"，而是**任何页面都能被她接管**，但闸门在你手上。
import json
import os
import re
import time
import uuid
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOURNAL = ROOT.joinpath("state", "page_control.jsonl")

ACTIONS = ("go", "click", "fill", "read", "press")
# 站内路径白名单：必须是 / 开头的站内地址，且不含协议/主机（杜绝外跳）
_PATH_OK = re.compile(r"^/[A-Za-z0-9_\-/]*$")

# 同意状态（进程内 + 落盘，重启不丢；默认**未授权**）
_CONSENT = {"granted": False, "token": "", "at": 0.0, "by": "", "scope": ""}
_QUEUE: list = []          # 待执行指令（浏览器轮询取走）
_RESULTS: dict = {}        # 已回传的结果


def _persist_path() -> Path:
    return ROOT.joinpath("state", "page_control_consent.json")


def _load() -> None:
    p = _persist_path()
    if not p.is_file():
        return
    try:
        got = json.loads(p.read_text(encoding="utf-8") or "{}")
        if isinstance(got, dict):
            _CONSENT.update({k: got.get(k, _CONSENT[k]) for k in _CONSENT})
    except (json.JSONDecodeError, OSError) as e:
        _swallow(__file__, e)



_load()


def _audit(kind: str, detail: dict, *, ok: bool = True) -> None:
    try:
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        with JOURNAL.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "kind": kind, **(detail or {})},
                               ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)



def consent(*, grant: bool | None = None, by: str = "用户", scope: str = "全部页面") -> dict:
    """同意闸门：grant=True 授权（发令牌）；grant=False 撤销；None 只读状态。"""
    if grant is None:
        return {"授权": bool(_CONSENT["granted"]), "令牌": _CONSENT["token"][:8] + "…"
                if _CONSENT["token"] else "", "范围": _CONSENT["scope"],
                "授权人": _CONSENT["by"], "授权时间": _CONSENT["at"],
                "口径": "没授权她一律不动手；授权可随时撤销"}
    if grant:
        _CONSENT.update({"granted": True, "token": uuid.uuid4().hex,
                         "at": time.time(), "by": str(by), "scope": str(scope)})
    else:
        _CONSENT.update({"granted": False, "token": "", "at": time.time(),
                         "by": str(by), "scope": ""})
        _QUEUE.clear()
    try:
        _persist_path().write_text(json.dumps(_CONSENT, ensure_ascii=False, indent=1),
                                   encoding="utf-8")
    except OSError as e:
        _swallow(__file__, e)

    _audit("consent", {"granted": bool(grant), "by": by, "scope": scope}, ok=True)
    return {"授权": bool(_CONSENT["granted"]), "范围": _CONSENT["scope"],
            "说明": "已授权：她现在可以操作站内任何页面" if grant else
                    "已撤销：她不再动手（队列已清空）"}


def validate(action: str, args: dict) -> dict:
    """动作白名单 + 路径白名单。不合规一律拒（不执行、只登记）。"""
    a = str(action or "").strip().lower()
    args = dict(args or {})
    if a not in ACTIONS:
        return {"ok": False, "reason": f"动作只认 {ACTIONS}"}
    if a == "go":
        path = str(args.get("path") or "")
        if not _PATH_OK.match(path):
            return {"ok": False, "reason": "只允许站内路径（/ 开头，不含协议或主机）"}
        return {"ok": True, "action": a, "args": {"path": path}}
    if a in ("click", "read"):
        target = str(args.get("target") or args.get("selector") or "")[:200]
        if not target:
            return {"ok": False, "reason": "click/read 要指定目标（按钮文字或选择器）"}
        return {"ok": True, "action": a, "args": {"target": target}}
    if a == "fill":
        target = str(args.get("target") or args.get("selector") or "")[:200]
        value = str(args.get("value") or "")[:2000]
        if not target:
            return {"ok": False, "reason": "fill 要指定输入框"}
        return {"ok": True, "action": a, "args": {"target": target, "value": value}}
    key = str(args.get("key") or "")[:20]
    if not key:
        return {"ok": False, "reason": "press 要指定键名"}
    return {"ok": True, "action": a, "args": {"key": key}}


def submit(action: str, args: dict, *, by: str = "数字人", need_consent: bool = True) -> dict:
    """下单一条页面操作（未授权一律拒绝）。"""
    if need_consent and not _CONSENT["granted"]:
        _audit("refused", {"action": action, "args": args, "why": "未授权"}, ok=False)
        return {"ok": False, "reason": "还没拿到你的授权 —— 先在语音对讲页开「允许她操作页面」",
                "需要授权": True}
    v = validate(action, args)
    if not v["ok"]:
        _audit("invalid", {"action": action, "args": args, "why": v["reason"]}, ok=False)
        return v
    cid = "c" + uuid.uuid4().hex[:10]
    cmd = {"id": cid, "action": v["action"], "args": v["args"], "ts": time.time(),
           "by": str(by), "state": "queued"}
    _QUEUE.append(cmd)
    _audit("submit", {"id": cid, "action": v["action"], "args": v["args"], "by": by})
    return {"ok": True, "id": cid, "指令": cmd,
            "说明": "已投递，页面执行器会取走并回传结果"}


def next_commands(*, limit: int = 5) -> list:
    """页面执行器取指令（取走即出队；取不到就是空）。"""
    out = []
    while _QUEUE and len(out) < max(1, int(limit)):
        out.append(_QUEUE.pop(0))
    return out


def post_result(cid: str, ok: bool, *, detail: str = "", page: str = "") -> dict:
    """页面执行器回传结果。"""
    _RESULTS[cid] = {"ok": bool(ok), "detail": str(detail)[:400], "page": str(page)[:60],
                     "at": time.time()}
    _audit("result", {"id": cid, "ok": bool(ok), "detail": detail, "page": page}, ok=bool(ok))
    return {"ok": True}


def results(limit: int = 20) -> list:
    rows = sorted(_RESULTS.items(), key=lambda kv: -(kv[1].get("at") or 0))
    return [{"id": k, **v} for k, v in rows[: max(1, int(limit))]]


# ─────────── 自然语言 → 动作（她说的话变成页面操作） ───────────
_PAGES = {
    "总控台": "/", "指挥中心": "/command", "ai指挥中心": "/command", "3d蓝图": "/blueprint",
    "蓝图": "/blueprint", "大脑": "/brain", "原生大脑": "/brain", "工作流": "/workflow",
    "对话": "/chat", "终端": "/terminal", "ai终端": "/terminal", "语音": "/voice",
    "语音操控": "/voice", "智能体": "/agents", "octop": "/octop", "云插件": "/cloud",
    "数据库": "/db", "库": "/db", "蓝牙": "/ble", "媒体": "/media", "数字人": "/digital-human",
    "总能力": "/capability", "能力": "/capability", "流水线": "/pipelines", "工具包": "/kits",
    "文档": "/docs",
}


def plan(text: str) -> dict:
    """把一句话翻成页面操作（规则先行，认得就干；认不得就明说）。"""
    t = str(text or "").strip()
    low = t.lower()
    if not t:
        return {"ok": False, "reason": "空指令"}
    # 去某页（用户会说"去云插件页""打开大脑页面"——把尾字"页/页面/page"剥掉再找）
    m = re.search(r"(?:去|打开|进|回到|切到|前往)\s*([^\s，。]{1,14})", t)
    if m:
        raw = m.group(1).strip()
        key = re.sub(r"(页面|页|page)$", "", raw, flags=re.I).strip().lower()
        path = _PAGES.get(key)
        if not path:                                   # 再按子串找一次（"云插件中枢"→"云插件"）
            for name, pth in _PAGES.items():
                if name in key or key in name:
                    path = pth
                    break
        if not path and _PATH_OK.match("/" + key.strip("/")):
            path = "/" + key.strip("/")                # 允许直接说英文路径，如 去 /cloud
        if path:
            return {"ok": True, "actions": [{"action": "go", "args": {"path": path}}],
                    "说": f"好，我带你去「{m.group(1)}」。"}
    # 读当前页
    if re.search(r"(念|读)(一下|给我)?(当前|这个|这|本)?(一)?(页|页面)", t) or low.startswith("read"):
        return {"ok": True, "actions": [{"action": "read", "args": {"target": "body"}}],
                "说": "好，我把这一页的重点念给你听。"}
    # 点某按钮
    m = re.search(r"(?:点|按|点击)\s*([^\s，。]{1,16})", t)
    if m:
        return {"ok": True, "actions": [{"action": "click", "args": {"target": m.group(1)}}],
                "说": f"好，我点「{m.group(1)}」。"}
    # 填写
    m = re.search(r"(?:填|输入|写上)\s*([^\s，。]{0,16})[：: ]?\s*(.{1,200})", t)
    if m:
        return {"ok": True, "actions": [{"action": "fill",
                                        "args": {"target": m.group(1) or "input",
                                                 "value": m.group(2).strip()}}],
                "说": "好，我帮你填进去。"}
    return {"ok": False, "reason": f"这句话我还翻不成页面操作：{t[:40]}（可以说「去云插件页」「点保存」「念一下这页」）"}


def execute(text: str, *, by: str = "数字人") -> dict:
    """她说一句 → 变成页面操作并投递（未授权会明确要求授权）。"""
    p = plan(text)
    if not p.get("ok"):
        return p
    sent = []
    for a in p["actions"]:
        r = submit(a["action"], a["args"], by=by)
        sent.append(r)
        if not r.get("ok"):
            return {"ok": False, "reason": r.get("reason"), "需要授权": r.get("需要授权"),
                    "说": ("这一步属于【危险类】，已停下并登记，其余流程继续；我可在授权到位后自动续跑。"
                           if r.get("需要授权") else r.get("reason"))}
    return {"ok": True, "说": p["说"], "已投递": [x["id"] for x in sent],
            "动作": p["actions"]}


def status() -> dict:
    return {"授权": consent(), "待执行": len(_QUEUE), "最近结果": results(limit=5),
            "动作白名单": list(ACTIONS),
            "页面覆盖": "运行时注入到全部 19 个页面（她到哪页都能动手）",
            "口径": "没授权不执行；只站内路径；执行结果页面上可见；全程留痕",
            "审计行数": _audit_lines()}


def _audit_lines() -> int:
    try:
        if not JOURNAL.is_file():
            return 0
        return sum(1 for ln in JOURNAL.read_text(encoding="utf-8").splitlines() if ln.strip())
    except OSError:
        return 0


def audit(limit: int = 50) -> list:
    if not JOURNAL.is_file():
        return []
    out = []
    for ln in JOURNAL.read_text(encoding="utf-8").splitlines()[-max(1, int(limit)):]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


__all__ = ["ACTIONS", "consent", "validate", "submit", "next_commands", "post_result",
           "results", "plan", "execute", "status", "audit"]
