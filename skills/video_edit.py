# skills/video_edit.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def run_edit_op(ctx, project_id, trace_id, index, op):
    devour, token = ctx.devour, uuid.uuid4().hex
    policy = ctx.policy.for_op(op["op"])                      # 版本化策略值
    media  = await ctx.timeline.media_at(project_id, op)      # 源媒体信息
    roi    = await ctx.exec.preview_roi(project_id)

    before_rev = await ctx.timeline.revision(project_id)
    fp = encode_fingerprint(ctx, media=media, roi=roi, algorithm_version=ALGORITHM_VERSION)

    devour.cache.invalidate(project_id)                       # 编辑前失效
    try:
        # ① 先取校准(按 项目+op+算法+fingerprint); 无 → 内联标定
        calib = await load_calibration(devour.db, project_id, op["op"], fp,
                                       await devour.db.db_now_epoch())
        if calib is None:
            calib = await calibrate_inline(devour, project_id, token, fp, policy)
        thresholds = (calib and {"threshold": calib.noise_threshold,
                                 "effect_floor": calib.effect_floor, "outside_max": calib.outside_max,
                                 "local_ratio": calib.local_ratio, "policy_version": calib.policy_version,
                                 "encode_fingerprint": calib.encode_fingerprint, "algorithm": calib.algorithm_version})

        # ② 基线(不缓存) + 执行编辑 + bump 版本 + 结果(不缓存)
        times = devour.verification_times(op)
        baseline = [await devour.snapshot_at(project_id, t["before_s"], timeline_rev=before_rev,
                    action_session_token=token, evidence_pair=True) for t in times]
        action = await ctx.touch.act("editor.timeline", op,
                                     idempotency_key=f"{trace_id}:{index}:{token}")
        if action.get("state") != "verified":
            return {"state": "blocked", "reason": action.get("error_code"), "evidence_written": False}

        after_rev = await ctx.timeline.bump_revision(project_id, op)
        after = [await devour.snapshot_at(project_id, t["after_s"], timeline_rev=after_rev,
                 action_session_token=token, evidence_pair=True) for t in times]

        # ③.校验 fingerprint 前后一致(变了就中止, 判 unknown)
        fp_after = encode_fingerprint(ctx, media=media, roi=await ctx.exec.preview_roi(project_id),
                                      algorithm_version=ALGORITHM_VERSION)
        if fp_after != fp:
            return {"state": "blocked", "reason": "encode_fingerprint_changed", "evidence_written": False}

        # ④ 持锁验证 + 同事务落证据
        refs = sorted({x["ref"] for x in baseline + after})
        async with ctx.leases.hold(refs, holder=trace_id, purpose="video.verify") as pinned:
            nt = thresholds["threshold"] if thresholds else None
            metrics = await devour.delta_metrics_for_pair(baseline, after, times,
                                                          read=pinned.read, noise_threshold=nt or 0.0)
            if thresholds is None:
                verdict, spec = "unknown", {"calibration_required": True, "algorithm": ALGORITHM_VERSION}
            else:
                verdict, spec = ctx.verify.verdict_and_spec(op, metrics, spec_base=thresholds,
                                                            ui_ok=True)
            summary = {"op": op["op"], "verdict": verdict, "metrics": metrics,
                       "compare_spec": spec,
                       "baseline_refs": [x["ref"] for x in baseline],
                       "result_refs":   [x["ref"] for x in after]}
            row = ctx.verify.evidence_row(trace_id, op, verdict, baseline, after, summary)
            # ★ 同事务: 证据行 + 校准键标量列(供面板反查)
            await ctx.db.execute("""INSERT INTO frame_verification_evidence
                (evidence_id, trace_id, op, verdict, baseline_ref, result_ref, digest,
                 summary_json, raw_available, created_epoch, retain_until_epoch,
                 algorithm_version, policy_version, encode_fingerprint)
                VALUES (?,?,?,?,?,?,?,?,1,?,?,?,?,?)""",
                (*row, spec.get("algorithm"), spec.get("policy_version"), fp))
        return {"state": verdict, "evidence_written": True}

    except Exception as exc:
        return {"state": "blocked", "reason": type(exc).__name__, "evidence_written": False}
    finally:
        devour.cache.invalidate(project_id)                   # 成功/失败/状态不明 都失效
