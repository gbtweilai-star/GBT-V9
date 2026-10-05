# body/fingerprint.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: fingerprint 标识【采集上下文】, 不标识画面内容。
#       必须排除: 调色/特效、播放头位置、选中态、编辑会话 —— 否则它随被验证对象一起变, 校准永远失效。
import hashlib, json

def encode_fingerprint(ctx, *, media, roi, algorithm_version) -> str:
    canon = {
        "media_identity": media["identity"],       # 源媒体指纹(不含时间线编辑)
        "media_format":   media["format"],         # 容器/编码
        "src_resolution": [media["w"], media["h"]],
        "roi_dimensions": [roi["w"], roi["h"]],    # 预览裁剪尺寸
        "capture_scale":  roi.get("scale"),        # 缩放系数
        "color_conv":     "rgb8-lab-float",        # cv2 转换路径
        "decoder_version": ctx.exec.decoder_version,
        "encoder_version": ctx.exec.encoder_version,
        "algorithm_version": algorithm_version,
        # ⛔ 明确排除: grade/filter/effect/transition, playhead_s, selection, action_session_token
    }
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]

async def calibrate_inline(devour, project_id, token, fingerprint, policy) -> dict | None:
    base = await devour.snapshot_at(project_id, -1.0, timeline_rev=None,          # 用当前暂停帧
                                    action_session_token=token, evidence_pair=True)
    samples = [base] + [await devour.snapshot_at(project_id, -1.0, timeline_rev=None,
                          action_session_token=token, evidence_pair=True) for _ in range(5)]
    # 必须同源帧; 否则放弃标定
    if any(s["source_frame_id"] != base["source_frame_id"] for s in samples):
        return None
    p50s, p99s = [], []
    for a, b in zip(samples, samples[1:]):
        de = devour.delta_e_map(a["ref"], b["ref"], noise_threshold=0.0)
        p50s.append(float(np.percentile(de, 50))); p99s.append(float(np.percentile(de, 99)))
    if len(p99s) < 5:
        return None
    delta_e_p50 = float(np.median(p50s))
    delta_e_p99 = float(np.percentile(p99s, 95))
    nt = min(12.0, max(2.0, 1.5 * delta_e_p99))            # clamp(1.5×p99, 2.0, 12.0)
    cand = CalibrationCandidate(project_id=project_id, op_kind=policy["op_kind"],
        algorithm_version=ALGORITHM_VERSION, encode_fingerprint=fingerprint,
        noise_threshold=nt, effect_floor=policy["effect_floor"], outside_max=policy["outside_max"],
        local_ratio=policy["local_ratio"], sample_count=len(p99s),
        delta_e_p50=delta_e_p50, delta_e_p99=delta_e_p99, noise_fraction_p99=0.0,
        policy_version=policy["version"])
    await save_calibration(devour.db, cand, now=await devour.db.db_now_epoch(),
                           ttl_s=86400 * 7, min_threshold=1.0, max_threshold=50.0)
    return cand
