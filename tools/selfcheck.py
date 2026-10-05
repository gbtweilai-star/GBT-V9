# tools/selfcheck.py —— 落盘后自检：目录完整性 + 语法 + 关键导入（可选：七条验收）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 用法：python tools/selfcheck.py [--with-acceptance]
import argparse
import importlib
import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED = [
    "README.md", "requirements.txt", ".env.example", "docker-compose.yml", "config.yaml",
    "contract/tentacle_contract.ts", "contract/slot_board.py",
    "core/brain.py", "core/isolated_bus.py", "core/mesh.py", "core/pulse.py",
    "senses/devour.py", "senses/r2.py", "senses/tiered_store.py", "senses/printer.py",
    "senses/playback.py", "senses/cache_reaper.py",
    "scan/scanner.py", "scan/scan_rules.py", "scan/cross_scan.py", "scan/report.py",
    "audit/ledger.py", "audit/stack_acceptance.py",
    "panel/server.py", "tools/entry.py", "main.py",
]

IMPORTS = [
    "core.brain", "core.isolated_bus", "core.mesh", "core.pulse",
    "contract.slot_board",
    "senses.devour", "senses.r2", "senses.tiered_store", "senses.printer",
    "senses.playback", "senses.cache_reaper",
    "scan.scanner", "scan.scan_rules", "scan.cross_scan", "scan.report",
    "audit.ledger", "audit.stack_acceptance",
]


def check_tree() -> list[str]:
    missing = [rel for rel in REQUIRED if not (ROOT / rel).exists()]
    return missing


def check_syntax() -> list[str]:
    bad = []
    # desktop/resources|release 与 octop-home 是打包进来的 Octop 便携底座与安装产物，
    # 其中含底座自带第三方文件（如 tcl/tix 演示），不属于本工程语法检查范围。
    skip_parts = {"_archive", "__pycache__", ".venv", "resources", "release",
                  "octop-home"}
    for p in sorted(ROOT.rglob("*.py")):
        if any(part in skip_parts for part in p.parts):
            continue
        try:
            py_compile.compile(str(p), doraise=True, cfile=str(p) + ".pyc_check")
        except py_compile.PyCompileError as exc:
            bad.append(f"{p.relative_to(ROOT)}: {exc}")
    for junk in ROOT.rglob("*.pyc_check"):
        junk.unlink(missing_ok=True)
    return bad


def check_imports() -> list[str]:
    bad = []
    for mod in IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{mod}: {type(exc).__name__}: {exc}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-acceptance", action="store_true")
    args = ap.parse_args()

    print(f"== 落盘自检 @ {ROOT} ==")
    missing = check_tree()
    print(f"[1] 目录完整性: {'OK ' + str(len(REQUIRED)) + ' 件齐全' if not missing else '缺 ' + str(len(missing))}")
    for m in missing:
        print("    - 缺:", m)
    syntax = check_syntax()
    print(f"[2] 语法检查  : {'OK' if not syntax else 'FAIL ' + str(len(syntax))}")
    for s in syntax[:8]:
        print("    !", s)
    imports = check_imports()
    print(f"[3] 关键导入  : {'OK ' + str(len(IMPORTS)) + '/' + str(len(IMPORTS)) if not imports else 'FAIL'}")
    for s in imports:
        print("    !", s)

    ok = not (missing or syntax or imports)
    if args.with_acceptance:
        print('[4] 七条验收  : 见下（真读数；拿不到写「未读到」）')
        try:
            from audit import stack_acceptance
            rc = stack_acceptance.main() if hasattr(stack_acceptance, "main") else 0
            ok = ok and int(rc or 0) == 0
        except SystemExit as exc:
            ok = ok and int(exc.code or 0) == 0
        except Exception as exc:  # noqa: BLE001
            print("    ! 验收执行失败:", type(exc).__name__, exc)
            ok = False

    print("\n自检结论:", "✅ 可落盘运行" if ok else "❌ 有未通过项（见上）")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
