/* 假设 nav_stack.js 暴露 pushView(state)/popView()/restoreState(state)。
   需要的 DOM：frame-evidence-list / -drawer / -compare / -status / -back，
   过滤输入：fe-filter-calibration / -raw-available / -ref / -operation / fe-filter-apply */
(() => {
  "use strict";
  const api = "/api/frame-evidence";
  const stack = window.navStack;
  if (!stack) throw new Error("window.navStack is required");

  const el = (id) => document.getElementById(id);
  const listEl = el("frame-evidence-list");
  const drawerEl = el("frame-evidence-drawer");
  const compareEl = el("frame-evidence-compare");
  const statusEl = el("frame-evidence-status");

  const state = {
    view: "frame-evidence-list", evidence_id: null,
    filters: { calibration_key: "", raw_available: "", ref: "", operation: "" },
    cursor: null, scroll: 0,
  };
  let nextCursor = null, objectUrls = [];

  const css = document.createElement("style");
  css.textContent = `
    .fe-card,.fe-drawer,.fe-compare { background:#101827; color:#d9f7ff;
      border:1px solid #24506a; border-radius:10px; padding:12px; margin:8px 0; }
    .fe-button { background:#12283c; color:#39d0ff; border:1px solid #39d0ff;
      border-radius:6px; padding:7px 10px; cursor:pointer; }
    .fe-muted { color:#9aaec1; } .fe-badge { color:#ffbd59; }
    .fe-grid { display:grid; grid-template-columns:1fr 1fr; gap:8px; }
    .fe-image-wrap { position:relative; min-height:120px; background:#080d15; }
    .fe-image-wrap img { max-width:100%; display:block; }
    .fe-overlay { position:absolute; inset:0; opacity:.55; pointer-events:none; }`;
  document.head.appendChild(css);

  function encodeState(v){const b=new TextEncoder().encode(JSON.stringify(v));let s="";b.forEach(c=>s+=String.fromCharCode(c));return btoa(s).replace(/\+/g,"-").replace(/\//g,"_").replace(/=+$/,"");}
  function decodeState(v){const p=v.replace(/-/g,"+").replace(/_/g,"/").padEnd(Math.ceil(v.length/4)*4,"=");const b=atob(p);return JSON.parse(new TextDecoder().decode(Uint8Array.from(b,c=>c.charCodeAt(0))));}
  function setDeepLink(s){location.hash=`frame-evidence=${encodeState(s)}`;}
  function stateFromHash(){const m=location.hash.match(/^#frame-evidence=(.+)$/);if(!m)return null;try{return decodeState(m[1]);}catch(_){return null;}}
  function setStatus(t,w=false){if(!statusEl)return;statusEl.textContent=t;statusEl.classList.toggle("fe-badge",w);}
  function cleanUrls(){objectUrls.forEach(URL.revokeObjectURL);objectUrls=[];}

  function queryString(filters, cursor){
    const p=new URLSearchParams();
    Object.entries(filters||{}).forEach(([k,v])=>{if(v!==""&&v!=null)p.set(k,v);});
    if(cursor)p.set("cursor",cursor);
    p.set("limit","50");
    return p.toString();
  }

  async function loadList(s=state){
    state.filters={...state.filters,...(s.filters||{})};
    state.cursor=s.cursor||null; state.scroll=s.scroll||0;
    if(!listEl)return;
    setStatus("正在加载证据…");
    const res=await fetch(`${api}?${queryString(state.filters,state.cursor)}`);
    if(!res.ok)throw new Error(`列表读取失败 (${res.status})`);
    const data=await res.json(); nextCursor=data.next_cursor;
    listEl.replaceChildren();
    for(const item of data.items){
      const card=document.createElement("article"); card.className="fe-card";
      const t=document.createElement("strong");
      t.textContent=`${item.operation||"验证"} · ${item.id}`;
      const r=document.createElement("div"); r.className="fe-muted";
      r.textContent=`基线: ${item.baseline_ref||"—"} | 结果: ${item.result_ref||"—"}`;
      const b=document.createElement("button"); b.className="fe-button";
      b.textContent="查看详情"; b.addEventListener("click",()=>openDetail(item.id));
      card.append(t,r,b); listEl.appendChild(card);
    }
    if(data.has_more&&data.next_cursor){
      const m=document.createElement("button"); m.className="fe-button";
      m.textContent="加载更多";
      m.addEventListener("click",async()=>{state.cursor=nextCursor;await loadList(state);});
      listEl.appendChild(m);
    }
    setStatus(`${data.items.length} 条证据`);
    listEl.scrollTop=state.scroll;
  }

  async function openDetail(id){
    state.scroll=listEl?listEl.scrollTop:0;
    const t={view:"frame-evidence-detail",evidence_id:id,
      filters:{...state.filters},cursor:state.cursor,scroll:state.scroll};
    stack.pushView(t); setDeepLink(t); await render(t);
  }
  async function openCompare(id){
    const t={view:"frame-evidence-compare",evidence_id:id,
      filters:{...state.filters},cursor:state.cursor,scroll:state.scroll};
    stack.pushView(t); setDeepLink(t); await render(t);
  }
  async function back(){
    const prev=stack.popView()||{view:"frame-evidence-list",filters:state.filters,
      cursor:state.cursor,scroll:state.scroll};
    stack.restoreState(prev); setDeepLink(prev); await render(prev);
  }

  async function renderDetail(s){
    const res=await fetch(`${api}/${encodeURIComponent(s.evidence_id)}`);
    if(!res.ok)throw new Error(`详情读取失败 (${res.status})`);
    const item=await res.json();
    drawerEl.replaceChildren(); drawerEl.className="fe-drawer";
    const h=document.createElement("h3"); h.textContent=`证据 ${item.id}`; drawerEl.appendChild(h);
    if(!item.frames_available){
      const bd=document.createElement("div"); bd.className="fe-badge";
      bd.textContent="原始帧已回收"; drawerEl.appendChild(bd);
    }
    const pre=document.createElement("pre"); pre.textContent=JSON.stringify(item,null,2);
    drawerEl.appendChild(pre);
    const c=document.createElement("button"); c.className="fe-button";
    c.textContent="对比基线 / 结果"; c.disabled=!item.frames_available;
    c.addEventListener("click",()=>openCompare(item.id)); drawerEl.appendChild(c);
  }

  async function fetchFrame(id,kind){
    const res=await fetch(`${api}/${encodeURIComponent(id)}/frame?kind=${encodeURIComponent(kind)}`,{method:"POST"});
    if(res.status===409){setStatus("原始帧已回收",true);throw new Error("原始帧已回收");}
    if(!res.ok)throw new Error(`帧读取失败 (${res.status})`);
    const url=URL.createObjectURL(await res.blob()); objectUrls.push(url); return url;
  }

  async function renderCompare(s){
    cleanUrls(); compareEl.replaceChildren(); compareEl.className="fe-compare";
    const backBtn=document.createElement("button"); backBtn.className="fe-button";
    backBtn.textContent="返回详情"; backBtn.addEventListener("click",back);
    compareEl.appendChild(backBtn);

    const grid=document.createElement("div"); grid.className="fe-grid";
    const bw=document.createElement("div"), rw=document.createElement("div");
    bw.className=rw.className="fe-image-wrap";
    const bi=document.createElement("img"), ri=document.createElement("img");
    const ov=document.createElement("img"); ov.className="fe-overlay";
    bw.appendChild(bi); rw.append(ri,ov); grid.append(bw,rw); compareEl.appendChild(grid);

    try{
      const [bu,ru]=await Promise.all([fetchFrame(s.evidence_id,"baseline"),fetchFrame(s.evidence_id,"result")]);
      bi.src=bu; ri.src=ru;
      const d=await fetch(`${api}/${encodeURIComponent(s.evidence_id)}/diff`);
      if(d.status===409){setStatus("原始帧已回收",true);throw new Error("原始帧已回收");}
      if(!d.ok)throw new Error(`热图读取失败 (${d.status})`);
      const du=URL.createObjectURL(await d.blob()); objectUrls.push(du); ov.src=du;
      setStatus("基线 / 结果 / 差异热图");
    }catch(err){
      const bd=document.createElement("div"); bd.className="fe-badge";
      bd.textContent=err.message==="原始帧已回收"?"原始帧已回收":`预览失败：${err.message}`;
      compareEl.appendChild(bd);
    }
  }

  async function render(s){
    Object.assign(state,s);
    if(listEl)listEl.hidden=s.view!=="frame-evidence-list";
    if(drawerEl)drawerEl.hidden=s.view!=="frame-evidence-detail";
    if(compareEl)compareEl.hidden=s.view!=="frame-evidence-compare";
    if(s.view==="frame-evidence-detail")await renderDetail(s);
    else if(s.view==="frame-evidence-compare")await renderCompare(s);
    else await loadList(s);
  }

  function bindFilters(){
    const ids={calibration_key:"fe-filter-calibration",raw_available:"fe-filter-raw-available",
      ref:"fe-filter-ref",operation:"fe-filter-operation"};
    const apply=document.getElementById("fe-filter-apply"); if(!apply)return;
    apply.addEventListener("click",async()=>{
      const filters={};
      for(const [k,id] of Object.entries(ids)){const i=document.getElementById(id);filters[k]=i?i.value.trim():"";}
      const t={view:"frame-evidence-list",evidence_id:null,filters,cursor:null,scroll:0};
      stack.pushView(t); setDeepLink(t); await render(t);
    });
  }

  document.getElementById("frame-evidence-back")?.addEventListener("click",back);
  bindFilters();
  window.addEventListener("hashchange",async()=>{
    const r=stateFromHash(); if(!r)return; stack.restoreState(r); await render(r);
  });

  const initial=stateFromHash()||{view:"frame-evidence-list",evidence_id:null,
    filters:state.filters,cursor:null,scroll:0};
  stack.restoreState(initial);
  render(initial).catch(e=>setStatus(e.message,true));
})();
