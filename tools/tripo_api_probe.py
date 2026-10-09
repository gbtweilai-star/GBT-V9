# tools/tripo_api_probe.py —— Tripo 密钥探针：一条命令把**确切错因**打出来（不猜）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
# 背景（2026-10-09）：主人说"我这里拿的密钥好像不行"。但本机 env 与 state/keys.env 里**都没有 key**，
#   所以"不行"不是框架判的。本探针把这件事变成可复现读数：
#     ① 先看 key 有没有（env → state/keys.env）；
#     ② 有就**真打** Tripo 开放接口，把 HTTP 码与错误原文摊出来；
#     ③ 常见错因逐条对照（401=key不对/没激活，402=额度/计费，403=没开 API 权限，404=端点写错）。
# 用法：python tools/tripo_api_probe.py            # 读 env / state/keys.env 里的 TRIPO_API_KEY
#       python tools/tripo_api_probe.py --key tsk_xxx
import json
import os
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "https://api.tripo3d.ai/v2/openapi"
WHY = {401: "key 不对/没激活（或复制不全、多了空格）",
       402: "额度或计费问题（API 调用通常单独计费，网页点数未必通用）",
       403: "这个账号/套餐没开 API 权限（Tripo 的 API 有时要单独开通）",
       404: "端点写错（v1 老端点已换 v2）",
       429: "限流"}


def key_from() -> tuple:
    if "--key" in sys.argv:
        return sys.argv[sys.argv.index("--key") + 1], "命令行"
    for env in ("TRIPO_API_KEY", "TRIPO_KEY", "V9_TRIPO_KEY", "TRIPO3D_API_KEY"):
        if os.environ.get(env):
            return os.environ[env], "env " + env
    from pathlib import Path
    f = Path(__file__).resolve().parent.parent / "state" / "keys.env"
    if f.is_file():
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip().startswith("TRIPO") and "=" in ln:
                k, v = ln.split("=", 1)
                if v.strip():
                    return v.strip(), "state/keys.env 的 " + k
    return "", ""


def call(path: str, key: str) -> dict:
    url = BASE + path
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return {"http": r.status, "body": r.read().decode("utf-8", "replace")[:300]}
    except urllib.error.HTTPError as e:
        return {"http": e.code, "body": e.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:                                  # noqa: BLE001
        return {"http": 0, "body": type(exc).__name__ + ": " + str(exc)[:150]}


def main() -> int:
    key, src = key_from()
    print("== Tripo 密钥探针 ==")
    if not key:
        print("  ❌ 本机没有 key（env 与 state/keys.env 都没有）——所以'不行'不是框架判的。")
        print("  怎么给：① 面板首启闸填 TRIPO_API_KEY，或 ② 写进 state/keys.env：TRIPO_API_KEY=tsk_xxx")
        print("  去哪拿：Tripo → 右上头像 → Account → **API**（你截图红箭头那个）里生成/复制")
        return 0
    print("  key 来源：%s · 掩码 %s…%s（共 %d 字符）" %
          (src, key[:6], key[-4:], len(key)))
    if key != key.strip():
        print("  ⚠ 前后有空白字符 —— 这本身就是'key 不行'的常见原因")
    for path in ("/user", "/task", ""):
        got = call(path, key)
        hint = WHY.get(got["http"], "")
        print("  GET %-8s → HTTP %-4s %s  %s" % (path or "/", got["http"],
                                                 (got["body"] or "").replace(chr(10), " ")[:110],
                                                 ("← " + hint) if hint else ""))
    print("\n口径：只有真打出来的 HTTP 码才算证据；本探针不伪造结论。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
