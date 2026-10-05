// tests/calibration_evidence_loop.test.js
test("calibration filters reach evidence list", () => {
  evidenceForCalibration({ projectId:"p", op_kind:"color", algorithm_version:"lab-delta-e76-v1",
                           policy_version:"policy-v3", encode_fingerprint:"fp1" });
  const top = nav.stack.at(-1);
  expect(top.type).toBe("evidence_list");
  expect(top.state.filters).toMatchObject({ op_kind:"color", encode_fingerprint:"fp1" });
});

test("back restores calibration list cursor/scroll", () => {
  open({type:"calibration_list", state:{projectId:"p", cursor:"C1", scrollTop:240}});
  evidenceForCalibration({projectId:"p", op_kind:"color", algorithm_version:"a", policy_version:"v", encode_fingerprint:"fp"});
  back();
  expect(nav.stack.at(-1).state).toMatchObject({cursor:"C1", scrollTop:240});   // 未重置
});

test("breadcrumb order", () => {
  expect(breadcrumbTypes(nav.stack)).toEqual(["calibration_list","evidence_list","compare"]);
});

test("expired-while-open updates honestly", () => {
  const view = mountCalibration({status:"ok"});
  onCalibrationRefresh(view, { status:"expired" });
  expect(view.badge[1]).toBe("#ff5d73");        // 红
  expect(view.state.cursor).toBe("C1");         // 状态没丢
});

test("duplicate calibration open is no-op", () => {
  open({type:"calibration", state:{projectId:"p",op_kind:"color",algorithm_version:"a",encode_fingerprint:"f"}});
  const n = nav.stack.length;
  calibrationForEvidence({projectId:"p", calibration_key:{op_kind:"color",algorithm_version:"a",encode_fingerprint:"f"}});
  expect(nav.stack.length).toBe(n);             // 命中 → 回退, 不新增
});

test("deep link restore validates and refetches", () => {
  const h = encodeHash(nav.stack);
  expect(validateStack(parseHash(h))).toBe(true);
  expect(parseHash("#v1=@@bad")).toBeNull();    // 非法 → null → 回列表
});
