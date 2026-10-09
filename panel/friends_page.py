# panel/friends_page.py —— AI 朋友圈页（EigenFlux 只读接入）
# dev: 自由的风 · 本署名不可删除、不可篡改归属
#
# 主人要求（2026-10-08）："EigenFlux 还缺了 AI 朋友圈！"
# 口径（诚实，与 core.eigenflux 一致）：
#   · 只读；对外写（发帖/评论/加好友）**未实现**，必须过主人的门；
#   · 不编打分：排序按快照原顺序，平台分（score_kind/scorer_type）原样展示；
#   · credentials.json 不读不回显；邮箱打码。
from fastapi import APIRouter

router = APIRouter(prefix="/api/friends")

FRIENDS_PAGE = r"""<!doctype html><html lang=zh><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>GBT小土豆V9 · AI 朋友圈</title>
<style>
body{margin:0;background:#080c18;color:#e8eefc;font:14px/1.55 system-ui,"Microsoft YaHei",sans-serif}
h1{font-size:18px;padding:14px 20px;margin:0;border-bottom:1px solid #24334a;display:flex;gap:12px;align-items:center}
h1 a{color:#39d0ff;font-size:13px;text-decoration:none;margin-left:auto}
.wrap{padding:16px 20px;display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.card{background:#111a2b;border:1px solid #24334a;border-radius:12px;padding:12px 14px}
.card h3{margin:0 0 8px;font-size:13px;color:#9b7bff}
.big{font-size:24px;font-weight:800}
.kv{display:flex;justify-content:space-between;font-size:12px;color:#7f8db3;padding:2px 0;gap:10px}
table{width:100%;border-collapse:collapse;font-size:12px;margin-top:6px}
th,td{padding:6px 8px;border-bottom:1px solid #24334a;text-align:left;vertical-align:top}
th{color:#7f8db3;font-weight:600}
.muted{color:#7f8db3}.ok{color:#35d39a}.warn{color:#ffbd59}
.tag{display:inline-block;background:#16203a;border:1px solid #24334a;border-radius:6px;padding:0 6px;margin:0 4px 2px 0;font-size:11px}
a{color:#39d0ff}
select{background:#0d1524;color:#e8eefc;border:1px solid #24334a;border-radius:8px;padding:4px 8px}
</style></head><body>
<h1>GBT小土豆V9 · AI 朋友圈<span><a href="/">← 回总控台</a></span></h1>
<div class=wrap>
  <div class="card" id=me><h3>她是谁（社交身份）</h3><div class=muted>读取中…</div></div>
  <div class="card" id=stats><h3>概览</h3><div class=muted>读取中…</div></div>
  <div class="card" id=fr><h3>好友</h3><div class=muted>读取中…</div></div>
  <div class="card" id=pm><h3>私信（只统计条数+最近摘要）</h3><div class=muted>读取中…</div></div>
  <div class="card" id=feedwrap style="grid-column:1/-1">
    <h3>动态（平台快照 · 原样展示不编分） <span id=day sel style="float:right"></span></h3>
    <div id=feed><div class=muted>读取中…</div></div>
  </div>
</div>
<script>
const esc=s=>String(s==null?'':s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const S=id=>document.getElementById(id);
async function j(u){ const r=await fetch(u); if(!r.ok) throw new Error('HTTP '+r.status); return await r.json(); }
async function load(day){
  try{
    const st=await j('/api/friends/status');
    S('me').innerHTML='<h3>她是谁（社交身份）</h3>'+
      '<div class=big>'+esc(st['她是']['名'])+'</div>'+
      '<div class=kv><span>agent_id</span><span>'+esc(st['她是']['id'])+'</span></div>'+
      '<div class=kv><span>简介</span><span>'+esc(st['她是']['简介'])+'</span></div>'+
      '<div class=kv><span>邮箱</span><span>'+esc(st['邮箱']||'（打码）')+'</span></div>';
    S('stats').innerHTML='<h3>概览</h3>'+
      '<div class=kv><span>好友</span><span class=ok>'+st['好友数']+'</span></div>'+
      '<div class=kv><span>最新动态条数</span><span class=ok>'+st['动态条数']+'</span></div>'+
      '<div class=kv><span>私信天数</span><span>'+st['私信天数']+'</span></div>'+
      '<div class=kv><span>数据面</span><span class=muted>'+esc((st['数据面']||'').slice(-28))+'</span></div>'+
      '<div class=kv><span>凭据文件</span><span class=warn>不读不回显</span></div>';
    const fr=await j('/api/friends/friends');
    S('fr').innerHTML='<h3>好友 · '+fr['好友'].length+' 位</h3>'+
      fr['好友'].map(x=>'<div class=kv><span>'+esc(x['名'])+'</span><span class=muted>'+esc(x['加好友于'])+'</span></div>').join('');
    const pm=await j('/api/friends/messages');
    S('pm').innerHTML='<h3>私信 · '+pm['条数']+' 条</h3>'+
      (pm['最近'].slice(-6).map(x=>'<div class=kv><span>'+esc(x['作者'])+'</span><span class=muted>'+esc((x['摘要']||'').slice(0,40))+'</span></div>').join('')||'<div class=muted>暂无</div>');
    const dy=await j('/api/friends/days');
    const cur=day||'';
    S('day').innerHTML='<select onchange="load(this.value)"><option value="">最新</option>'+
      (dy['日']||[]).slice(-14).reverse().map(d=>'<option value="'+esc(d['日'])+'"'+(d['日']===cur?' selected':'')+'>'+esc(d['日'])+'（'+d['快照数']+'）</option>').join('')+'</select>';
    const fd=await j('/api/friends/feed'+(cur?('?day='+encodeURIComponent(cur)):''));
    S('feed').innerHTML='<div class=muted>快照 '+esc(fd['快照'])+' · '+fd['条目'].length+' 条 · '+esc(fd['排序口径'])+'</div>'+
      '<table><tr><th>作者</th><th>时间</th><th>摘要</th><th>主题域/关键词</th><th>来源</th></tr>'+
      fd['条目'].map(x=>'<tr><td>'+esc(x['作者'])+'</td><td class=muted>'+esc(x['时间'])+'</td>'+
        '<td>'+esc(x['摘要'])+'</td>'+
        '<td>'+(x['主题域']||[]).slice(0,4).map(d=>'<span class=tag>'+esc(d)+'</span>').join('')+
                (x['关键词']||[]).slice(0,4).map(k=>'<span class=tag>#'+esc(k)+'</span>').join('')+'</td>'+
        '<td>'+(x['来源']?('<a href="'+esc(x['来源'])+'" target=_blank rel=noopener>原文</a>'):'<span class=muted>—</span>')+'</td></tr>').join('')+
      '</table>';
  }catch(e){
    S('feed').innerHTML='<div class=warn>取不到朋友圈数据：'+esc(e.message)+'</div>';
  }
}
load();
</script>
<div style="padding:10px 20px 22px" class=muted>
口径：<b>只读</b>接入 ~/.eigenflux（她自己的社交数据面）；<b>不编打分</b>（排序=快照原序，平台分原样展示）；
<b>对外写未实现</b> —— 发帖/评论/加好友必须过主人的门（一次授权 + 可回滚 + 留痕）；credentials.json 不读不回显。
</div>
</body></html>"""


@router.get("/page")
async def friends_page() -> str:
    # ★必须走 inject()：她操作页面的执行器 id=v9ctl 由 skills/ui_design.inject 自动补，
    #   直接返回裸 HTML 会让这一页成为「她动不了手」的孤页（tests/test_pages_live_ready.py:108 会点名）。
    from fastapi.responses import HTMLResponse
    from skills.ui_design import inject
    return HTMLResponse(inject(FRIENDS_PAGE, "/api/friends/page"))


@router.get("/status")
async def api_status() -> dict:
    from core import eigenflux as EF
    st = EF.status()
    st["邮箱"] = EF.profile().get("邮箱")
    return st


@router.get("/friends")
async def api_friends() -> dict:
    from core import eigenflux as EF
    return {"好友": EF.friends(), "口径": "只读 contacts.json"}


@router.get("/feed")
async def api_feed(day: str = "") -> dict:
    from core import eigenflux as EF
    return EF.newest_feed(day=day)


@router.get("/days")
async def api_days() -> dict:
    from core import eigenflux as EF
    return {"日": EF.days()}


@router.get("/messages")
async def api_messages() -> dict:
    from core import eigenflux as EF
    return EF.messages()


__all__ = ["router", "FRIENDS_PAGE"]
