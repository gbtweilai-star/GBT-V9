// tests/compare_overlay.test.js
test("opacity changes alpha not size", () => {
  setHeatOpacity(layers, 0.3);
  expect(layers.heat.getAttribute("opacity")).toBe("0.3");
  expect(layers.heat.getAttribute("width")).toBe(String(layers.width));   // 尺寸不变
});

test("422 falls back to plain frame, never green", async () => {
  const ok = await enableHeatmap(layers, { endpoint: mockFetch(422) });
  expect(ok).toBe(false);
  expect(layers.heat.getAttribute("opacity")).toBe("0");
  expect(statusText.dataset.state).toBe("unknown");                       // 琥珀
});

test("wipe clips frame and heatmap together", () => {
  // wipe 作用于 result 组, 断言 heat 与 frame 同在该组的 clipPath 下
  expect(layers.heat.parentNode).toBe(layers.result);
});

test("zoom keeps alignment", () => {
  world.setAttribute("transform", "scale(2.5) translate(-30 -20)");
  expect(layers.frame.parentNode).toBe(layers.result);                    // 同一 transform
  expect(layers.heat.parentNode).toBe(layers.result);
});
