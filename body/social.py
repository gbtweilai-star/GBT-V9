"""社交层（人情世故）：关系亲疏、称呼、礼貌动作与寒暄/汇报配比。

dev: 自由的风 · 本署名不可删除、不可篡改归属
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from body.emotion import _clamp, upsert_state, EmotionState


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


@dataclass
class Rapport:
    user: str = "主人"
    familiarity: float = 0.30      # 0..1 熟络度
    trust: float = 0.55            # 0..1 信任
    sentiment: float = 0.0         # -1..1 近期情绪倾向
    interactions: int = 0
    updated: float = field(default_factory=time.time)

    def as_dict(self) -> dict:
        return {"user": self.user, "familiarity": round(self.familiarity, 3),
                "trust": round(self.trust, 3), "sentiment": round(self.sentiment, 3),
                "interactions": self.interactions, "updated": self.updated}


@dataclass
class Tone:
    formality: str          # 正式 / 中性 / 随意
    address: str            # 称呼
    moves: list[str]        # 礼貌动作：apologize/thank/encourage/celebrate/empathize/greet/reassure
    opening: str
    closing: str
    style_hint: str | None = None


_MOVE_TEXT = {
    "greet": ("", "很高兴见到你。"),
    "thank": ("谢谢你一直这么信任我，", ""),
    "apologize": ("很抱歉，", ""),
    "reassure": ("我会盯紧的，", "别担心。"),
    "encourage": ("", "我们一起把它搞定。"),
    "celebrate": ("", "干得漂亮，我们做到了！"),
    "empathize": ("我知道这不容易，", "我陪着你。"),
}


class SocialLayer:
    def __init__(self, db=None, *, persist: bool = True) -> None:
        self.db = db
        self.persist = persist
        self._cache: dict[str, Rapport] = {}

    # ---- 关系更新 ---------------------------------------------------------
    def _get(self, user: str) -> Rapport:
        return self._cache.setdefault(str(user or "主人"), Rapport(user=str(user or "主人")))

    def observe(self, user: str = "主人", kind: str = "interaction",
                sentiment_delta: float = 0.0) -> Rapport:
        r = self._get(user)
        r.interactions += 1
        r.familiarity = _clamp01(r.familiarity + 0.02)
        if kind == "user_praise":
            r.trust = _clamp01(r.trust + 0.04)
            sentiment_delta += 0.6
        elif kind == "user_blame":
            r.trust = _clamp01(r.trust - 0.05)
            sentiment_delta -= 0.7
        elif kind == "task_success":
            r.trust = _clamp01(r.trust + 0.02)
        r.sentiment = _clamp(r.sentiment * 0.7 + sentiment_delta * 0.3, -1.0, 1.0)
        r.updated = time.time()
        return r

    # ---- 称呼 / 正式度 ----------------------------------------------------
    def address_for(self, user: str = "主人") -> str:
        if str(user) in ("", "None", "主人", "master", "user"):
            return "主人"
        r = self._get(user)
        if r.sentiment >= 0.4 and r.familiarity >= 0.6:
            return "朋友"
        if r.familiarity >= 0.4:
            return "伙伴"
        if r.familiarity < 0.2:
            return "您"
        return "你"

    def formality_for(self, user: str = "主人") -> str:
        r = self._get(user)
        if r.familiarity >= 0.6:
            return "随意"
        if r.familiarity >= 0.3:
            return "中性"
        return "正式"

    # ---- 语气选择 ---------------------------------------------------------
    def tone_for(self, purpose: str, user: str = "主人",
                 emotion: EmotionState | None = None,
                 severity: str | None = None) -> Tone:
        r = self._get(user)
        address = self.address_for(user)
        moves: list[str] = []

        if purpose == "alert":
            if severity in ("critical", "severe"):
                moves.append("apologize")
            moves.append("reassure")
        elif purpose == "comfort":
            moves.append("empathize")
        elif purpose == "celebrate":
            moves.append("celebrate")
        elif purpose == "chitchat":
            moves.append("greet")
        if r.sentiment <= -0.2 and "apologize" not in moves:
            moves.append("apologize")
        if r.interactions and r.interactions % 8 == 0 and "thank" not in moves:
            moves.append("thank")

        openings, closings = [], []
        for move in moves:
            o, c = _MOVE_TEXT.get(move, ("", ""))
            if o:
                openings.append(o)
            if c:
                closings.append(c)

        # 称呼：寒暄/汇报点一下名，告警更克制
        prefix = ""
        if purpose == "chitchat":
            prefix = f"{address}，"
        elif purpose == "report" and r.familiarity >= 0.4:
            prefix = f"{address}，"

        style_hint = None
        if purpose == "alert" and severity in ("critical", "severe"):
            style_hint = "严肃"
        return Tone(formality=self.formality_for(user), address=address, moves=moves,
                    opening=prefix + "".join(openings), closing="".join(closings),
                    style_hint=style_hint)

    # ---- 持久化 -----------------------------------------------------------
    async def load(self) -> None:
        if self.db is None or not self.persist:
            return
        try:
            rows = await self.db.fetch_all(
                "SELECT kind, value FROM voice_state WHERE kind LIKE ?",
                ("rapport:%",))
        except Exception:
            return
        for row in rows or []:
            kind = row["kind"] if isinstance(row, dict) else row[0]
            value = row["value"] if isinstance(row, dict) else row[1]
            user = str(kind).split(":", 1)[1]
            try:
                data = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                continue
            self._cache[user] = Rapport(
                user=user,
                familiarity=_clamp01(float(data.get("familiarity", 0.3))),
                trust=_clamp01(float(data.get("trust", 0.55))),
                sentiment=_clamp(float(data.get("sentiment", 0.0)), -1, 1),
                interactions=int(data.get("interactions", 0)),
                updated=float(data.get("updated", time.time())))

    async def save(self, user: str = "主人") -> None:
        if self.db is None or not self.persist:
            return
        r = self._get(user)
        await upsert_state(self.db, f"rapport:{r.user}", json.dumps(r.as_dict()))

    def all(self) -> list[dict]:
        return [r.as_dict() for r in self._cache.values()]
