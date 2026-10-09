# tools/param_rig_chain.py —— 把绑骨工具链的写死路径参数化（目标3：接通数字人骨架绑定工具链）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 事实：tools/codex-scripts 里 10 个脚本都有同一行
#   WS = pathlib.Path(r"C:\Users\ADMIN\Desktop\GBT小土豆V8")
# 搬自 V8 母仓 ⇒ 在本仓跑会去 V8 找资产（实测报 FileNotFoundError: ...V8\holo_...），
# 这就是"装进来了但跑不了"的真因。
# 改法：按**行**判（不跟正则转义较劲），换成"env 优先、默认本仓"。
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "tools" / "codex-scripts"
V9 = str(ROOT)


def is_ws_line(ln: str) -> bool:
    return ("WS" in ln) and ("pathlib.Path" in ln) and ("GBT" in ln and "V8" in ln)


def main() -> int:
    touched, clean, bad = [], [], []
    for f in sorted(SCRIPTS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        if "GBT_DH_WS" in src:
            clean.append(f.name + "(已参数化)")
            continue
        if "GBT小土豆V8" not in src:
            clean.append(f.name)
            continue
        lines = src.splitlines(keepends=True)
        hit = next((i for i, ln in enumerate(lines) if is_ws_line(ln)), None)
        if hit is None:
            bad.append(f.name + "(有 V8 但没有 WS= 行，未动)")
            continue
        repl = ('import os as _os\n'
                'WS = pathlib.Path(_os.environ.get("GBT_DH_WS") or r"' + V9 + '")   # 参数化：env 优先，默认本仓\n')
        lines[hit:hit + 1] = [repl]
        f.write_text("".join(lines), encoding="utf-8")
        touched.append(f.name)
    print("已参数化 %d 个：%s" % (len(touched), "、".join(touched) or "无"))
    print("无需处理 %d 个：%s" % (len(clean), "、".join(clean) or "无"))
    if bad:
        print("需要人看的 %d 个：%s" % (len(bad), "；".join(bad)))
    import py_compile
    files = sorted(SCRIPTS.glob("*.py"))
    ok = 0
    for f in files:
        try:
            py_compile.compile(str(f), doraise=True, cfile=str(f) + ".k")
            ok += 1
        except Exception as exc:
            print("语法不过:", f.name, exc)
        finally:
            Path(str(f) + ".k").unlink(missing_ok=True)
    print("语法检查：%d/%d 通过" % (ok, len(files)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
