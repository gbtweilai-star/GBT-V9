// panel/static/compare_overlay.js
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 铁律: 帧与热图同 <g> 同变换; 不透明滑杆只改 heat 的 opacity, 不改尺寸;
//       422/409/410 一律【不画假热图】, 只显示普通帧 + 琥珀提示, 绝不绿。
const NS = "http://www.w3.org/2000/svg";

function svgEl(name, attrs = {}) {
  const el = document.createElementNS(NS, name);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

function addImage(parent, href, w, h, label) {
  const img = svgEl("image", {
    x: 0, y: 0, width: w, height: h,           // 用帧的固有像素坐标, 避免半像素漂移
    preserveAspectRatio: "none",               // 同尺寸 → 逐像素对齐
    "image-rendering": "auto",                 // 视频帧用插值, 不用 pixelated
    href, role: "img", "aria-label": label,
  });
  parent.append(img);
  return img;
}

/* world = 既有的缩放/平移 <g>。热图与帧都挂它下面, 共享同一 transform。 */
function makeCompareLayers(world, { resultUrl, baselineUrl, width, height }) {
  const baseline = svgEl("g", { "data-layer": "baseline" });
  addImage(baseline, baselineUrl, width, height, "基线帧");

  const result = svgEl("g", { "data-layer": "result" });   // 结果帧 + 热图 = 一个整体
  const frame = addImage(result, resultUrl, width, height, "结果帧");
  const heat  = addImage(result, "", width, height, "差异热图");
  heat.setAttribute("pointer-events", "none");
  heat.setAttribute("opacity", "0");                        // 拉取成功后再打开

  world.append(baseline, result);
  return { baseline, result, frame, heat, width, height };
}

const heatUrlCache = new Map();
const MAX_HEAT_CACHE = 3;

async function getHeatUrl(key, endpoint) {
  if (heatUrlCache.has(key)) return heatUrlCache.get(key);
  const res = await fetch(endpoint, { credentials: "same-origin" });
  if (!res.ok) { const e = new Error(`heatmap_${res.status}`); e.status = res.status; throw e; }
  const url = URL.createObjectURL(await res.blob());
  heatUrlCache.set(key, url);
  while (heatUrlCache.size > MAX_HEAT_CACHE) {              // 只留最近几帧, 防大图堆积
    const [k0, u0] = heatUrlCache.entries().next().value;
    heatUrlCache.delete(k0); URL.revokeObjectURL(u0);
  }
  return url;
}

async function enableHeatmap(layers, { evidenceId, compareSpecRef, endpoint }) {
  try {
    // key 用不可变 ref + compare_spec 版本 —— 内容变了就换 key, 不命中旧图
    const url = await getHeatUrl(`${evidenceId}:${compareSpecRef}`, endpoint);
    layers.heat.setAttribute("href", url);
    layers.heat.setAttribute("opacity", "0.65");
    return true;
  } catch (err) {
    layers.heat.setAttribute("href", "");
    layers.heat.setAttribute("opacity", "0");               // 不清空不假画
    showHeatStatus(err.status);
    return false;
  }
}

function setHeatOpacity(layers, v) { layers.heat.setAttribute("opacity", String(Math.max(0, Math.min(1, v)))); }

function setViewMode(layers, mode, gap = 24) {
  layers.baseline.removeAttribute("transform");
  layers.result.removeAttribute("transform");
  if (mode === "overlay-heatmap") {
    layers.baseline.setAttribute("visibility", "hidden");
    layers.result.setAttribute("visibility", "visible");
    layers.frame.setAttribute("visibility", "visible");
    layers.heat.setAttribute("opacity", "0.65");
  } else if (mode === "standalone-heatmap") {
    layers.baseline.setAttribute("visibility", "hidden");
    layers.result.setAttribute("visibility", "visible");
    layers.frame.setAttribute("visibility", "hidden");       // 只看热图
    layers.heat.setAttribute("opacity", "1");
  } else if (mode === "side-by-side") {
    layers.baseline.setAttribute("visibility", "visible");
    layers.result.setAttribute("visibility", "visible");
    layers.result.setAttribute("transform", `translate(${layers.width + gap} 0)`);
    layers.frame.setAttribute("visibility", "visible");
    layers.heat.setAttribute("opacity", "0");                // 并排就是纯基线 vs 结果
  }
}

const opacitySlider = document.querySelector("#heat-opacity");   // aria-label="热图叠加透明度"
opacitySlider.addEventListener("input", () => setHeatOpacity(layers, +opacitySlider.value / 100));

function showHeatStatus(status) {
  const msg = { 422: "热图参数不兼容，无法生成对比。",
                409: "原始帧正在回收，暂时无法预览。",
                410: "原始帧证据已回收，当前无法复核。" }[status] || "热图暂时不可用。";
  statusText.textContent = msg;
  statusText.dataset.state = "unknown";        // 琥珀, 绝不 healthy
}

function missingFrameText(ev) {
  if (!ev.baseline.available && !ev.result.available)
    return "对比不可用：基线与结果原始帧均已回收，无法复核。";
  return `对比不完整：${ev.baseline.available ? "结果帧" : "基线帧"}已回收，无法复核。`;
}
