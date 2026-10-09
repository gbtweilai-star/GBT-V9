# core/stop_policy.py —— 停机闸（V9）：只有两种理由能停
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09，两次强调）：「V9框架里面设定好：要么部署生产上线停下，要么有必须需要用户
#   操作的可以停下，其他的停下干嘛？」
#
# 规则（硬）：
#   ✅ 合法停下 A：**部署/上线完成** —— 必须带证据（交付闸全绿读数 / 已推送的提交号 / 上线读数）
#   ✅ 合法停下 B：**真需要用户操作** —— 只认两类：① 主人**自己的身份证/账户登入**
#                 ② 六类危险动作的**授权**（转账/支付/删除/对外发布/隐私载体/不可回滚），且要写明是哪一类
#   ❌ 其他一律**禁止停下**：轮次要结束了、先歇一会、汇报一下、等你确认、等你有空、"先这样吧"…
# 禁止停下时**必须给出下一件该做的事**（从在办清单里取），所以不存在"停在这里没事干"。
from __future__ import annotations
from core.swallow import swallow as _swallow

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "stop_policy.jsonl"

LEGAL_KINDS = {
    "部署完成": "部署/上线完成（需证据：交付闸全绿 / 已推送提交号 / 上线读数）",
    "需要用户操作": "真需要用户操作（仅限：主人身份/账户登入，或六类危险动作授权，且须写明类别）",
}
DANGER_SIX = ("转账", "支付", "删除", "对外发布", "隐私载体", "不可回滚")
IDENTITY_MARKS = ("身份证", "账户登入", "账号登入", "登录 ID", "实名")


def open_work() -> list:
    """在办清单（机器可读）：从交付闸/排查/台账汇总"还没做完"的。"""
    todo = []
    try:
        from core import delivery_gate as DG
        st = DG.status(1)
        last = (st.get("最近") or [{}])[-1]
        if last and last.get("禁止交付"):
            todo.append({"来源": "交付闸", "事": "禁止交付 %s 个能力" % last.get("禁止交付"),
                         "下一步": "逐个修到它的独立验收器通过"})
    except Exception as e:
        _swallow(__file__, e)
    try:
        rows = []
        led = ROOT / "state" / "no_begging_audit.jsonl"
        if led.is_file():
            rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()[-1:]]
        if rows and (rows[-1].get("黄数") or 0) > 1:
            todo.append({"来源": "禁求助闸", "事": "求助文案剩 %s 条" % rows[-1]["黄数"],
                         "下一步": "逐条改写为「已登记待授权」口径"})
    except Exception as e:
        _swallow(__file__, e)
    # 已知在办（本仓现状，如实列）
    todo += [
        {"来源": "本仓现状", "事": "静默吞异常剩 12 处", "下一步": "AST 精确改造到 0"},
        {"来源": "本仓现状", "事": "开源仓推送被 403（凭据非仓库主）", "下一步": "换凭据后推送；不阻塞其它"},
        {"来源": "本仓现状", "事": "docker demo 未验证（守护未起）", "下一步": "守护起来后跑 demo profile 收读数"},
    ]
    return todo


def judge(kind: str, *, evidence: str = "", detail: str = "", who: str = "AI") -> dict:
    """判：这一次停下合法吗？不合法就给出下一件该做的事。"""
    kind = (kind or "").strip()
    ok = False
    why = ""
    if kind == "部署完成":
        ok = bool(evidence)
        why = "部署完成且带证据：%s" % evidence[:80] if ok else "声称部署完成但**没有证据**⇒ 不算"
    elif kind == "需要用户操作":
        has_id = any(m in detail for m in IDENTITY_MARKS)
        has_danger = any(d in detail for d in DANGER_SIX)
        ok = has_id or has_danger
        why = ("真需要用户操作：%s" % detail[:80]) if ok else \
            "声称需要用户操作，但既非身份证/账户登入、也不属六类危险授权 ⇒ **不算**（能自己办的必须自己办）"
    else:
        ok = False
        why = "「%s」不是合法停下理由：只允许 部署完成 / 真需要用户操作" % (kind or "(空)")
    todo = open_work()
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "谁": who, "理由": kind, "证据": evidence[:160],
           "细节": detail[:160], "放行": ok, "判": why, "在办数": len(todo),
           "下一件": (todo[0] if (todo and not ok) else None)}
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))
    return rec


def must_continue() -> dict:
    """给流程用：现在能不能停？不能停就把下一件甩出来。"""
    todo = open_work()
    return {"能停": not todo, "在办数": len(todo),
            "下一件": todo[0] if todo else None,
            "口径": "在办清单非空 ⇒ 不许停；只有 部署完成 / 真需要用户操作 才放行"}


def status(limit: int = 4) -> dict:
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return {"合法停下理由": LEGAL_KINDS, "六类危险": DANGER_SIX, "最近判定": rows,
            "在办": open_work(), "口径": "其它理由一律禁止停下，并给出下一件"}


__all__ = ["LEGAL_KINDS", "DANGER_SIX", "IDENTITY_MARKS", "open_work", "judge", "must_continue", "completeness", "may_stop", "USER_STOP_MARKS",
           "status", "LEDGER"]


# ② 主人令（2026-10-09 最终）：机制改成「看整体没完善就不停工，除非用户要求停止」
USER_STOP_MARKS = ("用户要求停止", "主人要求停止", "你说停", "用户喊停")


def _count_silent() -> int:
    """全仓静默吞异常计数（AST 口径）。"""
    import ast
    n = 0
    for f in ROOT.rglob('*.py'):
        sp = str(f)
        if any(x in sp for x in ('__pycache__', 'desktop', '.git', 'release', 'node_modules', '_archive', 'state')):
            continue
        try:
            t = ast.parse(f.read_text(encoding='utf-8', errors='replace'))
        except Exception:
            continue
        for nd in ast.walk(t):
            if isinstance(nd, ast.ExceptHandler):
                b = [x for x in nd.body if not (isinstance(x, ast.Expr) and isinstance(getattr(x, 'value', None), ast.Constant) and isinstance(x.value.value, str))]
                if len(b) == 1 and isinstance(b[0], ast.Pass):
                    n += 1
    return n


def completeness() -> dict:
    """整体完善度（机器可判）：交付闸全绿 + 禁求助红 0 + 静默 0 + 已知在办清零。"""
    gaps = []
    try:
        from core import delivery_gate as DG
        last = (DG.status(1).get('最近') or [{}])[-1]
        bad = last.get('禁止交付') if last else None
        if bad is None:
            gaps.append({'缺口': '交付闸还没跑过', '下一步': '跑一遍 DG.audit()'})
        elif bad:
            gaps.append({'缺口': '交付闸有 %s 个能力禁交' % bad, '下一步': '逐个修到独立验收器通过'})
    except Exception as e:
        gaps.append({'缺口': '交付闸读不到: %s' % type(e).__name__, '下一步': '修导入'})
    try:
        from core import no_begging as NB
        r = NB.scan()
        if r['红数']:
            gaps.append({'缺口': '禁求助闸红 %s 处' % r['红数'], '下一步': '改成自动降级/登记'})
    except Exception as e:
        gaps.append({'缺口': '禁求助闸读不到: %s' % type(e).__name__, '下一步': '修导入'})
    n_sil = _count_silent()
    if n_sil:
        gaps.append({'缺口': '静默吞异常 %s 处' % n_sil, '下一步': 'AST 精确改造'})
    try:
        from core import modular_deploy as MD
        cr = MD.closure_rate()
        if cr['未闭环']:
            gaps.append({'缺口': '模块闭环率 %s%%（未闭环 %s 个）' % (cr['闭环率'], cr['未闭环']),
                         '下一步': '逐个模块跑通它的独立验收器'})
    except Exception as e:
        gaps.append({'缺口': '模块闭环率读不到: %s' % type(e).__name__, '下一步': '修导入'})
    try:
        from core import senses_gate as SG
        bs = SG.blind_spots()
        if bs['缝隙数']:
            gaps.append({'缺口': '瞎子缝 %s 处（%s）' % (bs['缝隙数'], '、'.join(bs['瞎子缝'])),
                         '下一步': '把眼钉进这些动手口'})
    except Exception as e:
        gaps.append({'缺口': '瞎子缝扫描失败: %s' % type(e).__name__, '下一步': '修导入'})
    try:
        from core import file_binding as FB
        cov = FB.coverage()
        if cov['未绑定数']:
            gaps.append({'缺口': '绑定盲区 %s 个（覆盖率 %s%%）' % (cov['未绑定数'], cov['覆盖率']),
                         '下一步': 'bind_all() 重绑并清陈旧'})
    except Exception as e:
        gaps.append({'缺口': '绑定覆盖读不到: %s' % type(e).__name__, '下一步': '修导入'})
    known = ROOT / 'state' / 'open_items.json'
    if known.is_file():
        try:
            import json as _j
            for it in _j.loads(known.read_text(encoding='utf-8')):
                gaps.append(it)
        except Exception as e:
            import sys as _s
            print('[stop_policy] 读 open_items 失败: %s' % type(e).__name__, file=_s.stderr)
    return {'整体完善': not gaps, '缺口数': len(gaps), '缺口': gaps,
            '口径': '整体没完善就不许停工；只有 部署完成(带证据) / 真需用户操作 / 用户要求停止 才放行'}


def may_stop(reason_kind: str = '', *, evidence: str = '', detail: str = '') -> dict:
    """停工总判：用户喊停最高优先；否则看整体完善度；再看理由合法性。"""
    if reason_kind and any(m in (reason_kind + detail) for m in USER_STOP_MARKS):
        return {'能停': True, '依据': '用户要求停止（最高优先）', '整体': completeness()}
    c = completeness()
    if not c['整体完善']:
        return {'能停': False, '依据': '整体未完善（缺口 %s 项）' % c['缺口数'],
                '缺口': c['缺口'][:5], '下一件': c['缺口'][0] if c['缺口'] else None}
    r = judge(reason_kind, evidence=evidence, detail=detail) if reason_kind else {'放行': False, '判': '没给理由'}
    return {'能停': bool(r.get('放行')), '依据': r.get('判', ''), '整体': c}
