/* 情绪卡片：情绪仪表 + 语气徽标 + 最近播报。原生 JS，无框架。
   依赖 #emotion-card 与可选 window.navStack。 */
(() => {
  "use strict";
  const mount = document.getElementById("emotion-card");
  if (!mount) return;
  const API = "/api/voice/emotion";
  const MOOD_COLOR = {
    "喜": "#ffbd59", "怒": "#ff5d73", "哀": "#39d0ff",
    "乐": "#35d39a", "惊": "#9b7bff", "平": "#8ea3b5"
  };
  const el = (t, c = "", x = "") => {
    const n = document.createElement(t);
    if (c) n.className = c;
    if (x !== "") n.textContent = x;
    return n;
  };
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function padGauge(label, value) {           // value: -1..1
    const wrap = el("div", "emo-gauge");
    wrap.append(el("span", "emo-gauge-label", label));
    const track = el("div", "emo-track");
    const center = el("div", "emo-center");
    const fill = el("div", "emo-fill");
    const pct = clamp((value + 1) / 2, 0, 1) * 100;
    fill.style.width = pct + "%";
    fill.style.background = value >= 0 ? "#35d39a" : "#ff5d73";
    track.append(center, fill);
    wrap.append(track, el("span", "emo-gauge-val", value.toFixed(2)));
    return wrap;
  }

  function lineRow(line, onPick) {
    const row = el("div", "emo-line");
    const badge = el("span", "emo-style-badge", line.style || "");
    badge.style.borderColor = MOOD_COLOR[line.mood] || "#8ea3b5";
    badge.style.color = MOOD_COLOR[line.mood] || "#8ea3b5";
    const txt = el("span", "emo-line-text", line.text || "");
    const meta = el("span", "emo-line-meta",
      `${line.purpose || ""} · ${line.mood || ""}`);
    row.append(badge, txt, meta);
    row.tabIndex = 0;
    row.addEventListener("click", () => onPick(line));
    row.addEventListener("keydown", e => {
      if (e.key === "Enter" || e.key === " ") onPick(line);
    });
    return row;
  }

  function showDetail(line) {
    const detail = {
      view: "emotion-line", line,
      mood: line.mood, style: line.style, purpose: line.purpose
    };
    if (window.navStack && window.navStack.pushView) window.navStack.pushView(detail);
    mount.innerHTML = "";
    const back = el("button", "emo-back", "← 返回");
    back.addEventListener("click", render);
    mount.append(back, el("h3", "emo-title", "播报详情"));
    const body = el("pre", "emo-detail", JSON.stringify(line, null, 2));
    mount.append(body);
  }

  async function render() {
    let data;
    try {
      const res = await fetch(API, { headers: { Accept: "application/json" } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      mount.innerHTML = "";
      mount.append(el("div", "emo-error", `情绪状态获取失败：${err.message}`));
      return;
    }

    mount.innerHTML = "";
    const head = el("div", "emo-head");
    const mood = data.mood || "平";
    const moodChip = el("span", "emo-mood", mood);
    moodChip.style.background = (MOOD_COLOR[mood] || "#8ea3b5") + "22";
    moodChip.style.borderColor = MOOD_COLOR[mood] || "#8ea3b5";
    moodChip.style.color = MOOD_COLOR[mood] || "#8ea3b5";
    head.append(moodChip,
      el("span", "emo-style", `语气：${data.style || "平和"}`),
      el("span", "emo-int", `强度 ${(data.intensity || 0).toFixed(2)}`));
    mount.append(head);

    const pad = data.pad || { p: 0, a: 0, d: 0 };
    const gauges = el("div", "emo-gauges");
    gauges.append(padGauge("愉悦", pad.p), padGauge("唤醒", pad.a),
                  padGauge("掌控", pad.d));
    mount.append(gauges);

    if (Array.isArray(data.rapport) && data.rapport.length) {
      const rap = el("div", "emo-rapport");
      rap.append(el("h4", "", "关系"));
      data.rapport.forEach(r => {
        rap.append(el("div", "emo-rapport-row",
          `${r.user} · 熟络 ${(r.familiarity || 0).toFixed(2)} · 信任 ${(r.trust || 0).toFixed(2)}`));
      });
      mount.append(rap);
    }

    const lines = data.recent_lines || [];
    const list = el("div", "emo-lines");
    list.append(el("h4", "", "最近播报"));
    if (!lines.length) list.append(el("div", "emo-empty", "暂无播报"));
    lines.slice().reverse().forEach(line => list.append(lineRow(line, showDetail)));
    mount.append(list);
  }

  render();
  setInterval(render, 5000);
})();
