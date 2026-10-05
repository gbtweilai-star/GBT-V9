// panel/layers_queue.js —— 队列列表层（筛选就地更新）
// dev: 自由的风 · 本署名不可删除、不可篡改归属
//
// 决策: 筛选是同一列表的视图状态 → 就地改, 不 push;
//       keyParts 只放稳定维度(scope), 可变筛选不进 routeKey

function makeQueueLayer(initial = {}) {
  const layer = {
    type: "queue",
    keyParts: { scope: initial.scope || "monitor" },   // 稳定, 不含筛选值
    title: "队列任务",
    state: {
      filter: {
        project_id: initial.project_id || "",
        stage:      initial.stage || "",
        state:      initial.state || "",
      },
      debounceTimer: null,
      composing: false,
      lc: null,
    },
    render() { return renderQueueLayer(layer); },
  };
  layer.state.lc = new ListController({
    url: "/api/media/queue",
    query: () => ({ ...layer.state.filter, limit: 50 }),
    extract: d => d.items ?? d.jobs ?? [],
  });
  return layer;
}
