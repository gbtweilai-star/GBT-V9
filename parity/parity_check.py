# parity/parity_check.py —— 逐项对账（manifest 为准，DB 为证据）
def check(manifest, db, registry, commit_sha):
    errors, report = [], []
    expected = {(manifest["source_id"], c["id"]): c for c in manifest["capabilities"]}
    actual   = db.load_parity_rows(manifest["source_id"])

    # ① 清单 vs 数据库：缺行/多行都算错
    if set(actual) != set(expected):
        errors.append({"kind": "manifest_db_mismatch",
                       "missing": sorted(set(expected) - set(actual)),
                       "extra":   sorted(set(actual) - set(expected))})

    # ② 逐项跑真实 probe
    for key, cap in expected.items():
        item = {"capability_id": cap["id"], "result": "failed"}
        skill = registry.get(cap["native_skill"])

        if skill is None:
            item["reason"] = "native_skill_not_registered"
        elif skill.spec().get("name") != cap["native_skill"]:
            item["reason"] = "spec_name_mismatch"
        else:
            r = run_parity_probe(cap, skill)        # 必须回 observed + evidence
            if r["passed"] and r.get("evidence"):
                item.update(r, result="pass")
            else:
                item.update(r, reason=r.get("reason", "probe_failed"))

        db.append_run(run_id=new_run_id(), key=key, result=item["result"],
                      commit_sha=commit_sha, observed=item.get("observed", {}),
                      evidence=item.get("evidence", []), reason=item.get("reason"))
        report.append(item)

    # ③ 任何 non-pass 一律阻断，除非有走审批的显式例外
    if errors or any(x["result"] != "pass" for x in report):
        raise SystemExit(render_report(errors, report))
    print(render_report(errors, report))
