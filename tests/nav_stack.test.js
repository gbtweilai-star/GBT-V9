// tests/nav_stack.test.js
test("cursor preserved across back", () => {
  push({ key: "list", type: "list", state: { filters: { verdict: "failed" }, cursor: "C1", scrollTop: 320 } });
  push({ key: "ev:e1", type: "evidence", state: { evidenceId: "e1", scrollTop: 0 } });
  push({ key: "cmp:e1", type: "compare", state: { evidenceId: "e1", mode: "overlay-heatmap", opacity: .65 } });
  back();
  expect(nav.stack.at(-1).state.cursor).toBe("C1");        // 列表游标没被重置
});

test("compare state restored", () => {
  const st = { evidenceId: "e1", mode: "standalone-heatmap", opacity: .4, transform: "scale(2)", wipe: 30 };
  push({ key: "cmp:e1", type: "compare", state: st });
  back(); push({ key: "cmp:e1", type: "compare", state: { evidenceId: "e1" } });
  // restoreState 后 transform/opacity/wipe 应回到 st
});

test("back during fetch aborts and ignores", async () => {
  const p = loadAsset("k", "/slow", nav.generation);
  back();
  expect(await p).toBeNull();                              // 迟到响应被丢弃
});

test("evicted-while-open does not keep stale", () => {
  onAvailabilityRefresh(view, { baseline: { available: false } });
  expect(view.heat.getAttribute("href")).toBe("");         // 不显示旧图
  expect(statusText.dataset.state).toBe("unknown");        // 琥珀, 不绿
});

test("duplicate push is no-op", () => {
  push({ key: "cmp:e1", type: "compare", state: {} });
  const n = nav.stack.length; push({ key: "cmp:e1", type: "compare", state: {} });
  expect(nav.stack.length).toBe(n);
});
