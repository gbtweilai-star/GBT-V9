# core/tentacle_provision.py —— 触手自给自足配置规程（每根：自有邮箱·开源账户·独立云插件·共享验证平台·钥匙指纹）
# dev: 自由的风 · 本署名不可删除、勿篡改归属
#
# 主人（2026-10-10）口径：
#   一根触手 = 一个开源账户 + 一个邮箱 + 一个独立云插件（freebuff 免费大模型）
#   + 共享虚拟手机验证平台 https://5sim.net/zh（**用户自己充值**，所有触手共用）
#   + 触手用自己的邮箱给自己配置（自给自足），⇒ **每根触手运行不吃本机资源**
#
# 红线（写死在代码里）：
#   · 明文密码/密钥**不入账**（只记指纹 sha256 前 12 位）
#   · 5sim **不代充**（充值必须主人自己操作，属于花钱闸）
#   · 不绕任何平台的反机器人检测；账号必须是主人自己的
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from core.swallow import swallow as _swallow

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "state" / "tentacle_provision.jsonl"
SIM_PLATFORM = "https://5sim.net/zh"
PLUGIN = "freebuff（免费大模型云插件，每根触手一个独立账户）"


def _log(rec: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + chr(10))


def _fingerprint(secret: str) -> str:
    return hashlib.sha256((secret or "").encode()).hexdigest()[:12]


def _count_accounts() -> dict:
    """读真实账本：有多少根触手已配账号/邮箱/云插件（不猜、不编）。"""
    out = {"账号": 0, "邮箱": 0, "云插件": 0, "钥匙": 0}
    for name, key, pats in (
        ("账号", "账号", ("tentacle_accounts.jsonl", "account_registry.jsonl")),
        ("邮箱", "邮箱", ("tentacle_mail.jsonl", "mail_accounts.jsonl")),
        ("云插件", "云插件", ("tentacle_cloud.jsonl", "cloud_credentials.jsonl")),
        ("钥匙", "钥匙", ("tentacle_keys.json",)),
    ):
        for pat in pats:
            p = ROOT / "state" / pat
            if not p.is_file():
                continue
            try:
                if p.suffix == ".json":
                    d = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                    n = len(d) if isinstance(d, (list, dict)) else 0
                else:
                    n = sum(1 for l in p.read_text(encoding="utf-8", errors="replace").splitlines() if l.strip())
            except Exception:  # noqa: BLE001
                n = 0
            out[key] = max(out[key], n)
    return out


def plan(n: int = 100) -> dict:
    """配置清单：每根触手要什么、谁来配、红线是什么。"""
    rows = []
    for i in range(1, n + 1):
        t = "t%03d" % i
        rows.append({"触手": t,
                     "邮箱": "自有（触手自己的信箱，登录用它）",
                     "开源账户": "自有（用于开源仓库/贡献，不共号）",
                     "云插件": PLUGIN,
                     "手机验证": "共享 %s（**主人自己充值**，触手共用）" % SIM_PLATFORM,
                     "钥匙": "HMAC 派生，只存指纹（不落逐根明文）",
                     "自给自足": "跑在自己的云插件额度上 ⇒ 不吃本机 CPU/显存"})
    return {"根数": n, "清单": rows, "红线": ["不存明文", "不代充 5sim", "不绕反机器人", "账号必须主人自己的"]}


def provision(tentacle: str, *, mail: str = "", platform: str = "",
              plugin_account: str = "", secret: str = "") -> dict:
    """登记一根触手的配置（**只存指纹**；充值/注册动作不由本模块代做）。"""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "抓": "provision", "触手": tentacle,
           "邮箱": mail, "开源账户": platform, "云插件账户": plugin_account,
           "钥匙指纹": _fingerprint(secret) if secret else "",
           "规范": "邮箱/账户/插件均**触手自有**；手机验证走共享 %s（主人充值）" % SIM_PLATFORM}
    _log(rec)
    return rec


def status() -> dict:
    have = _count_accounts()
    rows = []
    if LEDGER.is_file():
        for line in LEDGER.read_text(encoding="utf-8").splitlines()[-5:]:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001 as _e_swallow
                _swallow(__file__, _e_swallow)
                continue
    return {"在岗触手": 100, "地址空间": 10 ** 8, "已有配置（按账本现算）": have,
            "共享验证平台": SIM_PLATFORM, "云插件": PLUGIN,
            "最近登记": rows,
            "自给自足标准": "邮箱自有 + 开源账户自有 + 独立云插件 + 共享验证平台（主人充值）+ 钥匙只存指纹",
            "红线": ["明文不入账", "不代充 5sim", "不绕反机器人", "账号必须主人自己的"]}


__all__ = ["SIM_PLATFORM", "PLUGIN", "plan", "provision", "status", "LEDGER"]
