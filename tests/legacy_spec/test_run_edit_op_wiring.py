# tests/test_run_edit_op_wiring.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_first_run_calibrates_then_reuses(ctx):
    r1 = await run_edit_op(ctx, "p", "t", 0, {"op": "color", "start_s": 1, "end_s": 3})
    assert ctx.db.count("devour_calibrations") >= 1
    fp = ctx.last_fingerprint
    r2 = await run_edit_op(ctx, "p", "t", 1, {"op": "color", "start_s": 1, "end_s": 3})
    assert ctx.db.count("devour_calibrations") == 1                 # 第二次复用, 不重标

def test_fingerprint_invariant_to_color_but_changes_on_resolution(ctx):
    a = encode_fingerprint(ctx, media=m(w=1920,h=1080,grade="none"), roi=r(640,360), algorithm_version="v1")
    b = encode_fingerprint(ctx, media=m(w=1920,h=1080,grade="warm"), roi=r(640,360), algorithm_version="v1")
    c = encode_fingerprint(ctx, media=m(w=1280,h=720,grade="none"), roi=r(640,360), algorithm_version="v1")
    assert a == b and a != c                                        # 调色不变 / 分辨率变

async def test_missing_calibration_yields_unknown_not_verified(ctx):
    ctx.devour.stub_static = None                                   # 标定失败
    r = await run_edit_op(ctx, "p", "t", 0, {"op": "effects", "start_s": 1, "end_s": 3})
    assert r["state"] == "unknown"                                  # 不是 verified

async def test_compare_spec_records_exact_thresholds(ctx):
    await run_edit_op(ctx, "p", "t", 0, {"op": "color", "start_s": 1, "end_s": 3})
    row = ctx.db.last_evidence()
    spec = json.loads(row["summary_json"])["compare_spec"]
    assert spec["threshold"] == ctx.db.last_calibration()["noise_threshold"]   # 用的就是当时那个
    assert row["encode_fingerprint"] == ctx.last_fingerprint
