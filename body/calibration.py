# body/calibration.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 只有 项目+op+算法版本+fingerprint 全匹配且未过期、样本足够 才复用;
#       读不到/过期 → 返回 unknown(calibration_required), 绝不悄悄编一个默认值;
#       历史证据的 compare_spec 不可变, 重标定只影响未来。
from dataclasses import dataclass

MIN_SAMPLES = 5
ALGORITHM_VERSION = "lab-delta-e76-v1"

@dataclass(frozen=True)
class Calibration:
    noise_threshold: float; effect_floor: float; outside_max: float; local_ratio: float
    sample_count: int; delta_e_p50: float; delta_e_p99: float; noise_fraction_p99: float
    algorithm_version: str; encode_fingerprint: str; policy_version: str; expires_at: int
    @classmethod
    def from_row(cls, r): return cls(**{k: r[k] for k in cls.__annotations__})


async def load_calibration(db, project_id, op_kind, fingerprint, now):
    row = await db.fetch_one("""SELECT * FROM devour_calibrations
        WHERE project_id=? AND op_kind=? AND algorithm_version=?
          AND encode_fingerprint=? AND expires_at>? AND sample_count>=?""",
        (project_id, op_kind, ALGORITHM_VERSION, fingerprint, now, MIN_SAMPLES))
    return Calibration.from_row(row) if row else None      # None → 调用方判 unknown

async def save_calibration(db, cand, *, now, ttl_s, min_threshold, max_threshold):
    if cand.sample_count < MIN_SAMPLES:
        raise ValueError("insufficient_calibration_samples")
    if not min_threshold <= cand.noise_threshold <= max_threshold:
        raise ValueError("calibration_threshold_out_of_bounds")
    key = (cand.project_id, cand.op_kind, cand.algorithm_version, cand.encode_fingerprint)

    async with db.transaction(immediate=(db.dialect == "sqlite")):
        if db.dialect == "postgres":
            await db.lock_key("devour-calibration:" + ":".join(key))   # 事务级 advisory lock
        old = await db.fetch_one("""SELECT noise_threshold FROM devour_calibrations
            WHERE project_id=? AND op_kind=? AND algorithm_version=? AND encode_fingerprint=?""", key)
        # ★ 异常尖峰不许覆盖已有基线
        if old and cand.noise_threshold > max(old["noise_threshold"] * 3,
                                              old["noise_threshold"] + 5):
            raise ValueError("anomalous_calibration_rejected")
        await db.execute("""INSERT INTO devour_calibrations
            (project_id,op_kind,algorithm_version,encode_fingerprint,noise_threshold,
             effect_floor,outside_max,local_ratio,sample_count,delta_e_p50,delta_e_p99,
             noise_fraction_p99,policy_version,created_at,updated_at,expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(project_id,op_kind,algorithm_version,encode_fingerprint) DO UPDATE SET
              noise_threshold=excluded.noise_threshold, effect_floor=excluded.effect_floor,
              outside_max=excluded.outside_max, local_ratio=excluded.local_ratio,
              sample_count=excluded.sample_count, delta_e_p50=excluded.delta_e_p50,
              delta_e_p99=excluded.delta_e_p99, noise_fraction_p99=excluded.noise_fraction_p99,
              policy_version=excluded.policy_version, updated_at=excluded.updated_at,
              expires_at=excluded.expires_at""",
            cand.to_row(now=now, expires_at=now + ttl_s))

async def verdict_and_spec(self, op, m, *, db, project_id, fingerprint, now, ui_ok=None):
    cal = await load_calibration(db, project_id, op["op"], fingerprint, now)
    if cal is None:
        return "unknown", {"calibration_required": True, "algorithm": ALGORITHM_VERSION}
    spec = {"algorithm": cal.algorithm_version, "colormap": COLORMAP,
            "threshold": cal.noise_threshold, "max_delta_e": MAX_DELTA_E,
            "effect_floor": cal.effect_floor, "outside_max": cal.outside_max,
            "local_ratio": cal.local_ratio, "policy_version": cal.policy_version,
            "encode_fingerprint": cal.encode_fingerprint}
    # ...后续判定逻辑同前; 注意 spec 里的值会【复制进该证据行】的 compare_spec
    return self._decide(op, m, spec, ui_ok), spec
