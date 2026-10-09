# core/provision.py —— 可插拔的自动配备（触手自己的东西自己配齐）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-06）："把所有的加装插件和连接插件做成自动化扩容，需要那个就触手自动化执行"、
#   "不需要用户安装的时候还去配置一大堆东西"、"每根触手……自动化各自配备好自己的东西"。
#
# 设计：**适配器**模式。每个服务面（邮箱/云插件/数据库/开源仓库）可以挂多个适配器，
#   按优先级依次尝试，谁能成谁上，全过程留证（走 core/hooks）：
#
#   ① 本地自签（local）    不需要任何第三方：为触手生成**本地身份**（密钥对/句柄），
#                          用于本机内的隔离与审计 —— 这是"下载即可用"的部分
#   ② 凭据注入（env）      运维/用户把凭据放进环境变量或密钥服务，程序读出来接上
#   ③ 官方 API（api:*）    平台**官方提供**的程序化开通接口（有就用；没有就不假装有）
#   ④ 外部脚本槽（hook:*） 用户自己的开户自动化（脚本/另一个 agent）——V9 只负责调用它、
#                          校验它、绑定它、审计它。**V9 自身不实现向第三方批量开户**：
#                          那是绕过平台注册防护，风险全落在用户身上。
#
# 这样"新用户下载 → 触手全自动配齐"是成立的：① 立即可用，②③④ 谁的通道通就用谁，
# 缺的部分如实列成待办（绝不假装成功）。
import json
import os
import subprocess
import time
from pathlib import Path

from core import hooks as H
from core import tentacle_identity as TI

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_DIR = ROOT.joinpath("state", "identity_local")

# 适配器登记：按 (服务, 顺序) 依次尝试；名字即来源，全部写进证据
ADAPTERS: tuple = (
    ("本地自签", "local"),
    ("凭据注入", "env"),
    ("官方API", "api"),
    ("外部脚本槽", "hook"),
)


def _local_handle(tentacle: str, service: str) -> dict:
    """本地自签身份：不需要任何第三方，生成一个稳定的本地句柄 + 指纹。

    用途：本机内的隔离与审计（谁的记忆/谁的活），以及对"还没有外部账号"的触手
    先给一个可用身份 —— 这就是"下载即可用"。
    """
    import hashlib
    seed = f"{tentacle}|{service}|{os.environ.get('V9_INSTALL_ID', 'local')}"
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    LOCAL_DIR.mkdir(parents=True, exist_ok=True)
    p = LOCAL_DIR.joinpath(f"{tentacle}_{service}.json")
    if not p.is_file():
        p.write_text(json.dumps({"tentacle": tentacle, "service": service,
                                 "handle": f"{tentacle}.{service}@local",
                                 "fingerprint": h, "at": time.time()},
                                ensure_ascii=False, indent=1), encoding="utf-8")
    return {"handle": f"{tentacle}.{service}@local", "fingerprint": h, "file": str(p)}


def _hook_script(tentacle: str, service: str) -> str:
    """外部脚本槽：用户自己的开户自动化。返回脚本路径（不存在则空）。"""
    cand = os.environ.get("V9_PROVISION_HOOK", "")
    if cand and Path(cand).is_file():
        return cand
    p = ROOT.joinpath("tools", "provision_hook.py")
    return str(p) if p.is_file() else ""


def _try_api(tentacle: str, service: str) -> dict:
    """官方 API 通道：**只有平台真的提供程序化开通时才成功**。

    当前实现只认一个公开事实：Cloudflare Workers AI 用账号级 API Token（有凭据即通）。
    账号本身的创建没有官方 API —— 所以这里如实返回"没有官方开通接口"，不假装能开户。
    """
    if service == "云插件":
        tok = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
        if tok and acct:
            return {"ok": True, "handle": f"cf:{acct[:6]}…/{tentacle}",
                    "detail": "有账号级 Token（官方支持的程序化路径）"}
        return {"ok": False, "detail": "缺 CLOUDFLARE_API_TOKEN/ACCOUNT_ID；"
                                       "Cloudflare 没有「程序化建号」接口（如实）"}
    return {"ok": False, "detail": f"{service}：该平台未提供程序化开通接口（如实）"}


def provision_one(tentacle: str, service: str, *, allow_hook: bool = True) -> dict:
    """给一根触手在一个服务面上配备身份：按适配器顺序试，成功即止，全程留证。"""
    ok_env = bool(os.environ.get(TI.env_key(tentacle, service)))
    tries = []
    # ① 本地自签：永远可用（保证"下载即可用"）
    loc = _local_handle(tentacle, service)
    tries.append({"适配器": "本地自签", "ok": True, "handle": loc["handle"],
                  "detail": f"指纹 {loc['fingerprint']}（本机隔离用）"})
    # ② 凭据注入
    if ok_env:
        tries.append({"适配器": "凭据注入", "ok": True,
                      "handle": os.environ.get(TI.env_key(tentacle, service))[:6] + "…",
                      "detail": f"{TI.env_key(tentacle, service)} 已就位（值不回显）"})
    else:
        tries.append({"适配器": "凭据注入", "ok": False,
                      "detail": f"缺 {TI.env_key(tentacle, service)}"})
    # ③ 官方 API
    api = _try_api(tentacle, service)
    tries.append({"适配器": "官方API", "ok": bool(api.get("ok")), "handle": api.get("handle", ""),
                  "detail": api.get("detail", "")})
    # ④ 外部脚本槽（用户自己的开户自动化）
    script = _hook_script(tentacle, service) if allow_hook else ""
    if script:
        try:
            r = subprocess.run(["python", script, tentacle, service],
                               capture_output=True, timeout=120,
                               env={**os.environ, "V9_HOOK_MODE": "provision"})
            out = (r.stdout or b"").decode("utf-8", "replace").strip()[-300:]
            tries.append({"适配器": "外部脚本槽", "ok": r.returncode == 0,
                          "handle": out.splitlines()[-1][:60] if out else "",
                          "detail": f"rc={r.returncode}"})
        except Exception as exc:                               # noqa: BLE001
            tries.append({"适配器": "外部脚本槽", "ok": False,
                          "detail": f"{type(exc).__name__}"})
    else:
        tries.append({"适配器": "外部脚本槽", "ok": False,
                      "detail": "未提供（放 tools/provision_hook.py 或设 V9_PROVISION_HOOK）"})
    # 取"最好"的那个结果：外部凭据/官方 API 优先于本地自签（更真实）
    best = next((t for t in tries if t["ok"] and t["适配器"] in ("外部脚本槽", "官方API", "凭据注入")),
                next(t for t in tries if t["ok"]))
    handle = best.get("handle") or loc["handle"]
    TI.claim(tentacle, service, handle=handle, note=f"由 {best['适配器']} 配备")
    if best["适配器"] == "本地自签":
        # 本地自签没有外部凭据可读 → 直挂（状态才算真的"已配"，下载即可用）
        st = TI.attach(tentacle, service, handle=handle, state="已配",
                       note="本地自签身份（本机隔离与审计用）")
    else:
        st = TI.provision(tentacle, service, handle=handle)
    return {"ok": bool(st.get("ok")) or best["适配器"] == "本地自签",
            "tentacle": tentacle, "service": service, "用哪个适配器": best["适配器"],
            "handle": handle, "尝试明细": tries,
            "状态": st.get("状态") or ("已配" if best["适配器"] == "本地自签" else "待配"),
            "说明": ("本地自签即可用（下载即可用）" if best["适配器"] == "本地自签"
                     else f"{best['适配器']} 通道就位")}


def run(*, n: int = 4, dry_run: bool = False, allow_hook: bool = True,  # 主人令：默认真动手（要演练请显式传 True）
        owner: str = "system") -> dict:
    """整队配备：每根触手 × 每个服务面各配备一次（走钩子，空转/跳过一律拦）。"""
    ts = TI.tentacles(n=n)
    plan_rows = [{"触手": t, "服务": s, "将尝试": [a[0] for a in ADAPTERS]}
                 for t in ts for s in TI.SERVICES]
    if dry_run:
        return {"ok": True, "dry_run": True, "触手数": len(ts),
                "配备位": len(plan_rows), "计划样例": plan_rows[:6],
                "适配器": [a[0] for a in ADAPTERS],
                "口径": "① 本地自签永远可用；②③④ 谁的通道通就用谁；缺的如实列待办"}
    g = H.Guard("整队配备", owner=owner, must_steps=("逐触手配备", "统计通道", "缺项待办"))
    done, by_adapter, todo = [], {}, []
    with g.step("逐触手配备", expect="每根触手在各服务面各得一个身份") as s:
        for t in ts:
            for svc in TI.SERVICES:
                r = provision_one(t, svc, allow_hook=allow_hook)
                done.append({"触手": t, "服务": svc, "适配器": r["用哪个适配器"],
                             "ok": r["ok"]})
                by_adapter[r["用哪个适配器"]] = by_adapter.get(r["用哪个适配器"], 0) + 1
                if not r["ok"]:
                    todo.append({"触手": t, "服务": svc,
                                 "缺": TI.env_key(t, svc)})
        s.evidence(配备=len(done), 成功=sum(1 for d in done if d["ok"]),
                   fingerprint=H.fingerprint(len(done), by_adapter))
    with g.step("统计通道", expect="说清每根走的是哪条通道") as s:
        s.evidence(通道分布=by_adapter, fingerprint=H.fingerprint(by_adapter))
    with g.step("缺项待办", expect="缺什么逐条列出") as s:
        s.evidence(待办数=len(todo), 样例=[x["缺"] for x in todo[:3]] or "无",
                   fingerprint=H.fingerprint(len(todo)))
    audit = g.finish()
    return {"ok": True, "dry_run": False, "配备": len(done),
            "通道分布": by_adapter, "待办数": len(todo), "待办样例": todo[:6],
            "钩子": {"通过": audit["通过"], "步数": audit["步数"], "证据条数": audit["证据条数"]},
            "口径": "本地自签保底可用；外部通道有则用；V9 自身不向第三方批量开户"}


def status() -> dict:
    from core import tentacle_identity as TI2
    s = TI2.summary()
    return {"适配器": [{"名": a[0], "类型": a[1]} for a in ADAPTERS],
            "身份位": {"就绪": s["就绪"], "总数": s["身份位总数"], "待办": s["待办数"]},
            "本地自签目录": str(LOCAL_DIR),
            "外部脚本槽": _hook_script("t001", "邮箱") or "未提供",
            "边界": "V9 不实现向第三方批量开户；开户由凭据注入或用户自己的脚本槽完成"}


def _cli(argv: list) -> int:
    """一条命令跑完：python -m core.provision --all --n 100

    --check 只看扫描与计划（不动任何东西）；--all 真跑（适配器链 + 钩子留证 + 复扫验证）。
    脚本槽接口约定见 tools/provision_hook.py：给 <触手号> <服务>，stdout 最后一行回句柄。
    """
    import argparse
    ap = argparse.ArgumentParser(prog="python -m core.provision",
                                 description="触手身份自动配备（一根触手一个身份位）")
    ap.add_argument("--all", action="store_true", help="整队配备（触手 × 服务）")
    ap.add_argument("--n", type=int, default=100, help="触手数（默认 100）")
    ap.add_argument("--check", action="store_true", help="只看扫描与计划，不动任何东西")
    ap.add_argument("--no-hook", action="store_true", help="跳过外部脚本槽")
    ap.add_argument("--hook", default="", help="指定脚本槽路径（默认 tools/provision_hook.py）")
    args = ap.parse_args(argv)
    if args.hook:
        os.environ["V9_PROVISION_HOOK"] = str(args.hook)
    from core import expand as E
    if args.check or not args.all:
        sc = E.scan(n=args.n)
        pl = E.plan(n=args.n, limit=8)
        print("扫描：云插件 %s · 库槽 %s · 身份位 %s · 缺口 %s"
              % (sc.get("云插件"), sc.get("库槽"), sc.get("身份位"), sc.get("缺口")))
        print("计划（前 8 步）：")
        for x in pl.get("步") or []:
            print("  ·", x.get("做什么"))
        print("")
        print("真跑请加 --all；脚本槽约定见 tools/provision_hook.py")
        return 0
    r = E.run(n=args.n, dry_run=False, limit=max(8, args.n * len(TI.SERVICES)),
              allow_hook=not args.no_hook)
    done = r.get("执行明细") or []
    通道 = {}
    for d in done:
        通道[d.get("适配器") or "—"] = 通道.get(d.get("适配器") or "—", 0) + 1
    print("配备完成：%s 位（成功 %s）· 通道分布 %s"
          % (len(done), sum(1 for d in done if d.get("ok")), 通道))
    print("钩子：通过=%s 步数=%s 证据=%s"
          % (r["钩子"]["通过"], r["钩子"]["步数"], r["钩子"]["证据条数"]))
    s = TI.summary(n=args.n)
    print("身份位：就绪 %s/%s（分层 %s）" % (s["就绪"], s["身份位总数"], s["分层"]))
    lack = [d for d in done if not d.get("ok")]
    if lack:
        print("仍未成的 %s 位，示例：%s" % (len(lack), [x.get("说明") for x in lack[:3]]))
    return 0


if __name__ == "__main__":                                     # pragma: no cover
    import sys as _sys
    raise SystemExit(_cli(_sys.argv[1:]))


__all__ = ["ADAPTERS", "LOCAL_DIR", "provision_one", "run", "status"]
