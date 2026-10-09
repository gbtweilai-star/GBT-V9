# senses/ble.py —— 蓝牙感知与操控（多源枚举 + 真实 BLE 扫描 + 授权闸门 + 审计）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 蒸馏来源（真实项目，MIT）：Hypijump31/bluetooth-mcp-server（23★，Python，2025-04）
#   · 借鉴：① Windows 原生多源枚举（PnP 设备类 + BTHPORT 注册表 + MAC 前缀厂商库）→ 不依赖 pybluez
#           ② 把「扫描」做成 AI 可调用的工具面（参数 duration / filter_name / include_classic）
#           ③ BLE(bleak) 与 Classic 分流
#   · 不照抄：本项目自己的纪律 → 授权=唯一闸门（Grant）、只读优先、操作全审计、读不到就说不确定
#   · 未使用其任何代码；仅取其公开描述的架构与 Windows 枚举思路
#
# 纪律:
#   - 只做本机蓝牙：不发起任何网络请求（无 URL 面，不涉 SSRF）
#   - 枚举来源逐条标注（pnp / registry / bleak / classic）；拿不到的字段写 None + 原因，绝不编
#   - 写操作（GATT 写、配对）必须持有效 Grant（授权=唯一闸门），且逐条落审计
import json
import os
import platform
import re
import subprocess
import sys
import time

IS_WINDOWS = platform.system() == "Windows"

# 极小的 OUI 表（只放**确定**的厂商前缀；不在表里就返回 "未知"，绝不猜）
OUI = {
    "1C7125": "Apple", "54423D": "Apple?（本机 BTHLE 实例）", "F0DBE2": "Apple",
    "A4C138": "Xiaomi", "64B473": "Xiaomi", "7CDFA1": "Espressif（ESP32）",
    "3C71BF": "Espressif（ESP32）", "246F28": "Espressif（ESP32）",
    "CC50E3": "Espressif（ESP32）", "B4E62D": "Espressif（ESP32）",
    "001A7D": "Sony", "FCF152": "Sony", "AC80FB": "Samsung", "F409D8": "Samsung",
    "DC2C6E": "Samsung", "0E8D" : "MediaTek（RZ608 适配器）",
}
_MAC_RE = re.compile(r"(?:DEV_|_)([0-9A-F]{12})(?:\\|$)")


def vendor_of(mac: str) -> str:
    """厂商：按 MAC 前 6 位查小表；查不到就「未知」（不编）。"""
    key = re.sub(r"[^0-9A-Fa-f]", "", mac or "").upper()[:6]
    return OUI.get(key, "未知")


def norm_mac(raw: str) -> str:
    """把 12 位十六进制规范成 AA:BB:CC:DD:EE:FF；不是 12 位就原样返回。"""
    hexd = re.sub(r"[^0-9A-Fa-f]", "", raw or "").upper()
    if len(hexd) != 12:
        return (raw or "").strip()
    return ":".join(hexd[i:i + 2] for i in range(0, 12, 2))


def _ps(script: str, timeout: float) -> tuple:
    """跑 PowerShell 并把输出**按 UTF-8 容错解码**（中文 Windows 默认 GBK，硬解会炸线程）。

    返回 (rc, stdout, stderr)。脚本前缀强制控制台输出 UTF-8，避免中文设备名乱码。
    """
    prelude = ("$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8;")
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", prelude + script],
            capture_output=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:                              # noqa: BLE001
        return -1, "", f"{type(exc).__name__}"
    dec = lambda b: (b or b"").decode("utf-8", errors="replace")   # noqa: E731
    return out.returncode, dec(out.stdout), dec(out.stderr)


def _loads(text: str):
    """容错解析 PowerShell 的 ConvertTo-Json 输出（空 → []）。"""
    t = (text or "").strip()
    if not t:
        return []
    try:
        return json.loads(t)
    except ValueError:
        return None


# ═══════════ ① Windows 原生枚举（PnP 设备类）═══════════
def scan_pnp(timeout: float = 20.0) -> dict:
    """Get-PnpDevice 枚举蓝牙类设备：适配器 / 已连接设备 / BLE GATT 子设备。

    返回 {"ok":bool, "devices":[...], "reason":str}；命令失败 → ok=False + 原因（不返回空当成功）。
    """
    if not IS_WINDOWS:
        return {"ok": False, "devices": [], "reason": "仅 Windows 支持此来源"}
    rc, stdout, stderr = _ps(
        "Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue | "
        "Select-Object Status,FriendlyName,InstanceId | ConvertTo-Json -Compress", timeout)
    if rc != 0:
        return {"ok": False, "devices": [],
                "reason": f"powershell rc={rc}: {(stderr or '').strip()[:120]}"}
    rows = _loads(stdout)
    if rows is None:
        return {"ok": False, "devices": [], "reason": "JSON 解析失败"}
    if isinstance(rows, dict):
        rows = [rows]
    devices = []
    for r in rows or []:
        inst = str(r.get("InstanceId") or "")
        name = str(r.get("FriendlyName") or "").strip()
        status = str(r.get("Status") or "").strip()
        m = _MAC_RE.search(inst)
        mac = norm_mac(m.group(1)) if m else None
        if inst.startswith("USB\\"):
            kind = "adapter"
        elif inst.startswith("BTHLE"):
            kind = "ble"
        elif inst.startswith("BTHENUM"):
            kind = "paired"
        else:
            kind = "other"
        # BLE GATT 子设备的 InstanceId 里带 128 位服务 UUID：是"服务"而不是"设备"
        svc = None
        ms = re.search(r"\{(0x)?([0-9A-Fa-f-]{36})\}", inst)
        if ms:
            svc = ms.group(2).upper()
        devices.append({"mac": mac, "name": name, "kind": kind, "status": status,
                        "service_uuid": svc, "instance": inst, "source": "pnp",
                        "vendor": vendor_of(mac or "") if mac else None})
    return {"ok": True, "devices": devices, "reason": ""}


# ═══════════ ② 注册表：已配对设备（BTHPORT）═══════════
def scan_registry(timeout: float = 10.0) -> dict:
    """读 HKLM BTHPORT\\Parameters\\Devices：已配对设备的 MAC（名字在子键 Name 值里）。"""
    if not IS_WINDOWS:
        return {"ok": False, "devices": [], "reason": "仅 Windows 支持此来源"}
    ps = ("$base='HKLM:\\SYSTEM\\CurrentControlSet\\Services\\BTHPORT\\Parameters\\Devices';"
          "if(!(Test-Path $base)){ '[]'; exit 0 };"
          "$out=@(); foreach($k in Get-ChildItem $base -ErrorAction SilentlyContinue){"
          "$n=$null; try{ $b=(Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue).Name;"
          " if($b){ $n=[Text.Encoding]::UTF8.GetString($b).Trim([char]0) } }catch{};"
          "$out += [pscustomobject]@{ mac=$k.PSChildName; name=$n } };"
          "$out | ConvertTo-Json -Compress")
    rc, stdout, stderr = _ps(ps, timeout)
    if rc != 0:
        return {"ok": False, "devices": [],
                "reason": f"rc={rc}: {(stderr or '').strip()[:120]}"}
    rows = _loads(stdout)
    if rows is None:
        return {"ok": False, "devices": [], "reason": "JSON 解析失败"}
    if isinstance(rows, dict):
        rows = [rows]
    devices = []
    for r in rows or []:
        mac = norm_mac(str(r.get("mac") or ""))
        if not mac or len(mac) != 17:
            continue
        devices.append({"mac": mac, "name": (r.get("name") or None), "kind": "paired",
                        "status": "paired", "service_uuid": None, "instance": None,
                        "source": "registry", "vendor": vendor_of(mac)})
    return {"ok": True, "devices": devices, "reason": ""}


def _run_async(coro):
    """在**已有事件循环**里也能跑（FastAPI/面板进程）：检测到运行中的 loop 就丢到线程里跑。

    这是真机踩到的坑：面板进程内 asyncio.run() 会直接抛
    "asyncio.run() cannot be called from a running event loop"，射频扫描就永远失败。
    """
    import asyncio
    import concurrent.futures
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)                          # 没有运行中的 loop：直接跑
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result(timeout=120)


# 这些名字是**协议/枚举/服务节点**，不是可操控设备（诚实分类，不当成设备报出来）
_SERVICE_HINT = ("Enumerator", "Protocol TDI", "Service", "服务", "RFCOMM",
                 "Phonebook", "NAP", "PAN ", "AVRCP", "HID ")


def is_service_node(name: str, kind: str) -> bool:
    n = str(name or "")
    if kind in ("adapter", "paired", "ble"):
        return False
    return any(h in n for h in _SERVICE_HINT)


# ═══════════ ③ 真实 BLE 射频扫描（bleak，可选依赖）═══════════
def scan_ble(timeout: float = 5.0, *, filter_name: str | None = None) -> dict:
    """真扫：bleak 扫周边 BLE 广播（带 RSSI）。未安装/失败 → ok=False + 原因（不假装扫到）。"""
    try:
        from bleak import BleakScanner
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "devices": [],
                "reason": f"未安装 bleak（{type(exc).__name__}）—— 可 pip install bleak 开启真实扫描"}

    async def _run():
        found = await BleakScanner.discover(timeout=max(1.0, float(timeout)), return_adv=True)
        out = []
        items = found.values() if isinstance(found, dict) else found
        for item in items:
            dev, adv = item if isinstance(item, tuple) else (item, None)
            name = getattr(dev, "name", None) or getattr(adv, "local_name", None)
            out.append({"mac": getattr(dev, "address", None), "name": name,
                        "kind": "ble", "status": "advertising",
                        "rssi": getattr(adv, "rssi", None) or getattr(dev, "rssi", None),
                        "service_uuid": (list(getattr(adv, "service_uuids", []) or [])[:1] or [None])[0],
                        "instance": None, "source": "bleak",
                        "vendor": vendor_of(getattr(dev, "address", "") or "")})
        return out

    try:
        devices = _run_async(_run())
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "devices": [], "reason": f"扫描失败 {type(exc).__name__}: {exc}"[:160]}
    if filter_name:
        key = filter_name.lower()
        devices = [d for d in devices if key in str(d.get("name") or "").lower()]
    return {"ok": True, "devices": devices, "reason": ""}


# ═══════════ ④ 适配器状态 ═══════════
def adapters() -> dict:
    pnp = scan_pnp()
    if not pnp["ok"]:
        return {"ok": False, "count": None, "list": [], "reason": pnp["reason"]}
    ads = [d for d in pnp["devices"] if d["kind"] == "adapter"]
    return {"ok": True, "count": len(ads),
            "list": [{"name": d["name"], "status": d["status"], "instance": d["instance"]}
                     for d in ads], "reason": ""}


# ═══════════ ⑤ 合并扫描（AI 工具面：duration / filter_name / include_classic）═══════════
def scan(duration: float = 5.0, *, filter_name: str | None = None,
         include_classic: bool = True, rf: bool = True) -> dict:
    """多源合并：registry(已配对) + pnp(在册/连接) + 可选 bleak(射频真扫)。

    合并键 = MAC；每台设备带 sources 列表（哪个来源看到的），字段缺失写 None。
    """
    sources, errors = [], []
    reg = scan_registry()
    (sources if reg["ok"] else errors).append("registry" if reg["ok"] else
                                             {"source": "registry", "reason": reg["reason"]})
    pnp = scan_pnp()
    (sources if pnp["ok"] else errors).append("pnp" if pnp["ok"] else
                                             {"source": "pnp", "reason": pnp["reason"]})
    ble = {"ok": False, "devices": [], "reason": "未启用射频扫描"}
    if rf:
        ble = scan_ble(duration, filter_name=filter_name)
        (sources if ble["ok"] else errors).append("bleak" if ble["ok"] else
                                                 {"source": "bleak", "reason": ble["reason"]})
    merged: dict = {}
    for src, res in (("registry", reg), ("pnp", pnp), ("bleak", ble)):
        if not res.get("ok"):
            continue
        for d in res["devices"]:
            if d.get("kind") == "adapter" or is_service_node(d.get("name"), d.get("kind")):
                continue                                   # 适配器/协议服务节点不是可操控设备
            # GATT 服务子条目（有 service_uuid 且没有名字）也不是设备本身：
            # 先照常归并，最后只在"没有任何其他来源认识这个 MAC"时才丢弃。
            key = d.get("mac") or f"{src}:{d.get('instance') or d.get('name')}"
            cur = merged.setdefault(key, {"mac": d.get("mac"), "name": None, "kinds": set(),
                                          "sources": [], "rssi": None, "vendor": None,
                                          "status": None, "service_uuid": None,
                                          "gatt_only": True})
            if d.get("name"):
                cur["name"] = d["name"]
                cur["gatt_only"] = False
            if not d.get("service_uuid"):
                cur["gatt_only"] = False
            cur["kinds"].add(d.get("kind"))
            cur["sources"].append(src)
            if d.get("rssi") is not None:
                cur["rssi"] = d["rssi"]
            if not cur["vendor"] and d.get("vendor"):
                cur["vendor"] = d["vendor"]
            if d.get("status"):
                cur["status"] = d["status"]
            if not cur["service_uuid"] and d.get("service_uuid"):
                cur["service_uuid"] = d["service_uuid"]
    rows, gatt_only = [], 0
    for cur in merged.values():
        kinds = {k for k in cur["kinds"] if k}
        if cur.get("gatt_only"):
            gatt_only += 1
            continue                                        # 只被 GATT 子条目提到 → 不算设备
        rows.append({"mac": cur["mac"], "name": cur["name"],
                     "kind": ("ble" if "ble" in kinds else
                              ("paired" if "paired" in kinds else
                               (sorted(kinds)[0] if kinds else "other"))),
                     "kinds": sorted(kinds), "sources": sorted(set(cur["sources"])),
                     "rssi": cur["rssi"], "vendor": cur["vendor"],
                     "status": cur["status"], "service_uuid": cur["service_uuid"]})
    if filter_name:
        key = filter_name.lower()
        rows = [r for r in rows if key in str(r.get("name") or "").lower()]
    if not include_classic:
        rows = [r for r in rows if r["kind"] != "classic"]
    rows.sort(key=lambda r: (-(r["rssi"] or -999), str(r.get("name") or "")))
    return {"ok": True, "count": len(rows), "devices": rows,
            "gatt_service_rows": gatt_only,
            "sources": [s for s in sources if isinstance(s, str)],
            "errors": [e for e in errors if isinstance(e, dict)],
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


# ═══════════ ⑥ 写操作：GATT 写 / 读（必须持 Grant；授权=唯一闸门）═══════════
class BLEError(Exception):
    pass


def _grant_ok(grant, action: str, target: str) -> tuple:
    """授权检查：Grant 必须存在、未过期、且允许该动作与目标。没有 Grant → 一律拒绝。

    兼容两种形态（踩过的坑）：
      ① 正规签发的**令牌字符串**（core.gui_grant.issue 的产物）/ Grant 实例；
      ② 自带 verify_action(dict) 的**适配器对象**。
    以前只认形态②，而正规签发的是形态① → 每次都 TypeError 被 except 吞成"未授权"，
    等于**把写路径永久焊死**（接线参数种类错的隐蔽断线）。两种都要放行才算真打通。
    """
    if not grant:
        return False, "未授权：蓝牙写操作必须持 Grant（授权=唯一闸门）"
    # 形态①：令牌字符串 / Grant 实例 → 走 gui_grant 的正规校验
    tok = grant if isinstance(grant, str) else getattr(grant, "token", None)
    if isinstance(tok, str):
        try:
            from core.gui_grant import Grant
            ok, why, _ = Grant.verify_action(tok, primitive="ble.write", app="ble")
            return bool(ok), "" if ok else str(why)
        except Exception as exc:                          # noqa: BLE001
            return False, f"授权校验异常 {type(exc).__name__}"
    # 形态②：自带 verify_action(dict) 的适配器
    try:
        res = grant.verify_action({"app": "ble", "action": action, "target": target,
                                   "primitives": ["ble.write"]})
        if isinstance(res, tuple):
            return bool(res[0]), str(res[1] if len(res) > 1 else "")
        if isinstance(res, dict):
            return bool(res.get("ok")), str(res.get("reason", ""))
        return bool(res), ""
    except Exception as exc:                              # noqa: BLE001
        return False, f"授权校验异常 {type(exc).__name__}"


def gatt_write(address: str, uuid: str, data_hex: str, *, grant=None,
               timeout: float = 15.0) -> dict:
    """向 BLE 特征写数据（真发）。data 是十六进制串。未授权/失败一律如实返回。"""
    ok, why = _grant_ok(grant, "gatt_write", address)
    if not ok:
        return {"ok": False, "reason": why}
    raw = re.sub(r"[\s:\-]", "", data_hex or "")
    if not raw:
        return {"ok": False, "reason": "空数据：拒绝发送"}
    if not re.fullmatch(r"[0-9A-Fa-f]+", raw):
        return {"ok": False, "reason": "数据不是合法十六进制：拒绝发送"}
    payload = bytes.fromhex(raw if len(raw) % 2 == 0 else raw + "0")
    try:
        import asyncio
        from bleak import BleakClient
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "reason": f"未安装 bleak（{type(exc).__name__}）"}

    async def _run():
        async with BleakClient(address, timeout=timeout) as client:
            await client.write_gatt_char(uuid, payload, response=True)
            return True

    try:
        asyncio.run(_run())
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:160],
                "address": address, "uuid": uuid}
    return {"ok": True, "address": address, "uuid": uuid, "bytes": len(payload),
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


def gatt_read(address: str, uuid: str, *, grant=None, timeout: float = 15.0) -> dict:
    """读 BLE 特征（真读）。读操作也走授权闸门（统一纪律，避免绕过）。"""
    ok, why = _grant_ok(grant, "gatt_read", address)
    if not ok:
        return {"ok": False, "reason": why}
    try:
        import asyncio
        from bleak import BleakClient
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "reason": f"未安装 bleak（{type(exc).__name__}）"}

    async def _run():
        async with BleakClient(address, timeout=timeout) as client:
            return bytes(await client.read_gatt_char(uuid))

    try:
        data = asyncio.run(_run())
    except Exception as exc:                              # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"[:160]}
    return {"ok": True, "address": address, "uuid": uuid,
            "hex": data.hex(), "bytes": len(data)}


def status() -> dict:
    """蓝牙总体状态：适配器 + 可用来源（真实可观测，缺什么写什么）。"""
    ad = adapters()
    try:
        import bleak                                    # noqa: F401
        rf = {"ok": True, "reason": ""}
    except Exception as exc:                              # noqa: BLE001
        rf = {"ok": False, "reason": f"未安装 bleak（{type(exc).__name__}）"}
    return {"平台": platform.system(), "适配器": ad,
            "射频扫描": rf, "只读优先": True, "授权是唯一闸门": True,
            "写操作需": "Grant(ble.write)", "审计表": "ble_ops"}


def main() -> int:                                        # pragma: no cover - CLI
    """命令行自检：python -m senses.ble  → 打印真实枚举结果（不外发、不写任何东西）。"""
    print(json.dumps(status(), ensure_ascii=False, indent=1))
    res = scan(duration=float(os.environ.get("BLE_SCAN_S", "4")), rf=True)
    print(json.dumps(res, ensure_ascii=False, indent=1)[:4000])
    return 0


if __name__ == "__main__":                                # pragma: no cover
    sys.exit(main())
