# panel/routes/frame_compare.py
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 铁律: 每次取帧都先 hold lease; 只 ready 才可流; 绝不回传存储 key;
#       diff 必须【同时】hold 两帧的 lease, 阈值只从 summary.compare_spec 取,
#       缺失就 422, 不许浏览器自己编一个阈值。
@router.get("/api/frame-evidence/{eid}/frames/{side}")
async def frame(eid: str, side: str, user=Depends(auth), db=Depends(get_db)):
    if side not in {"baseline", "result"}:
        raise HTTPException(400, "invalid_frame_side")
    ev = await owned_evidence(db, eid, user)
    ref = ev[f"{side}_ref"]
    try:
        async with leases.hold(ref, holder=user.id, purpose="evidence-preview"):
            data = await artifacts.get_bytes(ref)
    except ArtifactDeleting:               raise HTTPException(409, "frame_recycling")
    except (ArtifactMissing, EvidenceUnavailable):
        raise HTTPException(410, "raw_frame_evicted")
    return Response(data, media_type="image/png",
                    headers={"ETag": f'"{ref}"',                # 按 ref 缓存: 内容寻址天然可选
                             "Cache-Control": "private, no-cache"})


@router.get("/api/frame-evidence/{eid}/diff")
async def diff(eid: str, user=Depends(auth), db=Depends(get_db)):
    ev = await owned_evidence(db, eid, user)
    spec = (ev["summary"] or {}).get("compare_spec")
    if not spec or "threshold" not in spec:
        raise HTTPException(422, "diff_threshold_unavailable")   # 宁可不给, 也别自造阈值
    try:
        async with leases.hold_many([ev["baseline_ref"], ev["result_ref"]],
                                    holder=user.id, purpose="evidence-diff"):
            before, after = await artifacts.get_pair_bytes(ev["baseline_ref"], ev["result_ref"])
            png = heatmap_png(before, after, spec=spec)          # 固定算法+固定 colormap
    except ArtifactDeleting:               raise HTTPException(409, "frame_recycling")
    except (ArtifactMissing, EvidenceUnavailable):
        raise HTTPException(410, "raw_frame_evicted")
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-cache"})
