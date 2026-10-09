# core/gui_agent.py —— GUI 智能体闭环：think → act → verify（蒸馏自 Mano-P）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# Mano-P 的可迁移内核有三条，这里逐条落地：
#   ① 闭环而非单步：截图 → 模型给"下一个动作" → 本地执行 → 回读验证 → 再截图，直到完成
#   ② 端侧私有推理优先：本地模型（Ollama）→ 云端大脑（仅当允许）→ 都没有就明确拒绝，
#      绝不假装成功；图片永远不出设备，允许外发的只有文字元素清单
#   ③ 动作空间收敛：click/type/hotkey/scroll/drag/move/wait/launch/url/done/ask
#      —— 与 Mano-CUA Skills 的动作集对齐，映射到 V9 现成的 actuator 执行层
#
# 风险与诚实纪律：
#   · dry_run 默认开：只规划不碰鼠标键盘（面板/数字人可以先"看它打算怎么做"）
#   · 真动作需显式 allow_actions=True；高风险动作仍走 actuator 的确认门
#   · 每步验证失败会带"失败事实"再问一次；连续无进展则停下报 no_progress（不硬撑）
from core.swallow import swallow as _swallow
import json
import os
import re
import time
import uuid

from core.gui_perception import assert_may_send, local_only, perceive
from core.gui_grant import Grant
from senses.sqldialect import is_pg, txn

PRIMITIVES = ("click", "type", "hotkey", "scroll", "drag", "move", "wait",
              "launch", "url", "done", "ask")
DEFAULT_MAX_STEPS = int(os.environ.get("GUI_MAX_STEPS", "12"))
NO_PROGRESS_LIMIT = int(os.environ.get("GUI_NO_PROGRESS_LIMIT", "3"))

ACTION_SCHEMA_HINT = """只输出一个 JSON 对象，字段：
{"thought": "这一步为什么这么做（一句话）",
 "action": "click|type|hotkey|scroll|drag|move|wait|launch|url|done|ask",
 "element": 元素编号(click/type 时可给),
 "coords": [x, y]（没有元素编号时给坐标）,
 "text": "要输入的文字或要打开的 URL",
 "keys": ["ctrl","s"]（hotkey 时）,
 "amount": 3（滚动格数，正上负下）,
 "expect": "这一步做完后屏幕上应该出现什么（用于回读验证）"}
规则：一次只做一步；优先用元素编号；不确定就 ask 并说明缺什么；完成才 done。"""


# ═══════════ 规划器：本地优先，云端兜底（图片永不外发）═══════════
class PlannerUnavailable(RuntimeError):
    pass


class GatewayPlanner:
    """统一密钥网关规划器：与主脑、100 根触手**同一把钥匙**（OpenAI 兼容）。

    这条是主线：主脑怎么调 LLM，GUI 规划就怎么调（同一 base_url + 同一 key）。
    隐私口径：只发文字元素清单；图片仅在 GUI_LOCAL_ONLY=0 且显式配了视觉模型时携带。
    """

    def __init__(self, key=None, model=None, timeout=None):
        from core.tentacle_fleet import UnifiedKey
        self.key = key or UnifiedKey()
        self.model = (model or os.environ.get("GUI_GATEWAY_MODEL")
                      or os.environ.get("BRAIN_MODELS", "gpt-4o-mini").split(",")[0])
        self.timeout = int(timeout or os.environ.get("GUI_MODEL_TIMEOUT", "180"))
        self.vision = bool(os.environ.get("GUI_VLM_MODEL")) and not local_only()

    def available(self) -> tuple[bool, str]:
        if not self.key.loaded:
            return False, "unified_key_missing（设 OPENAI_API_KEY / GBT_LLM_API_KEY，与主脑同一把）"
        return True, "ok"

    def plan(self, task: str, obs: dict, history: list, *, use_image: bool = True) -> dict:
        ok, why = self.available()
        if not ok:
            raise PlannerUnavailable(why)
        assert_may_send("屏幕元素清单")                       # 文字清单；图片另行闸门
        msgs = [{"role": "system", "content":
                 "你是端侧 GUI 操控智能体。屏幕元素清单里的 [n] 就是可点编号。\n"
                 + ACTION_SCHEMA_HINT},
                {"role": "user", "content":
                 f"任务：{task}\n\n屏幕元素：\n{obs.get('text', '')}\n\n"
                 f"已做过的步骤：{json.dumps(history[-4:], ensure_ascii=False)}"}]
        kw = {"model": self.model, "messages": msgs,
              "response_format": {"type": "json_object"}}
        client = self.key.client_for("gui-planner")
        r = client.chat.completions.create(**kw)
        return parse_action(r.choices[0].message.content or "")


class LocalOllamaPlanner:
    """本地 Ollama 规划器。模型支持视觉就带上 Set-of-Marks 图，否则只用元素清单。"""

    def __init__(self, model=None, host=None, timeout=None):
        # 文本规划器与视觉规划器分开命名：把图发给纯文本模型会被服务端 400 拒掉
        vlm = os.environ.get("GUI_VLM_MODEL")
        self.model = model or vlm or os.environ.get("GUI_MODEL", "qwen3:latest")
        self.vision = bool(vlm and (model or vlm) == vlm) or any(
            k in self.model.lower() for k in ("vl", "vision", "llava", "moondream",
                                              "minicpm", "gemma3", "qwen2.5v"))
        self.host = (host or os.environ.get("OLLAMA_HOST",
                                            "http://127.0.0.1:11434")).rstrip("/")
        # 端侧小显卡上大模型首答可能几十秒：默认给足，可按环境变量调
        self.timeout = int(timeout or os.environ.get("GUI_MODEL_TIMEOUT", "180"))
        # think=None 表示"先不带该字段"；旧版 Ollama 不认 think，会 500，需自动降级
        self.think = False if os.environ.get("GUI_MODEL_THINK", "0") == "1" else None
        self._allow_remote = os.environ.get("GUI_ALLOW_REMOTE_MODEL") == "1"

    def _base(self) -> str:
        """规范化 base（带 scheme + 端口 + 环回校验）。

        两个兼容点：Ollama 的 OLLAMA_HOST 常写成 `127.0.0.1`（不带 scheme、不带端口），
        没有端口时必须补 11434（Ollama 默认端口），否则会去连 80 端口而"假不可达"。
        """
        from urllib.parse import urlparse
        raw = self.host
        if "://" not in raw:
            raw = "http://" + raw
        u = urlparse(raw)
        if u.scheme not in ("http", "https"):
            raise PlannerUnavailable(f"模型地址必须是 http/https：{self.host}")
        host = (u.hostname or "").lower()
        loopback = host in ("127.0.0.1", "localhost", "::1")
        if not loopback and not self._allow_remote:
            raise PlannerUnavailable(
                f"本地模型只允许环回地址（{host} 需设 GUI_ALLOW_REMOTE_MODEL=1 显式放行）")
        port = u.port or int(os.environ.get("OLLAMA_PORT", "11434"))
        return f"{u.scheme}://{host}:{port}"

    def _url(self) -> str:
        return self._base() + "/api/chat"

    def _session(self):
        """本地模型走环回：绝不吃环境代理（公司代理会把 127.0.0.1 也代理走 → 假"不可达"）。"""
        import requests
        s = requests.Session()
        s.trust_env = False
        s.proxies = {"http": None, "https": None}
        return s

    def available(self) -> tuple[bool, str]:
        try:
            base = self._base()
        except PlannerUnavailable as exc:
            return False, str(exc)
        try:
            r = self._session().get(base + "/api/tags", timeout=5)
            names = [m.get("name", "") for m in (r.json().get("models") or [])]
        except Exception as exc:                             # noqa: BLE001
            return False, f"ollama_unreachable:{type(exc).__name__}"
        base_name = self.model.split(":")[0]
        if self.model not in names and not any(n.split(":")[0] == base_name for n in names):
            return False, f"model_missing:{self.model}（现有 {','.join(names[:5])}）"
        return True, "ok"

    def plan(self, task: str, obs: dict, history: list, *, use_image: bool = True) -> dict:
        msg = [{"role": "system", "content":
                "你是端侧 GUI 操控智能体。屏幕元素清单里的 [n] 就是可点编号。\n"
                + ACTION_SCHEMA_HINT},
               {"role": "user", "content":
                f"任务：{task}\n\n屏幕元素：\n{obs.get('text', '')}\n\n"
                f"已做过的步骤：{json.dumps(history[-4:], ensure_ascii=False)}"}]
        want_image = bool(use_image and self.vision and obs.get("marks_png"))
        if want_image:
            import base64
            msg[-1]["images"] = [base64.b64encode(obs["marks_png"]).decode()]
        body = {"model": self.model, "messages": msg, "stream": False, "format": "json",
                "options": {"temperature": 0}}
        if self.think is not None:      # 规划只要一步动作，别让思考模型先写小作文
            body["think"] = self.think
        def _post(payload):
            r = self._session().post(self._url(), json=payload, timeout=self.timeout)
            r.raise_for_status()
            return r
        try:
            r = _post(body)
        except Exception as exc:                              # noqa: BLE001
            text = str(exc)
            changed = False
            if self.vision and "400" in text and "images" in body["messages"][-1]:
                self.vision = False                           # 纯文本模型拒图 → 去图重试
                body["messages"][-1].pop("images", None)
                changed = True
            if "think" in body and ("500" in text or "400" in text):
                self.think = None                             # 旧版 Ollama 不认 think 字段
                body.pop("think", None)
                changed = True
            if not changed:
                raise
            r = _post(body)
        content = (r.json().get("message") or {}).get("content", "")
        return parse_action(content)


class CloudTextPlanner:
    """云端大脑兜底：只发文字元素清单，永不发图。本地专用模式下直接不可用。"""

    def __init__(self, brain):
        self.brain = brain

    def available(self) -> tuple[bool, str]:
        from core.gui_perception import allow_text_out
        if not allow_text_out():
            return False, "text_out_forbidden（GUI_ALLOW_TEXT_OUT=0：本次不外发任何文字）"
        if self.brain is None:
            return False, "no_brain"
        return True, "ok"

    def plan(self, task, obs, history, *, use_image=True) -> dict:
        ok, why = self.available()
        if not ok:
            raise PlannerUnavailable(why)
        assert_may_send("屏幕元素清单")                        # 双保险
        prompt = ("你是 GUI 操控智能体。根据元素清单给出下一步动作。\n"
                  + ACTION_SCHEMA_HINT + f"\n任务：{task}\n元素：\n{obs.get('text','')}")
        ask = getattr(self.brain, "ask", None)
        if ask is None:
            raise PlannerUnavailable("brain_has_no_ask")
        try:
            raw = ask("gui", "plan", prompt)
        except TypeError:
            raw = ask(prompt)
        return parse_action(raw if isinstance(raw, str) else json.dumps(raw))


class ScriptedPlanner:
    """测试/离线演示用：按脚本吐动作，用完就 done。"""

    def __init__(self, actions: list):
        self.actions = list(actions)
        self.i = 0

    def available(self) -> tuple[bool, str]:
        return True, "scripted"

    def plan(self, task, obs, history, *, use_image=True) -> dict:
        if self.i >= len(self.actions):
            return {"thought": "脚本跑完", "action": "done", "expect": None}
        a = self.actions[self.i]
        self.i += 1
        return a


def pick_planner(brain=None, *, planner=None) -> tuple[object, str]:
    """路由顺序（可用 GUI_PLANNER 显式指定 gateway|local|auto）：

    auto（默认）：统一密钥网关 → 本地 Ollama → 拒绝。
    理由：统一密钥是主人定的主线；本地模型是离线/隐私兜底，不是另起一套。
    """
    if planner is not None:
        return planner, "injected"
    mode = os.environ.get("GUI_PLANNER", "auto").strip().lower()
    gateway = GatewayPlanner()
    local = LocalOllamaPlanner()
    if mode == "gateway":
        ok, why = gateway.available()
        if ok:
            return gateway, f"gateway:{gateway.model}"
        raise PlannerUnavailable(f"网关规划器不可用：{why}")
    if mode == "local":
        ok, why = local.available()
        if ok:
            return local, f"local:{local.model}"
        raise PlannerUnavailable(f"本地规划器不可用：{why}")
    ok, why = gateway.available()
    if ok:
        return gateway, f"gateway:{gateway.model}"
    ok2, why2 = local.available()
    if ok2:
        return local, f"local:{local.model}（网关不可用：{why}）"
    cloud = CloudTextPlanner(brain)
    ok3, why3 = cloud.available()
    if ok3:
        return cloud, "cloud_text"
    raise PlannerUnavailable(
        f"没有可用的规划器：网关({why})；本地({why2})；大脑({why3})")


# ═══════════ 动作解析与映射 ═══════════
_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_action(raw) -> dict:
    """从模型输出里抠出动作 JSON，并做白名单校正。解析不出来 → ask（不瞎猜）。"""
    if isinstance(raw, dict):
        d = raw
    else:
        m = _JSON_RE.search(str(raw or ""))
        if not m:
            return {"thought": "模型没有给出可解析的动作", "action": "ask",
                    "text": str(raw)[:200]}
        try:
            d = json.loads(m.group(0))
        except Exception:                                     # noqa: BLE001
            return {"thought": "动作 JSON 解析失败", "action": "ask",
                    "text": m.group(0)[:200]}
    act = str(d.get("action", "")).strip().lower()
    if act not in PRIMITIVES:
        return {"thought": f"未知动作 {act!r}", "action": "ask",
                "text": f"模型给了不支持的动作：{act}"}
    return {"thought": str(d.get("thought", ""))[:300], "action": act,
            "element": d.get("element"), "coords": d.get("coords"),
            "text": d.get("text"), "keys": d.get("keys"),
            "amount": d.get("amount"), "expect": d.get("expect")}


def to_actuator_action(a: dict, elements: list, geom: dict | None = None):
    """把模型动作映射到 V9 actuator 的 Action（含回读验证意图 + 坐标换算）。

    纯视觉模式下模型给的是**图像像素**：先按 geom（DPI 缩放 + 多屏偏移）换成逻辑坐标，
    否则高 DPI 屏上会点偏 —— 这是"纯视觉能不能真操作"的关键一步。
    """
    from core.actuator import Action
    from core.gui_perception import to_screen
    primitive = a["action"]
    keys = a.get("keys") or []
    text = a.get("text")
    amount = a.get("amount")

    def _pt(coords, element_idx):
        el = next((e for e in (elements or []) if e.get("idx") == element_idx), None)
        raw = (el or {}).get("center") or (list(coords) if coords else None)
        # 元素自带的 center 是屏幕逻辑坐标（UIA 口径）；模型直给的是图像像素 → 需换算
        if el and el.get("center"):
            return list(el["center"])
        return to_screen(raw[0], raw[1], geom) if raw else None

    target: dict = {}
    p = _pt(a.get("coords"), a.get("element"))
    if p:
        target["coords"] = p
    args: dict = {}
    if primitive == "type":
        args["text"] = "" if text is None else str(text)
    if primitive == "hotkey":
        args["keys"] = keys
    if primitive == "scroll":
        args.update(amount=int(amount or 3), x=p[0] if p else None, y=p[1] if p else None)
    if primitive == "move":
        args.update(x=p[0] if p else None, y=p[1] if p else None)
    if primitive == "drag":
        to_pt = _pt(a.get("to_coords"), a.get("to_element"))
        if not to_pt:
            return None                                  # 拖动没给终点 → 明确失败，不瞎拖
        args.update(to=to_pt, dur=float(a.get("dur") or 0.5))
    if primitive in ("launch", "url"):
        cmd = str(text or "")
        if primitive == "url" and not cmd.startswith(("http://", "https://")):
            cmd = "https://" + cmd
        args["cmd"] = cmd
    if primitive == "wait":
        args["ms"] = int(amount or 800)
    verify = {"kind": "text", "probe": a.get("expect")} if a.get("expect") else {}
    return Action(primitive=("launch" if primitive in ("launch", "url") else primitive),
                  target=target, args=args, verify=verify,
                  risk=("write" if primitive in ("type", "hotkey", "launch", "drag")
                        else "safe"))


# ═══════════ 闭环 ═══════════
def _element_key(e: dict) -> tuple:
    return (e.get("name", "")[:60], e.get("role", ""), tuple(e.get("rect") or ()))


def _changed(prev: list, cur: list) -> bool:
    if len(prev) != len(cur):
        return True
    return {_element_key(e) for e in prev} != {_element_key(e) for e in cur}


def _expect_hit(expect, cur: list) -> bool:
    if not expect:
        return True
    needle = str(expect).strip().lower()
    if not needle:
        return True
    for e in cur:
        blob = f"{e.get('name','')} {e.get('value','')}".lower()
        if needle in blob:
            return True
    return False


class GuiAgent:
    """think → act → verify 闭环。默认只规划（dry_run），碰鼠标键盘要显式放行。"""

    def __init__(self, ledger=None, brain=None, actuator=None, *, planner=None,
                 perceive_fn=None, dry_run=False, allow_actions=False, max_steps=None,
                 vision_only=None):
        self.led, self.brain, self.actuator = ledger, brain, actuator
        self.vision_only = (os.environ.get("GUI_VISION_ONLY", "0") == "1"
                            if vision_only is None else bool(vision_only))
        if perceive_fn is not None:
            self.perceive_fn = perceive_fn
        else:
            def _perc():
                return perceive(vision_only=self.vision_only)
            self.perceive_fn = _perc
        self.dry_run = dry_run and not allow_actions
        self.max_steps = int(max_steps or DEFAULT_MAX_STEPS)
        self.planner, self.planner_kind = pick_planner(brain, planner=planner)
        self.task_id = uuid.uuid4().hex[:12]
        if ledger is not None:
            self._init_table()

    def _init_table(self):
        try:
            with txn(self.led) as cur:
                cur.execute("CREATE TABLE IF NOT EXISTS gui_task_trace ("
                            "task_id TEXT, step INTEGER, task TEXT, planner TEXT,"
                            " thought TEXT, action TEXT, payload_json TEXT, ok INTEGER,"
                            " verify_note TEXT, screen_changed INTEGER, dry_run INTEGER,"
                            " expect_hit INTEGER, at TEXT, PRIMARY KEY (task_id, step))")
        except Exception as e:
            _swallow(__file__, e)

    def _trace(self, step, task, action, *, ok, verify_note, screen_changed, expect_hit):
        if self.led is None:
            return
        try:
            with txn(self.led) as cur:
                cur.execute("INSERT INTO gui_task_trace (task_id, step, task, planner,"
                            " thought, action, payload_json, ok, verify_note,"
                            " screen_changed, dry_run, expect_hit, at)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (self.task_id, step, task[:400], self.planner_kind,
                             (action or {}).get("thought", "")[:300],
                             (action or {}).get("action", ""),
                             json.dumps(action or {}, ensure_ascii=False,
                                        default=str)[:2000],
                             1 if ok else 0, (verify_note or "")[:300],
                             1 if screen_changed else 0, 1 if self.dry_run else 0,
                             1 if expect_hit else 0,
                             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        except Exception as e:
            _swallow(__file__, e)

    def run(self, task: str, *, expect: str | None = None,
            max_steps: int | None = None, grant=None) -> dict:
        """grant = 主人签发的授权令牌（core/gui_grant）。

        有授权 → 高风险动作照做（授权是唯一闸门，关键词黑名单只用来标注风险）；
        无授权 → 高风险动作在执行前停下，返回 needs_confirm 并登记（授权到位自动续跑）。
        """
        # 视觉钉死：动手前必须有新鲜取景（主人令：禁传统瞎子操作）
        from core.senses_gate import require_eye as _re
        _eye = _re()
        if not _eye.get("ok"):
            return {"ok": False, "拒动": True, "在哪一步": "①眼", "读数": _eye}

        limit = int(max_steps or self.max_steps)
        granted, grant_why, grant_data = Grant.verify_action(grant, primitive="*") \
            if grant is not None else (False, "no_grant", {})
        # 授权只决定"要不要在执行前停下"，不改变"每次动作都要回读验证"这条纪律
        self._grant, self._grant_data = grant, grant_data
        history: list = []
        obs = self.perceive_fn()
        prev_elements = obs.get("elements", [])
        steps_log: list = []
        no_progress = 0
        reason = "max_steps"

        for step in range(1, limit + 1):
            action = self.planner.plan(task, obs, history)
            act = action.get("action")
            if act == "ask":
                self._trace(step, task, action, ok=False,
                            verify_note="planner_ask:" + str(action.get("text"))[:120],
                            screen_changed=False, expect_hit=False)
                return self._result(task, "need_info", steps_log, action, None, obs)
            if act == "done":
                hit = _expect_hit(expect or action.get("expect"), prev_elements)
                self._trace(step, task, action, ok=hit, verify_note="done",
                            screen_changed=False, expect_hit=hit)
                return self._result(task, "done" if hit else "expect_unmet",
                                    steps_log, action, None, obs)

            # ── act ──
            ok, exec_note = True, "dry_run"
            if self.dry_run:
                exec_note = "dry_run（只规划，未碰鼠标键盘）"
            elif self.actuator is None:
                ok, exec_note = False, "no_actuator"
            else:
                act = to_actuator_action(action, prev_elements, geom=obs.get("geometry"))
                a_ok, a_why, a_data = Grant.verify_action(
                    grant, primitive=act.primitive, step=step)
                try:
                    # ★授权是唯一闸门：有令牌就把本轮确认门交给授权（每步仍落审计）
                    restore = None
                    if a_ok and getattr(self.actuator, "auto_confirm", False) is False:
                        restore = self.actuator.auto_confirm
                        self.actuator.auto_confirm = True
                    res = self.actuator.run_action(act)
                    if restore is not None:
                        self.actuator.auto_confirm = restore
                    ok = bool(res.get("ok"))
                    if res.get("needs_confirm") and not a_ok:
                        # 无授权/超范围 → 停下来等主人，而不是硬闯
                        self._trace(step, task, action, ok=False,
                                    verify_note=f"needs_grant:{a_why}",
                                    screen_changed=False, expect_hit=False)
                        return self._result(task, "needs_confirm", steps_log, action,
                                            {"reason": a_why}, obs)
                    exec_note = ("ok" if ok else
                                 ("needs_confirm" if res.get("needs_confirm")
                                  else f"exec_failed:{res.get('reason') or res.get('error')}"))
                    if res.get("needs_confirm"):
                        self._trace(step, task, action, ok=False,
                                    verify_note="needs_confirm", screen_changed=False,
                                    expect_hit=False)
                        return self._result(task, "needs_confirm", steps_log, action,
                                            res, obs)
                except Exception as exc:                      # noqa: BLE001
                    ok, exec_note = False, f"exec_exception:{type(exc).__name__}"

            # ── verify（回读屏幕，比"执行没报错"更硬）──
            obs2 = self.perceive_fn()
            cur_elements = obs2.get("elements", [])
            changed = _changed(prev_elements, cur_elements)
            hit = _expect_hit(action.get("expect") or expect, cur_elements)
            verify_ok = bool(ok and (changed or hit))
            note = (f"{exec_note} · 屏幕{'有' if changed else '无'}变化 · "
                    f"期望{'命中' if hit else '未命中'}")
            self._trace(step, task, action, ok=verify_ok, verify_note=note,
                        screen_changed=changed, expect_hit=hit)
            steps_log.append({"step": step, "action": act,
                              "thought": action.get("thought"),
                              "ok": verify_ok, "note": note,
                              "payload": {k: v for k, v in action.items()
                                          if k != "thought"}})
            history.append({"step": step, "action": act, "note": note})

            if not changed:
                no_progress += 1
                if no_progress >= NO_PROGRESS_LIMIT:
                    return self._result(task, "no_progress", steps_log, action, None, obs2)
            else:
                no_progress = 0
            if expect and hit:
                return self._result(task, "expect_satisfied", steps_log, action, None, obs2)
            prev_elements, obs = cur_elements, obs2

        return self._result(task, reason, steps_log, None, None, obs)

    def _result(self, task, reason, steps, last_action, extra, obs) -> dict:
        return {"task_id": self.task_id, "task": task, "reason": reason,
                "ok": reason in ("done", "expect_satisfied"),
                "dry_run": self.dry_run, "planner": self.planner_kind,
                "steps": steps, "last_action": last_action, "extra": extra,
                "elements": len(obs.get("elements", [])),
                "notes": obs.get("notes", []),
                "local_only": local_only()}


__all__ = ["GuiAgent", "LocalOllamaPlanner", "CloudTextPlanner", "ScriptedPlanner",
           "PlannerUnavailable", "pick_planner", "parse_action", "to_actuator_action",
           "PRIMITIVES", "ACTION_SCHEMA_HINT"]
