# tests/test_frame_compare.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_both_ready_loads_frames_and_diff(client, ev_both_ready):
    eid = ev_both_ready["evidence_id"]
    for side in ("baseline", "result"):
        r = await client.get(f"/api/frame-evidence/{eid}/frames/{side}")
        assert r.status_code == 200 and r.headers["ETag"].startswith('"')
    assert (await client.get(f"/api/frame-evidence/{eid}/diff")).status_code == 200

async def test_one_evicted_is_410_and_not_green(client, ev_baseline_evicted):
    eid = ev_baseline_evicted["evidence_id"]
    r = await client.get(f"/api/frame-evidence/{eid}/frames/baseline")
    assert r.status_code == 410                                   # 已回收
    ui = (await client.get(f"/api/projects/p1/frame-evidence/{eid}")).json()["compare"]
    assert ui["state"] != "healthy" and ui["mode_enabled"] is False  # 绝不绿

async def test_recycling_is_409(client, ev_result_deleting):
    r = await client.get(f"/api/frame-evidence/{ev_result_deleting['evidence_id']}/frames/result")
    assert r.status_code == 409

async def test_diff_requires_stored_threshold(client, ev_no_spec):
    r = await client.get(f"/api/frame-evidence/{ev_no_spec['evidence_id']}/diff")
    assert r.status_code == 422                                   # 不许自造阈值

async def test_diff_leases_both_refs(client, ev_both_ready, leases_spy):
    await client.get(f"/api/frame-evidence/{ev_both_ready['evidence_id']}/diff")
    assert set(leases_spy.last_held) == {ev_both_ready["baseline_ref"], ev_both_ready["result_ref"]}
