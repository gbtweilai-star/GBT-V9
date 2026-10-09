# parity/run.py —— 隔离子进程运行器
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 父只消毒+spawn+汇总; 子进程才导入应用模块;
#       结果用 --out 增量原子落盘(stdout 只当日志);
#       子进程崩溃/超时/被杀 → 未到达项补 skipped, 绝不算通过

from __future__ import annotations
import argparse, asyncio, json, os, re, shutil, signal, subprocess
import sys, uuid
from pathlib import Path
from core.swallow import swallow as _swallow

OK, PARITY_FAILED, CONFIG_ERROR, INTERNAL_ERROR, TIMEOUT = range(5)

# 生产凭据一律不传子进程
BLOCKED = re.compile(r"^(DATABASE_URL|PG.*|R2_.*|AWS_.*|S3_.*|CLOUDFLARE_.*|REDIS_URL)$")
SENSITIVE = re.compile(r"(TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE|ACCESS_KEY)", re.I)
STATUSES = ("pass", "failed", "missing", "degraded", "skipped")


def atomic_json(path: Path, data: dict) -> None:
    """原子替换：崩溃/被 kill 时绝不会读到半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=True)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


# ─────────────────────────────────────────────────────────────
# ① env 消毒（白名单，不复制父环境）
# ─────────────────────────────────────────────────────────────
def child_env(project_root: Path) -> dict[str, str]:
    names = {"PATH", "LANG", "LC_ALL", "TZ", "HOME",
             "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}
    env = {k: os.environ[k] for k in names if k in os.environ}
    env["PYTHONPATH"] = str(project_root)          # 不继承调用者任意导入路径

    # 只透传 PARITY_*（测试账本/测试 R2 凭据就在这里，属合法）
    for k, v in os.environ.items():
        if k.startswith("PARITY_") and k != "PARITY_ENV_PASS":
            env[k] = v

    # 逃逸阀：只允许显式列出的**非敏感**变量名
    for name in filter(None, (x.strip() for x in
                              os.environ.get("PARITY_ENV_PASS", "").split(","))):
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", name)
                or BLOCKED.match(name) or SENSITIVE.search(name)):
            raise ValueError(f"PARITY_ENV_PASS 拒绝变量名: {name}")
        if name in os.environ:
            env[name] = os.environ[name]

    # 最后一道扫地：删掉一切被拦的键（★必须 list(env)，否则边遍历边删会 RuntimeError）
    for key in list(env):
        if BLOCKED.match(key):
            env.pop(key, None)
    return env


# ─────────────────────────────────────────────────────────────
# ② 报告
# ─────────────────────────────────────────────────────────────
def summarize(caps: list[dict]) -> dict:
    return {"total": len(caps),
            **{s: sum(c.get("status") == s for c in caps) for s in STATUSES}}


def write_reports(result: dict, run_dir: Path) -> dict:
    result["summary"] = summarize(result.get("capabilities", []))
    atomic_json(run_dir / "report.json", result)

    s = result["summary"]
    L = ["# Parity 验收报告", "",
         f"- 状态：`{result.get('status', 'incomplete')}`",
         f"- run_id：`{result.get('run_id', '')}`",
         f"- commit_sha：`{result.get('commit_sha', '')}`",
         f"- 总数 {s['total']}｜通过 {s['pass']}｜失败 {s['failed']}｜"
         f"缺失 {s['missing']}｜降级 {s['degraded']}｜跳过 {s['skipped']}", ""]
    if result.get("error"):
        L += [f"> 错误：`{result['error']}`", ""]

    L += ["## 能力明细", ""]
    for cap in result.get("capabilities", []):
        L.append(f"### `{cap.get('id')}` — {cap.get('status')}")
        if cap.get("note"):
            L.append(f"- 说明：{cap['note']}")
        for ck in cap.get("checks", []):
            L.append(f"- `{ck.get('kind')}`：{'通过' if ck.get('passed') else '失败'}")
            if ck.get("reason"):
                L.append(f"  - 原因：`{ck['reason']}`")
            for ev in ck.get("evidence", []):
                L.append(f"  - 证据：`{ev.get('path')}` SHA-256 `{ev.get('sha256')}`")
        L.append("")
    (run_dir / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return result


def backfill_unreached(result: dict, manifest: dict) -> dict:
    """★关键：子进程中途死掉时，未跑到的能力必须显式标 skipped，不能凭空消失。"""
    done = {c["id"] for c in result.get("capabilities", [])}
    for cap in manifest.get("capabilities", []):
        if cap["id"] not in done:
            result["capabilities"].append({
                "id": cap["id"], "status": "skipped", "checks": [],
                "note": "not_reached：子进程提前退出，本项未执行",
            })
    return result


# ─────────────────────────────────────────────────────────────
# ③ 子进程：导入应用 + 跑断言 + 增量落盘
# ─────────────────────────────────────────────────────────────
async def child(args) -> int:
    # 到这里父进程已消毒 env；之后才允许导入项目模块
    from parity.bootstrap import load_manifest, build_context
    from parity.probe import run_parity_probe

    run_dir, out = Path(args.run_dir), Path(args.out)
    manifest = load_manifest(args.manifest)
    result = {"run_id": args.run_id, "commit_sha": args.commit_sha,
              "status": "running", "capabilities": []}
    atomic_json(out, result)

    try:
        ctx = await build_context(manifest=manifest,
                                  evidence_dir=run_dir / "evidence",
                                  temp_root=Path(os.environ["PARITY_TEMP_ROOT"]))
        for cap in manifest["capabilities"]:
            item = {"id": cap["id"], "status": "pass", "checks": []}
            result["capabilities"].append(item)
            atomic_json(out, result)                      # 增量

            if cap.get("equivalence") not in (None, "full"):
                item["status"] = "degraded"               # 降级默认阻断

            for assertion in cap.get("acceptance", []):
                ck = await run_parity_probe(assertion, ctx, run_id=args.run_id)
                ck["kind"] = assertion.get("kind")
                item["checks"].append(ck)
                if not ck["passed"]:
                    cat = (ck.get("reason") or {}).get("category")
                    item["status"] = "missing" if cat == "not_registered" else "failed"
                atomic_json(out, result)                  # 每条断言即落盘

        failed = any(c["status"] != "pass" for c in result["capabilities"])
        result["status"] = "failed" if failed else "passed"
        atomic_json(out, result)
        return PARITY_FAILED if failed else OK

    except (KeyError, ValueError, RuntimeError) as e:      # 配置/隔离错误
        result.update(status="config_error", error=str(e)); atomic_json(out, result)
        return CONFIG_ERROR
    except Exception as e:
        result.update(status="internal_error",
                      error=f"{type(e).__name__}: {e}"); atomic_json(out, result)
        return INTERNAL_ERROR


# ─────────────────────────────────────────────────────────────
# ④ 父进程：预检 + spawn + 超时 + 汇总
# ─────────────────────────────────────────────────────────────
# 预检按 profile 分流：offline 只需隔离 SQLite；PG allowlist / R2 桶是 full 的硬要求
REQUIRED_ENV_BY_PROFILE = {
    "offline": ("PARITY_DATABASE_URL",),
    "full": ("PARITY_DATABASE_URL", "PARITY_ALLOWED_DB_HOSTS", "PARITY_R2_BUCKET"),
}
REQUIRED_ENV = REQUIRED_ENV_BY_PROFILE["full"]

def parent(args) -> int:
    # fail closed：配置缺失在 spawn 前就说清楚，省一轮调试
    prof = os.environ.get("PARITY_PROFILE", "").strip()
    required = REQUIRED_ENV_BY_PROFILE.get(prof, REQUIRED_ENV)
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if prof not in REQUIRED_ENV_BY_PROFILE:
        missing = missing or ["PARITY_PROFILE(offline|full)"]
    if missing:
        run_dir = Path(args.out_dir) / args.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        write_reports({"run_id": args.run_id, "commit_sha": args.commit_sha,
                       "status": "config_error", "capabilities": [],
                       "error": f"缺少环境变量: {', '.join(missing)}"}, run_dir)
        print(f"配置错误，拒绝运行（缺失: {', '.join(missing)}）", file=sys.stderr)
        return CONFIG_ERROR

    root = Path(args.project_root).resolve()
    run_dir = (Path(args.out_dir).resolve() / args.run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    scratch = run_dir / "work"; scratch.mkdir(exist_ok=True)
    out = run_dir / "child-result.json"

    try:
        env = child_env(root)
    except ValueError as e:
        write_reports({"run_id": args.run_id, "commit_sha": args.commit_sha,
                       "status": "config_error", "capabilities": [],
                       "error": str(e)}, run_dir)
        print(e, file=sys.stderr); return CONFIG_ERROR

    env["PARITY_TEMP_ROOT"] = str(scratch)
    env["PARITY_EVIDENCE_DIR"] = str(run_dir / "evidence")

    cmd = [sys.executable, "-m", "parity.run", "--child",
           "--manifest", str(Path(args.manifest).resolve()),
           "--run-dir", str(run_dir), "--out", str(out),
           "--run-id", args.run_id, "--commit-sha", args.commit_sha]

    code, timed_out = INTERNAL_ERROR, False
    try:
        # 子进程输出强制 utf-8 + replace：Windows 默认 GBK 会把中文报告解码炸掉
        proc = subprocess.Popen(cmd, cwd=root, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
        try:
            logs, _ = proc.communicate(timeout=args.timeout)
            code = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                logs, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                logs, _ = proc.communicate()
            code = TIMEOUT
        (run_dir / "child.log").write_text(logs or "", encoding="utf-8")
    finally:
        if not args.keep_work:
            shutil.rmtree(scratch, ignore_errors=True)     # 证据/报告保留

    # 读最后一份增量结果；损坏也不能当成通过
    try:
        result = json.loads(out.read_text("utf-8"))
    except Exception:
        result = {"run_id": args.run_id, "commit_sha": args.commit_sha,
                  "status": "incomplete", "capabilities": [],
                  "error": "子进程未产出可用结果文件"}

    # ★补齐未到达项（子进程被 kill / 崩溃时）
    try:
        manifest = json.loads(Path(args.manifest).read_text("utf-8"))
        backfill_unreached(result, manifest)
    except Exception as e:
        _swallow(__file__, e)


    if timed_out:
        result["status"] = "timeout"; code = TIMEOUT
    elif code not in (OK, PARITY_FAILED, CONFIG_ERROR, INTERNAL_ERROR):
        # 被信号杀死(负码)或未知码 → 内部错误，绝不是超时
        result["status"] = "runner_error"
        result["error"] = f"子进程异常退出: returncode={code}"; code = INTERNAL_ERROR

    write_reports(result, run_dir)
    s = result["summary"]
    print(f"[parity] {result['status']}  total={s['total']} pass={s['pass']} "
          f"failed={s['failed']} missing={s['missing']} "
          f"degraded={s['degraded']} skipped={s['skipped']}")
    print(f"[parity] 报告: {run_dir / 'report.md'}")
    return code


def main() -> int:
    p = argparse.ArgumentParser(prog="parity.run")
    p.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--manifest", required=True)
    p.add_argument("--out", default=""); p.add_argument("--run-dir", default="")
    p.add_argument("--run-id", default=""); p.add_argument("--commit-sha", default="unknown")
    p.add_argument("--project-root", default=".")
    p.add_argument("--out-dir", default="parity-reports")
    p.add_argument("--timeout", type=int, default=900)
    p.add_argument("--keep-work", action="store_true",
                   help="保留临时目录(排障用)，证据与报告始终保留")
    a = p.parse_args()

    if a.child:
        if not (a.out and a.run_dir):
            print("--child 需要 --out 与 --run-dir", file=sys.stderr); return CONFIG_ERROR
        return asyncio.run(child(a))

    a.run_id = a.run_id or uuid.uuid4().hex
    return parent(a)


if __name__ == "__main__":
    raise SystemExit(main())
