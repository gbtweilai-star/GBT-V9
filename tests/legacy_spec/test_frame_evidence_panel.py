# tests/test_frame_evidence_panel.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
async def test_raw_unavailable_never_healthy(client, seeded_evicted):
    r = await client.get("/api/projects/p1/frame-evidence?raw_available=0")
    row = r.json()["items"][0]
    assert r.json()["ui_state"][row["evidence_id"]] != "healthy"     # 回收的帧不许绿
    fr = await client.get(f"/api/projects/p1/frame-evidence/{row['evidence_id']}/frame")
    assert fr.status_code == 410                                     # 帧已回收

async def test_deleting_is_409(client, seeded_deleting):
    ev = (await client.get("/api/projects/p1/frame-evidence")).json()["items"][0]
    fr = await client.get(f"/api/projects/p1/frame-evidence/{ev['evidence_id']}/frame")
    assert fr.status_code == 409

async def test_same_timestamp_paging_no_dup(client, seeded_same_epoch):
    seen, cur = set(), None
    for _ in range(5):
        r = (await client.get(f"/api/projects/p1/frame-evidence?limit=2&cursor={cur or ''}")).json()
        for it in r["items"]:
            assert it["evidence_id"] not in seen; seen.add(it["evidence_id"])
        cur = r.get("next_cursor")
        if not cur: break

async def test_tampered_cursor_rejected(client):
    r = await client.get("/api/projects/p1/frame-evidence?cursor=deadbeef")
    assert r.status_code == 400

async def test_cross_backend_identical(sqlite_client, pg_client, seeded):
    a = (await sqlite_client.get("/api/projects/p1/frame-evidence")).json()
    b = (await pg_client.get("/api/projects/p1/frame-evidence")).json()
    assert a["items"] == b["items"] and a["next_cursor"] == b["next_cursor"]
