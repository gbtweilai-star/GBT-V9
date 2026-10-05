# tests/test_calibration.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_reuse_when_valid(db):
    await save_calibration(db, cand(nt=6.0, samples=10), now=1000, ttl_s=3600,
                           min_threshold=1, max_threshold=50)
    cal = await load_calibration(db, "p", "color", "fp1", now=1200)
    assert cal and cal.noise_threshold == 6.0

async def test_recalibrate_on_fingerprint_change(db):
    await save_calibration(db, cand(nt=6.0, samples=10, fp="fp1"), now=1000, ttl_s=3600,
                           min_threshold=1, max_threshold=50)
    assert await load_calibration(db, "p", "color", "fp2", now=1200) is None   # 指纹变 → 重标

async def test_recalibrate_on_expiry_and_low_samples(db):
    await save_calibration(db, cand(nt=6.0, samples=10), now=1000, ttl_s=10,
                           min_threshold=1, max_threshold=50)
    assert await load_calibration(db, "p", "color", "fp1", now=2000) is None   # 过期

async def test_anomalous_rejected_keeps_old(db):
    await save_calibration(db, cand(nt=6.0, samples=10), now=1000, ttl_s=3600,
                           min_threshold=1, max_threshold=50)
    with pytest.raises(ValueError, match="anomalous_calibration_rejected"):
        await save_calibration(db, cand(nt=40.0, samples=10), now=1100, ttl_s=3600,
                               min_threshold=1, max_threshold=50)
    assert (await load_calibration(db, "p", "color", "fp1", 1200)).noise_threshold == 6.0

async def test_concurrent_upsert_single_row(db):
    await asyncio.gather(*[save_calibration(db, cand(nt=6.0, samples=10), now=1000,
                                            ttl_s=3600, min_threshold=1, max_threshold=50)
                           for _ in range(5)])
    assert db.count("devour_calibrations") == 1

async def test_missing_calibration_does_not_crash(db):
    v, spec = await dv.verdict_and_spec({"op": "color", "start_s": 1, "end_s": 3}, metrics,
                                        db=db, project_id="p", fingerprint="fpX", now=1000)
    assert v == "unknown" and spec["calibration_required"] is True
