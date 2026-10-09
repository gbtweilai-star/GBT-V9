# tools/provision_hook.py —— 触手身份「开户槽」（V9 调用你，你回句柄）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 这是 V9 ⇄ 你的开户自动化之间的**唯一接口**。core/provision.py 的第四个适配器会调用它：
#
#   调用：python tools/provision_hook.py <触手号> <服务>      （环境变量 V9_HOOK_MODE=provision）
#   约定：
#     · 退出码 0 = 成功；非 0 = 失败（V9 会退到别的适配器，并如实记录原因）
#     · **stdout 最后一行** = 这次拿到的句柄（邮箱地址 / 账号名 / 仓库路径 …）
#     · 幂等：同一个 (触手号, 服务) 重复调用，必须返回**同一个句柄**，不许再开一个
#     · 不许把明文凭据打到 stdout（V9 会把最后一行写进金库与台账）
#
# 谁填这一段：
#   · 本地自签 / 凭据注入 / 官方 API —— V9 自己就做了，不需要这个脚本；
#   · **开户动作本身**（去平台注册账号）—— 由你的运维脚本、企业号自动化或另一个 agent 填进来。
#     V9 不代你向第三方开户（那是绕过平台注册防护），但 V9 会把调用、校验、绑定、
#     审计、扩容全包了 —— 你只要保证这个脚本"给触手号和服务，回一个句柄"。
#
# 用法（一条命令跑完 400 个位）：
#   python -m core.provision --all --n 100        # 走适配器链（含本脚本）
#   python -m core.provision --check              # 只看扫描与计划，不动任何东西
import json
import os
import sys
import time
from pathlib import Path
from core.swallow import swallow as _swallow

SERVICES = ("邮箱", "云插件", "数据库", "开源仓库")
ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = ROOT.joinpath("state", "provision_hook_log.jsonl")

# ─────────────────────────────────────────────────────────────
#  在这里接你的开户实现。约定：拿到 (tentacle, service) → 返回句柄字符串。
#  下面三个分支演示"三种合法来源"，你按自己的通道替换/追加即可。
# ─────────────────────────────────────────────────────────────
def open_identity(tentacle: str, service: str) -> tuple:
    """返回 (句柄, 来源说明)。**必须是幂等的**：同参数重复调用给同一个句柄。"""
    # ① 你已经有账号池：从环境变量/密钥服务里取（推荐：一个触手一个，别复用）
    #    约定变量名：V9ACCT_<服务>_<触手号大写>，值为句柄；凭据另存 *_SECRET
    env_handle = os.environ.get(f"V9ACCT_{_svc_key(service)}_{tentacle.upper()}", "").strip()
    if env_handle:
        return env_handle, "账号池（环境变量 V9ACCT_*）"

    # ② 平台有官方 API：在这里调用它（用你的企业凭据，别写死在源码里）
    #    api = _official_open(tentacle, service)   # ← 你的实现
    #    if api: return api, "官方 API"
    # ③ 你的自动化（浏览器/接口脚本）：在这里调用你自己的模块
    #    opened = _my_automation(tentacle, service)  # ← 你的实现
    #    if opened: return opened, "自建自动化"
    #
    # 都没接：如实说"没接"，让 V9 退到本地自签（下载即可用的那条）——**不许假装开了号**。
    return "", "未接入（请在本文件里填你的开户通道）"


def _svc_key(service: str) -> str:
    return {"邮箱": "MAIL", "云插件": "CLOUD", "数据库": "DB", "开源仓库": "REPO"}.get(
        str(service), "X")


def _log(tentacle: str, service: str, handle: str, why: str, ok: bool) -> None:
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "tentacle": tentacle, "service": service,
                                "handle": handle, "why": why, "ok": ok},
                               ensure_ascii=False) + "\n")
    except OSError as e:
        _swallow(__file__, e)



def main(argv: list) -> int:
    if len(argv) < 3:
        print("用法：provision_hook.py <触手号> <服务>；服务取 " + "/".join(SERVICES),
              file=sys.stderr)
        return 2
    tentacle, service = str(argv[1]).strip(), str(argv[2]).strip()
    if not tentacle.startswith("t") or not tentacle[1:].isdigit():
        print(f"触手号形如 t001…t100，收到 {tentacle!r}", file=sys.stderr)
        return 2
    if service not in SERVICES:
        print(f"服务只认 {'/'.join(SERVICES)}，收到 {service!r}", file=sys.stderr)
        return 2
    handle, why = open_identity(tentacle, service)
    _log(tentacle, service, handle, why, bool(handle))
    if not handle:
        # 没接通道：如实失败，V9 会退到本地自签（不影响"下载即可用"）
        print(f"未接入开户通道：{tentacle}/{service} —— {why}", file=sys.stderr)
        return 3
    print(handle)                      # stdout 最后一行 = 句柄
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
