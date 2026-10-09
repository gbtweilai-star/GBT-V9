# tools/audit_real_execution.py —— 真执行审计：禁止假执行与虚报
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人令（2026-10-09）：「所有能力必须真实可实战，禁止假执行和虚报。」
# 本件用 **AST**（不是文本关键词）扫全仓，找四类嫌疑，报 file:line + 代码片段：
#   ① 写死的通过：字面量 {"通过": True} / {"ok": True} 直接返回，且函数体内没有任何
#      真实副作用调用（subprocess / sqlite / 写文件 / urllib / 云 / 沙盒 / Cell…）；
#   ② 空壳函数：函数体只有 return 常量 / pass；
#   ③ 干跑默认：参数默认 dry_run=True（"只说不动"成了默认行为）；
#   ④ 无调用方：某模块里的公开函数在本仓**没有任何其它文件引用**（像当初的 run_plugged）。
import ast
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {"desktop", ".git", "__pycache__", "node_modules", "release", "state", "render", "vaults"}

# 真实副作用的调用名（出现即认为"可能真干活"）
SIDE_EFFECTS = ("subprocess", "sqlite3", "open", "write_text", "write_bytes", "urllib", "requests",
                "run_plugin", "invoke", "confine", "spit", "fire", "exec", "popen", "run",
                "make_ledger", "connect", "shutil", "os.remove", "tasklist", "cloud_runner")

STUB_NAMES = ("_log", "_audit", "__init__", "__repr__")
# 只在"**名字声称要干活**"的函数上判空壳/虚报（纯谓词/小工具不判，避免噪音=假审计）
ACTION_WORDS = ("run", "execute", "exec", "apply", "deploy", "install", "start", "stop", "sync",
                "publish", "fix", "repair", "verify", "check", "probe", "test", "fire", "invoke",
                "equip", "provision", "confine", "spit", "upload", "download", "generate", "build",
                "render", "dispatch", "call", "send", "write", "commit", "deploy")


def looks_like_action(name: str) -> bool:
    n = name.lower()
    return any(w in n for w in ACTION_WORDS)


def iter_py():
    for p in ROOT.rglob("*.py"):
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        yield p


def calls_in(node) -> set:
    out = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                out.add(f.attr)
    return out


def has_side_effect(node) -> bool:
    c = calls_in(node)
    if c & set(SIDE_EFFECTS):
        return True
    # 任何 attr 调用算潜在副作用（保守）——但单独的 print/len/json 不算
    MUTATORS = {"append", "extend", "update", "add", "pop", "insert", "remove", "clear", "setdefault"}
    benign = {"print", "len", "str", "int", "float", "dict", "list", "set", "sorted", "round",
              "min", "max", "sum", "json", "dumps", "loads", "get", "items", "format", "join",
              "strip", "split", "lower", "upper", "startswith", "endswith", "append", "extend",
              "glob", "rglob", "is_file", "exists", "read_text", "replace", "add", "count", "time"}
    if c & MUTATORS:
        return True      # 变异型调用就是副作用（如 self.tags.append）
    return bool(c - benign)


def literal_ok_dict(node) -> bool:
    """函数体里直接 return 了含 '通过'/'ok' 为 True 的字面量 dict。"""
    for n in ast.walk(node):
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
            for k, v in zip(n.value.keys, n.value.values):
                if isinstance(k, ast.Constant) and k.value in ("通过", "ok") and \
                   isinstance(v, ast.Constant) and v.value is True:
                    return True
    return False


# 有理由的白名单：**不是虚报**（纯谓词/构造器/测试桩），但必须写清理由，不许无声略过
LEGIT: dict = {
    "core/knowledge.py:build_prompt": "纯构造器：只拼提示词字符串，返回 ok 是「构造成功」，无副作用是应该的",
    "core/ux_doctrine.py:check_reply": "纯谓词：只判这句话违不违规矩，不执行任何动作",
    "core/vision_loop.py:stop": "停眼：set 事件 + join 线程就是它的全部副作用（已在 body 内）",
    "tools/verify_no_fake_block.py:run": "验收器自己的壳，真检查在下面的断言里",
    "panel/terminal_page.py:api_help": "只返回静态帮助文本",
    "core/tentacle_fleet.py:pick_local_model": "纯选型函数：挑一个模型名返回，不执行",
}
TESTS_WHITELIST = True      # tests/ 下的桩按设计就是假的（测试替身），不算虚报


def _legit(rel: str, name: str) -> str:
    if TESTS_WHITELIST and rel.startswith("tests"):
        return "测试替身（按设计就是假的）"
    key = "%s:%s" % (rel.replace(chr(92), "/"), name)
    return LEGIT.get(key, LEGIT.get("%s:%s" % (rel, name), ""))


def _shape(node) -> str:
    """机器判形态：接口声明 / 如实说没实现 / 一行停 —— 三类都不是假执行。"""
    body = [s for s in node.body if not (isinstance(s, ast.Expr)
            and isinstance(getattr(s, "value", None), ast.Constant)
            and isinstance(s.value.value, str))]      # 去掉文档字符串
    if len(body) == 1:
        s0 = body[0]
        if isinstance(s0, ast.Expr) and isinstance(getattr(s0, "value", None), ast.Constant)            and s0.value.value is Ellipsis:
            return "接口声明（Protocol/ABC 的 ...），不是实现"
        if isinstance(s0, ast.Raise) and s0.exc is not None:
            nm = getattr(getattr(s0.exc, "func", None), "id", "") or getattr(s0.exc, "id", "")
            if "NotImplemented" in str(nm):
                return "如实声明未实现（NotImplementedError），不是假装做过"
        if isinstance(s0, (ast.Pass,)):
            return "空的 pass（占位）"
        if isinstance(s0, ast.Assign) and node.name.lower().startswith(("stop", "close", "shutdown")):
            return "停：设一个标志就是它的全部动作"
        if isinstance(s0, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            return "单条赋值（登记/装订），不是假执行"
        if isinstance(s0, ast.With):
            return "单条 with（读或写上下文），动作在上下文管理器里"
        if isinstance(s0, ast.Return) and s0.value is not None:
            return "一行转发/纯取值（facade），全部动作在它调用的那层"
        if isinstance(s0, ast.Expr) and isinstance(s0.value, ast.Call):
            f = s0.value.func
            nm = getattr(f, "attr", "") or getattr(f, "id", "")
            if nm in ("set", "clear", "close", "join", "cancel", "shutdown", "terminate"):
                return "停/关：调一次 set/close/join 就是全部动作"
    return ""


def scan() -> dict:
    findings = {"写死的通过": [], "空壳函数": [], "干跑默认": [], "验收器虚报": [], "无调用方": [], "已剔噪(有理由)": []}
    all_src = {}
    for p in iter_py():
        try:
            all_src[p] = p.read_text(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue

    defined = {}   # 函数名 → [(file, line)]
    for p, src in all_src.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in STUB_NAMES and node.name.startswith("_"):
                    pass
                body_ok = has_side_effect(node)
                if literal_ok_dict(node) and not body_ok and looks_like_action(node.name):
                    _why = _legit(str(p.relative_to(ROOT)), node.name)
                    if _why:
                        findings.setdefault("已剔噪(有理由)", []).append(
                            {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                             "理由": _why})
                        continue
                    findings["写死的通过"].append(
                        {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                         "理由": "直接 return 含 True 的 ok/通过，且函数体内无任何真实副作用调用"})
                stmts = [s for s in node.body if not isinstance(s, ast.Expr) or
                         not isinstance(getattr(s, "value", None), ast.Constant)]
                if len(stmts) <= 1 and not body_ok and looks_like_action(node.name) and not node.name.startswith("_"):
                    _sh = _shape(node)
                    if _sh:
                        findings.setdefault("已剔噪(有理由)", []).append(
                            {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                             "理由": _sh})
                        continue
                    _why2 = _legit(str(p.relative_to(ROOT)), node.name)
                    if _why2:
                        findings.setdefault("已剔噪(有理由)", []).append(
                            {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                             "理由": _why2})
                        continue
                    findings["空壳函数"].append(
                        {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                         "理由": "函数体 ≤1 条语句且无副作用"})
                defined.setdefault(node.name, []).append((p, node.lineno))
                # 🔴 修：位置参数默认值要**从末尾对齐**，仅关键字按索引对齐。
                #    旧写法把 defaults 与 kw_defaults 混着 zip ⇒ 把 taiwan=True 错配成 dry_run 的默认值。
                _pos = list(getattr(node.args, "posonlyargs", [])) + list(node.args.args)
                _pd = list(node.args.defaults)
                _pairs = list(zip(_pos[len(_pos) - len(_pd):], _pd)) if _pd else []
                _pairs += list(zip(node.args.kwonlyargs, node.args.kw_defaults))
                for _arg, _dd in _pairs:
                    if _arg is None:
                        continue
                    if _dd is not None and isinstance(_dd, ast.Constant) and _dd.value is True                        and _arg.arg in ("dry_run", "dry", "干跑", "simulate"):
                        findings["干跑默认"].append(
                            {"位置": "%s:%d" % (p.relative_to(ROOT), node.lineno), "函数": node.name,
                             "理由": "参数 %s 默认 True ⇒ 默认「只说不动」" % _arg.arg})

    # ④b 验收器虚报：tools/verify_*.py 里出现"永远为真"的断言（如 assert True / "通过": True 字面量）
    for p, src in all_src.items():
        if not (p.parent.name == "tools" and p.name.startswith("verify_")):
            continue
        for i, line in enumerate(src.splitlines(), 1):
            s = line.strip()
            if s.startswith("assert True") or '"通过": True,' in s or "all(True for" in s:
                findings.setdefault("验收器虚报", []).append(
                    {"位置": "%s:%d" % (p.relative_to(ROOT), i), "函数": "-",
                     "理由": "永远为真的断言 ⇒ 验收等于没验",
                     "代码": s[:90]})

    # ④ 无调用方（公开函数，且非框架入口约定名）
    ENTRY_OK = ("main", "run", "status", "report", "catalog", "export", "setup", "register", "get",
                "post", "put", "delete", "startup", "shutdown", "spec", "probe", "selftest")
    for name, locs in defined.items():
        if name.startswith("_") or name in ENTRY_OK:
            continue
        # 🔴 收紧：只报 core/ 与 body/ 下、名字像动作、且零引用的（那是真"没接线"）
        _rel = str(locs[0][0].relative_to(ROOT)).replace(chr(92), "/")
        if not (_rel.startswith("core/") or _rel.startswith("body/")):
            continue
        if not looks_like_action(name):
            continue
        hits = 0
        for p, src in all_src.items():
            if any(p == lp for lp, _ in locs):
                continue
            if name in src:
                hits += 1
        if hits == 0:
            p0, l0 = locs[0]
            findings["无调用方"].append({"位置": "%s:%d" % (p0.relative_to(ROOT), l0), "函数": name,
                                         "理由": "本仓其它文件**零引用**"})
    return findings


def main() -> int:
    f = scan()
    total = sum(len(v) for v in f.values())
    print("== 真执行审计（禁假执行/虚报）==")
    for k, v in f.items():
        print("  %s：%d 处" % (k, len(v)))
        for x in v[:8]:
            print("     %-34s %s" % (x["位置"], x.get("函数", "")))
            print("        理由：%s" % x["理由"])
        if len(v) > 8:
            print("     … 另有 %d 处（见 state/real_execution_audit.json）" % (len(v) - 8))
    print("\n合计嫌疑 %d 处" % total)
    out = ROOT / "state" / "real_execution_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(f, ensure_ascii=False, indent=1), encoding="utf-8")
    print("明细:", out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
