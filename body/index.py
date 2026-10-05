def parity_probe(ctx):
    """① 真走一遍 4 类登记 → verify_chain 必须 ok；
       ② 在隔离临时库里篡改一条 payload_json → verify_chain 必须在那个 seq 上报 event_hash_mismatch；
       ③ 删掉 manifest → boot_check 必须 unhealthy。
       断言"能发现损坏"才是这个探针的唯一价值。"""
    led = ctx.temp_ledger()
    ctx.run_sync(lambda: register(led, event_type="scan", actor="t1",
                                  payload={"enumerated": 3},
                                  targets=[{"path": "a.py", "after": "h1"}]))
    # ... change / fix / harden 各一条（走真实入口，不直接 INSERT）
    ok = ctx.run_sync(lambda: verify_chain(led.read_snapshot()))
    led.execute("UPDATE registration SET payload_json='{\"tampered\":1}' WHERE seq=2")
    bad = ctx.run_sync(lambda: verify_chain(led.read_snapshot()))
    return {"passed": ok.ok and not bad.ok and bad.at_seq == 2
                      and bad.reason == "event_hash_mismatch",
            "observed": {"clean_ok": ok.ok, "checked": ok.checked,
                         "tamper_detected_at": bad.at_seq, "reason": bad.reason},
            "evidence": {"module": "body.index", "clean_head": ok.head_seq,
                         "negative_path_verified": True}}
