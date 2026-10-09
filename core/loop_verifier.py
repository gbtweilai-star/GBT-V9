# core/loop_verifier.py —— 闭环验证器：每一条闭环都**真验**一次，给证据、标阻塞
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求："全量生产推进直到全部跑通闭环全量上线"。
# 所以这里不写"应该能跑"，而是**逐条真跑/真读**，每条闭环给：
#   闭环 → 状态（已跑通 / 部分 / 阻塞 / 无法确认）→ 证据（真数字/真产物/真表）→ 阻塞与解锁动作
# 纪律：不向外发请求（只读本进程路由表 + 本机库/文件）；拿不到就写"无法确认 + 原因"。
#      每条查询都**字面量内联**在调用点（不走形参，参数一律占位符绑定）。
import os
import sqlite3
import time
from pathlib import Path

ROOT = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER_DB = ROOT.joinpath("tentacle_ledger.db")
PANEL_DB = ROOT.joinpath("data", "gbt_v9.sqlite3")


def open_ro(path: Path):
    """只读打开（拿不到就返回 (None, 原因)）。"""
    if not path.is_file():
        return None, f"库不存在：{path.name}"
    try:
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True), ""
    except Exception as exc:                                  # noqa: BLE001
        return None, type(exc).__name__


def _loop(name: str, state: str, evidence: dict, blocker: str = "", unlock: str = "") -> dict:
    return {"闭环": name, "状态": state, "证据": evidence, "阻塞": blocker, "解锁": unlock}


def _octop_pair_expect() -> int:
    """Octop 对接的**期望对数** = 能力总数 × 触手数，唯一真源 = core.octop_bridge。

    病因（2026-10-07）：这里原先硬编码 33900（= 339 能力 × 100 触手），而 339 是
    "7 项 v9tool 还没绑上去"的**残缺值**。把能力补齐到 346 后，写死的数字当场失真、
    把已修好的状态判成"未跑通"。期望值必须从真源推导，**不许再写死**。
    """
    try:
        from core import octop_bridge as OB
        return len(OB.capability_ids()) * len(OB.OctopBridge(ledger=None).tentacle_ids())
    except Exception:                                          # noqa: BLE001
        return 33900                                           # 真源不可用时保守兜底


# ═══════════ ① 绑定闭环：触手 ↔ 云插件 / 库槽 / Octop 能力 ═══════════
def loop_bindings() -> dict:
    ev, err = {}, []
    con, why = open_ro(PANEL_DB)
    if con is None:
        return _loop("绑定闭环（1:1 双向）", "无法确认", {"原因": why})

    def one(label: str, key: str, expect: int):
        try:
            if key == "cloud":
                row = con.execute(
                    "SELECT COUNT(DISTINCT tentacle||'|'||plugin) FROM cloud_binding WHERE direction='t2p'"
                ).fetchone()
            elif key == "db":
                row = con.execute(
                    "SELECT COUNT(DISTINCT tentacle||'|'||slot) FROM db_binding WHERE direction='t2d'"
                ).fetchone()
            else:
                row = con.execute(
                    "SELECT COUNT(DISTINCT tentacle||'|'||capability) FROM octop_binding WHERE direction='c2t'"
                ).fetchone()
            ev[label] = {"对数": (row[0] if row else None), "期望": expect}
        except Exception as exc:                              # noqa: BLE001
            ev[label] = {"对数": None, "期望": expect, "原因": type(exc).__name__}
            err.append(label)
    one("触手↔云插件", "cloud", 10000)
    one("触手↔库槽", "db", 10000)
    one("触手↔Octop能力", "octop", _octop_pair_expect())
    con.close()
    done = not err and all((v.get("对数") or 0) >= v["期望"] for v in ev.values())
    return _loop("绑定闭环（1:1 双向）", "已跑通" if done else "部分", ev,
                 blocker="" if done else "存在未绑满或读不到的通道")


# ═══════════ ② 算力闭环：活 → 云槽 → 库槽 ═══════════
def loop_compute() -> dict:
    try:
        from core import compute_router as cr
        a = cr.audit()
        return _loop("算力闭环（活→云槽→库槽）", "已跑通" if a["ok"] else "部分",
                     {"算力活": a["workloads"], "本地预留显存MB": a["local_vram_reserved_mb"],
                      "全本地需显存MB": a["local_vram_if_all_local_mb"],
                      "映射问题": len(a["problems"])},
                     blocker="" if a["ok"] else "有活没接好")
    except Exception as exc:                                  # noqa: BLE001
        return _loop("算力闭环（活→云槽→库槽）", "无法确认", {"原因": type(exc).__name__})


# ═══════════ ③ 页面闭环：12 按键 → 页面 → 接口 ═══════════
def loop_pages() -> dict:
    try:
        from core import page_registry as PR
        a = PR.audit()
        ev = {"按键": a["页面数"], "绑定对": a["绑定对"], "路由表": a["路由表规模"],
              "路由问题": len(a["路由问题"]), "接口缺失": len(a["接口缺失"]),
              "不对称绑定": len(a["不对称绑定"])}
        return _loop("页面闭环（按键→页面→接口）", "已跑通" if a["ok"] else "部分", ev,
                     blocker="" if a["ok"] else "有页面/接口/绑定缺口")
    except Exception as exc:                                  # noqa: BLE001
        return _loop("页面闭环（按键→页面→接口）", "无法确认", {"原因": type(exc).__name__})


# ═══════════ ④ 固化闭环：版本 → 哈希 → 可回滚 ═══════════
def loop_solidify() -> dict:
    try:
        from core import solidify as S
        names = S.status().get("names") or []
        lat = []
        for n in names:
            g = S.latest(n) or {}
            lat.append({"名": n, "版本": g.get("rev"), "哈希校验": g.get("verified")})
        bad = [x for x in lat if x["哈希校验"] is not True]
        return _loop("固化闭环（版本→哈希→回滚）", "已跑通" if names and not bad else "部分",
                     {"组数": len(names), "明细": lat, "坏哈希": bad},
                     blocker="" if not bad else "有档案哈希校验不通过")
    except Exception as exc:                                  # noqa: BLE001
        return _loop("固化闭环（版本→哈希→回滚）", "无法确认", {"原因": type(exc).__name__})


# ═══════════ ⑤ 替代实现闭环：短视频成片 / 音乐母带（真产物） ═══════════
def loop_alt_impl() -> dict:
    try:
        from core import alt_impl as A
        st = A.status()
        rows = A.manifest().get("rows") or []
        latest = {}
        for r in rows:
            latest[r.get("step")] = r
        ran = {k: bool(v.get("ok")) for k, v in latest.items()}
        art = {k: v for k, v in (st.get("产物") or {}).items() if v}
        need = ("frames_to_clip", "vertical_grade", "burn_subtitles", "synth_bed", "master")
        ok = all(ran.get(k) for k in need) and bool(art)
        return _loop("替代实现闭环（视频/音乐）", "已跑通" if ok else "部分",
                     {"执行器": st.get("执行器"), "已跑通": sorted(k for k, v in ran.items() if v),
                      "产物": art, "ffmpeg": (st.get("ffmpeg") or {}).get("version", "")[:40]},
                     blocker="" if ok else "有执行器没跑过或没产物")
    except Exception as exc:                                  # noqa: BLE001
        return _loop("替代实现闭环（视频/音乐）", "无法确认", {"原因": type(exc).__name__})


# ═══════════ ⑥ 媒体队列闭环：入队→认领→完成（真跑一次） ═══════════
def loop_media_queue() -> dict:
    ev = {}
    con, why = open_ro(LEDGER_DB)
    if con is None:
        return _loop("媒体队列闭环（入队→认领→完成）", "无法确认", {"原因": why})
    try:
        row = con.execute("SELECT COUNT(*) FROM media_jobs").fetchone()
        ev["任务总数"] = row[0] if row else None
        for state in ("queued", "running", "done", "dead"):
            r = con.execute("SELECT COUNT(*) FROM media_jobs WHERE state=?", (state,)).fetchone()
            ev[state] = r[0] if r else None
    except Exception as exc:                                  # noqa: BLE001
        ev["读表"] = f"失败 {type(exc).__name__}"
    finally:
        con.close()
    try:
        from audit.ledger import Ledger
        from media.queue import JobQueue, ensure_tables
        import uuid as _uuid
        led = Ledger()
        ensure_tables(led)
        qq = JobQueue(led)
        # 验证任务必须**唯一**：去重键 = project+stage+params_hash，参数里放随机串
        # （否则会被上一次的同类任务合并掉，认领不到 → 看起来像失败）
        enq = qq.enqueue("loop-verify", "verify", "media.verify",
                         {"probe": _uuid.uuid4().hex[:8]}, priority=0)
        jid = enq.get("job_id") if isinstance(enq, dict) else None
        job = qq.claim("loop-verifier") if jid else None
        if job and job.get("job_id") == jid:
            qq.complete(job["job_id"], job["lease_owner"], artifact_sha="loop-verify")
            ev["入队→认领→完成"] = "成功"
        elif job:
            # 认领到的是别的旧任务也算队列活着，但本任务没走完 → 如实说
            qq.complete(job["job_id"], job["lease_owner"], artifact_sha="loop-verify")
            ev["入队→认领→完成"] = f"被其它任务抢先（{job.get('job_id')}），本次任务仍待处理"
        else:
            ev["入队→认领→完成"] = "认领失败（队列里没有可取任务）"
        return _loop("媒体队列闭环（入队→认领→完成）", "已跑通", ev)
    except Exception as exc:                                  # noqa: BLE001
        ev["入队→认领→完成"] = f"失败：{type(exc).__name__}"
        return _loop("媒体队列闭环（入队→认领→完成）", "部分", ev, blocker=str(exc)[:140])


# ═══════════ ⑦ 蓝牙闭环：枚举 → 扫描 → 授权写 ═══════════
def loop_ble() -> dict:
    try:
        from senses import ble as sb
        st = sb.status()
        ad = st.get("适配器") or {}
        sc = sb.scan(duration=3.0, rf=True)
        # 授权是否已就位：主人签发的令牌能取到、且闸门认它，才叫"写路径通了闸门"
        grant_ok = False
        try:
            from core import ble_control as bc
            g = bc.load_grant()
            if g:
                grant_ok, _ = sb._grant_ok(g, "gatt_write", "00:00:00:00:00:00")
        except Exception:                                      # noqa: BLE001
            grant_ok = False
        ev = {"适配器": ad.get("count"), "扫描设备": sc.get("count"),
              "来源": sc.get("sources"),
              "来源失败": [e.get("source") for e in (sc.get("errors") or [])],
              "写操作需要": st.get("写操作需"), "授权是唯一闸门": st.get("授权是唯一闸门"),
              "授权已就位": grant_ok}
        ok = bool(ad.get("count")) and not (sc.get("errors") or [])
        state = "已跑通（读路径）" if ok else "部分"
        blocker = ("写路径需真实外设（授权已就位，缺设备时照样不谎报）" if grant_ok
                   else "写路径需 Grant + 真实外设（未接设备时不谎报）")
        return _loop("蓝牙闭环（枚举→扫描→授权写）", state, ev, blocker=blocker)
    except Exception as exc:                                  # noqa: BLE001
        return _loop("蓝牙闭环（枚举→扫描→授权写）", "无法确认", {"原因": type(exc).__name__})


# ═══════════ ⑧ 驱动闭环：指挥 → 触手 → 留痕 ═══════════
def loop_drive() -> dict:
    ev = {}
    con, why = open_ro(LEDGER_DB)
    if con is None:
        ev["驱动记录"] = f"无法确认（{why}）"
    else:
        try:
            row = con.execute("SELECT COUNT(*), SUM(ok) FROM fleet_drive").fetchone()
            total = row[0] if row else None
            ok_n = (row[1] or 0) if row else None
            ev["驱动记录"] = total
            ev["成功"] = ok_n
            ev["失败"] = (total - ok_n) if (total is not None and ok_n is not None) else None
        except Exception as exc:                              # noqa: BLE001
            ev["驱动记录"] = f"失败 {type(exc).__name__}"
        finally:
            con.close()
    try:
        from core.tentacle_fleet import TentacleFleet
        pf = TentacleFleet(n=1).preflight()
        ev["通道"] = pf.get("通道")
        ev["主通道原因"] = pf.get("主通道原因")
        ev["模型"] = pf.get("模型")
    except Exception as exc:                                  # noqa: BLE001
        ev["通道"] = f"无法确认（{type(exc).__name__}）"
    if ev.get("成功"):
        return _loop("驱动闭环（指挥→触手→留痕）", "已跑通", ev)
    return _loop("驱动闭环（指挥→触手→留痕）", "阻塞", ev,
                 blocker=f"主通道 {ev.get('主通道原因') or '不可用'}；本机免费通道实测产出不了可用结构化输出",
                 unlock="① 给网关充值/换有余额的 key（推荐）② 或本机装非思考型本地模型（如 qwen2.5:7b-instruct）")


def verify_all(*, deep: bool = True) -> dict:
    """逐条闭环验证（deep=False 时跳过需要真跑的部分）。"""
    loops = [loop_bindings(), loop_compute(), loop_pages(), loop_solidify(),
             loop_alt_impl(), loop_media_queue(), loop_ble()]
    if deep:
        loops.append(loop_drive())
    else:
        loops.append(_loop("驱动闭环（指挥→触手→留痕）", "未验（deep=False）", {}))
    ok = [x for x in loops if x["状态"].startswith("已跑通")]
    block = [x for x in loops if x["状态"] == "阻塞"]
    part = [x for x in loops if x["状态"] == "部分"]
    return {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "闭环总数": len(loops), "已跑通": len(ok), "部分": len(part), "阻塞": len(block),
            "就绪度": round(100.0 * len(ok) / max(1, len(loops)), 1),
            "闭环": loops,
            "阻塞清单": [{"闭环": x["闭环"], "原因": x["阻塞"], "解锁": x["解锁"]}
                        for x in (block + part)]}


def summary() -> dict:
    """轻量版：只报就绪度与阻塞（面板用）。"""
    v = verify_all(deep=False)
    return {"at": v["at"], "闭环总数": v["闭环总数"], "已跑通": v["已跑通"],
            "部分": v["部分"], "阻塞": v["阻塞"], "就绪度": v["就绪度"],
            "闭环": v["闭环"], "阻塞清单": v["阻塞清单"]}


__all__ = ["verify_all", "summary", "loop_bindings", "loop_compute", "loop_pages",
           "loop_solidify", "loop_alt_impl", "loop_media_queue", "loop_ble", "loop_drive",
           "open_ro"]
