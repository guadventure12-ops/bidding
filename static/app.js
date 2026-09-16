const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icons = {
  dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  library:'<path d="M4 5v15m4-17v17m4-15v15m3-16 5 15M3 21h18"/>',
  settings:'<path d="M4 7h16M4 17h16"/><circle cx="9" cy="7" r="3" fill="currentColor" stroke="none"/><circle cx="15" cy="17" r="3" fill="currentColor" stroke="none"/>',
  shield:'<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/>',
  refresh:'<path d="M20 11a8 8 0 0 0-14-5L3 9m0-5v5h5m-4 4a8 8 0 0 0 14 5l3-3m0 5v-5h-5"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  arrow:'<path d="M5 12h14m-5-5 5 5-5 5"/>',
  back:'<path d="M19 12H5m5-5-5 5 5 5"/>',
  file:'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  upload:'<path d="M12 16V3m-5 5 5-5 5 5M4 14v6h16v-6"/>',
  download:'<path d="M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  spark:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  checkcircle:'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  alert:'<path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.1"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
  edit:'<path d="m16 3 5 5-12 12-6 1 1-6L16 3ZM13 6l5 5"/>',
  link:'<path d="m10 13 4-4m-6 7-2 2a4 4 0 0 1-6-6l5-5a4 4 0 0 1 6 0m2 1 2-2a4 4 0 0 1 6 6l-5 5a4 4 0 0 1-6 0" transform="translate(1 -1)"/>',
  folder:'<path d="M3 7V5h7l2 2h9v13H3V7Z"/>',
  eye:'<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
  stop:'<rect x="5" y="5" width="14" height="14" rx="2"/>',
};
function icon(name){ return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name] || icons.file}</svg>`; }
$$('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
const statusNames={draft:'草稿',pending:'待审核',approved:'已批准',expired:'已失效',disabled:'已停用',ready:'已解析',error:'解析异常',queued:'等待执行',running:'执行中',succeeded:'已完成',failed:'执行失败',cancelled:'已取消',interrupted:'已中断',analyzed:'已解析',generated:'已生成',reviewed:'已审核',analyzing:'解析中',generating:'生成中',reviewing:'审核中',review:'待复核',local_candidates:'本地候选',complete:'已完成',ai_complete:'完整分析',gap:'待补充',drafted:'待确认',confirmed:'已确认',not_applicable:'不适用',partial:'分析未完整'};
const domainNames={archive:'会计电子档案系统',expense:'费控系统'};
const scopeNames={general:'通用企业资料',archive:'会计电子档案',expense:'费控系统',historical:'历史投标参考',internal:'内部资料'};
const categoryNames={technical:'技术要求',business:'商务要求',qualification:'资格要求',scoring:'评分项',commercial:'商务要求',delivery:'交付要求',format:'编制格式',security:'安全要求',implementation:'实施要求',service:'服务要求',pricing:'报价要求',other:'其他要求'};
const modeNames={outline:'AI规划下级目录',paginate:'索引页数更新',reparse:'文档重新解析',analyze:'招标解析',generate:'投标书生成',review:'交付审核',import:'文件导入',knowledge_import:'知识库导入',tender_import:'招标文件导入'};
const state={route:'dashboard',projectId:null,tab:'analyze',dashboard:null,project:null,knowledge:null,settings:null,selectedReq:null,selectedSection:null,requirementFilter:'all',projectFilter:'all',query:'',knowledgeStatus:'all',knowledgeScope:'all',knowledgePage:1,knowledgeSelected:new Set(),knowledgeSelectionAll:false,knowledgeOperations:[],dirty:false,jobs:new Map(),polling:false,routeToken:0,dialogToken:0,files:[],importKind:null,importMode:'upload'};
const formRevisions=new WeakMap();
const savedFormRevisions=new WeakMap();
function markFormEdited(form){if(form)formRevisions.set(form,(formRevisions.get(form)||0)+1);}
function saveContext(form){return {form,revision:formRevisions.get(form)||0,routeToken:state.routeToken,projectId:state.projectId,dialog:!!form.closest('#dialog'),dialogToken:state.dialogToken};}
function saveIsCurrent(context){return context.form.isConnected&&context.routeToken===state.routeToken&&(!context.dialog||($('#dialog').open&&context.dialogToken===state.dialogToken));}
function finishSave(context){
  if(!saveIsCurrent(context))return false;
  savedFormRevisions.set(context.form,context.revision);
  const otherDialogEdits=context.dialog&&$$('form',$('#dialog')).some(form=>form!==context.form&&(formRevisions.get(form)||0)!==(savedFormRevisions.get(form)||0));
  if((formRevisions.get(context.form)||0)!==context.revision){
    if(context.dialog)state.dialogDirty=true;else state.dirty=true;
    const label=$('#editor-save-state')||$('#settings-save-label');if(label)label.textContent='提交时的版本已保存；后续修改尚未保存';
    toast('提交时的版本已保存，保存期间的新修改已保留，请继续保存。');return false;
  }
  if(context.dialog){state.dialogDirty=otherDialogEdits;if(otherDialogEdits){toast('本项已保存，同一窗口中的其他修改尚未保存。');return false;}}else state.dirty=false;
  return true;
}
async function finishProjectSave(context){if(!saveIsCurrent(context))return;const render=finishSave(context);if(context.dialog&&render)closeDialog();await refreshProject(render);}
function dialogIsCurrent(token){return token!==null&&$('#dialog').open&&state.dialogToken===token;}
function badge(status,label){let cls=['approved','confirmed','succeeded','ready','complete','reviewed'].includes(status)?'good':['pending','drafted','gap','local_candidates','partial'].includes(status)?'warning':['failed','error','interrupted','expired'].includes(status)?'error':['running','queued','analyzing','generating'].includes(status)?'working':'neutral';return `<span class="badge ${cls}">${esc(label||statusNames[status]||status||'待开始')}</span>`;}
function date(value){if(!value)return '—';const d=new Date(value);return Number.isNaN(d.getTime())?esc(value):d.toLocaleDateString('zh-CN',{month:'2-digit',day:'2-digit',year:'numeric'});}
function time(value){if(!value)return '';const d=new Date(value);return Number.isNaN(d.getTime())?'':d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});}
function size(value){return value>=1048576?`${(value/1048576).toFixed(1)} MB`:value>=1024?`${Math.round(value/1024)} KB`:`${value||0} B`;}
function fmt(value){return Number(value||0).toLocaleString('zh-CN');}
function btn(label,action,extra='',cls=''){return `<button type="button" class="button ${cls}" data-action="${action}" ${extra}>${label}</button>`;}
function empty(title,description,action='',compact=false){return `<div class="empty ${compact?'compact':''}"><span class="empty-symbol">${icon('file')}</span><h3>${esc(title)}</h3><p>${esc(description)}</p>${action}</div>`;}
function note(text,type='info'){return `<div class="info-note ${type==='info'?'':type}">${icon(type==='error'||type==='warning'?'alert':'info')}<div>${text}</div></div>`;}
function jsonDetails(value){return typeof value==='string'?value:value && Object.keys(value).length?JSON.stringify(value,null,2):'';}
function safeLocalUrl(value){if(typeof value!=='string'||!value.startsWith('/')||value.startsWith('//')||/[\\\u0000-\u001f]/.test(value))return null;return value;}
async function api(url,options={}){
  const opt={...options,headers:{...options.headers}};
  if(opt.body && !(opt.body instanceof FormData)){opt.headers['Content-Type']='application/json';opt.body=JSON.stringify(opt.body);}
  let response;try{response=await fetch(url,opt);}catch{throw new Error('无法连接本地服务。请确认招投标启动窗口仍在运行，然后刷新重试。');}
  const raw=await response.text();let data;try{data=raw?JSON.parse(raw):{};}catch{data={detail:raw.slice(0,700)};}
  if(!response.ok){let detail=data.detail||data.message||`请求失败（${response.status}）`;if(Array.isArray(detail))detail=detail.map(x=>x.msg||JSON.stringify(x)).join('；');if(typeof detail==='object')detail=JSON.stringify(detail,null,2);throw new Error(detail);}
  return data;
}
function toast(message,error=false){const el=document.createElement('div');el.className=`toast ${error?'error':''}`;el.innerHTML=`${icon(error?'alert':'checkcircle')}<span>${esc(message)}</span><button type="button" aria-label="关闭通知">×</button>`;el.querySelector('button').onclick=()=>el.remove();$('#toast-region').append(el);setTimeout(()=>el.remove(),error?14000:6000);}
async function busy(button,fn){if(button?.disabled)return;const html=button?.innerHTML;if(button){button.disabled=true;button.innerHTML=`<span class="spinner"></span>处理中…`;}try{return await fn();}finally{if(button?.isConnected){button.disabled=false;button.innerHTML=html;}}}
function updateSidebar(settings){if(!settings)return;$('#sidebar-company').textContent=settings.company_name||'我的企业空间';$('#connection-label').textContent=settings.key_configured?'DeepSeek 已配置':'DeepSeek 待配置';$('#connection-dot').className='connection-dot online';}
function routeInfo(){const bits=location.hash.replace(/^#/,'').split('/');if(bits[0]==='project'&&bits[1])return {route:'project',projectId:bits[1],tab:['analyze','requirements','outline','sections','review'].includes(bits[2])?bits[2]:'analyze'};return {route:['knowledge','outline-library','settings','review-settings'].includes(bits[0])?bits[0]:'dashboard',projectId:null,tab:'analyze',knowledgeTab:bits[0]==='knowledge'&&bits[1]==='modules'?'modules':'documents'};}
function allowLeave(){if(state.productModuleBusy){toast('正在保存本次模块操作，请稍候。');return false;}if($('#dialog [data-uploading="true"]')){toast('文件正在上传，请等待上传完成后离开。');return false;}if(state.dialogDirty&&!allowCloseDialog())return false;if($('#dialog').open){++state.dialogToken;$('#dialog').close();state.files=[];}if(!state.dirty)return true;if(window.confirm('有尚未保存的编辑内容。离开将丢失这些修改，是否继续？')){state.dirty=false;return true;}return false;}
async function navigate(force=false){
  const target=routeInfo();Object.assign(state,target);state.currentHash=location.hash;state.dirty=false;const token=++state.routeToken;
  $$('#primary-nav a').forEach(a=>{const on=a.dataset.nav===(target.route==='project'?'dashboard':target.route);a.classList.toggle('active',on);if(on)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
  $('#page-name').textContent={dashboard:'项目总览',knowledge:'企业知识库','outline-library':'通用目录库',settings:'模型与企业设置','review-settings':'审核设置',project:'项目工作台'}[target.route];
  $('#main').innerHTML='<div class="page-loading"><span class="spinner"></span>正在加载…</div>';
  try{
    if(target.route==='dashboard'){const data=await api('/api/dashboard');if(token!==state.routeToken)return;state.dashboard=data;updateSidebar(data.settings);trackJobs(data.jobs||data.recent_jobs||[]);renderDashboard();}
    if(target.route==='knowledge'&&target.knowledgeTab==='modules'){const data=await api('/api/product-modules');if(token!==state.routeToken)return;state.productModules=data;renderProductModules();}
    if(target.route==='knowledge'&&target.knowledgeTab!=='modules'){const [data,dashboard,operations]=await Promise.all([api('/api/knowledge'+(state.query?'?q='+encodeURIComponent(state.query):'')),api('/api/dashboard'),api('/api/knowledge/approval/operations')]);if(token!==state.routeToken)return;state.knowledge=data;state.knowledgeOperations=operations.operations;trackJobs(dashboard.jobs||[]);updateSidebar(dashboard.settings);renderKnowledge();}
    if(target.route==='outline-library'){const data=await api('/api/outline-library');if(token!==state.routeToken)return;state.outlineLibrary=JSON.parse(JSON.stringify(data));state.outlineEditVersion=0;renderOutlineLibrary();}
    if(target.route==='settings'){const data=await api('/api/settings');if(token!==state.routeToken)return;state.settings=data;updateSidebar(data);renderSettings();}
    if(target.route==='review-settings'){const data=await api('/api/review-settings');if(token!==state.routeToken)return;state.reviewSettings=data;renderReviewSettings();}
    if(target.route==='project'){const data=await api('/api/projects/'+encodeURIComponent(target.projectId));if(token!==state.routeToken)return;state.project=data;if(target.tab==='outline'){const plan=data.compilation_outline||await api('/api/projects/'+encodeURIComponent(target.projectId)+'/outline-plan');if(token!==state.routeToken)return;setOutlineDraft(plan);}trackJobs(data.jobs||[]);renderProject();}
  }catch(error){if(token===state.routeToken){$('#main').innerHTML=empty('工作空间暂时无法加载',error.message,btn(`${icon('refresh')}重新加载`,'refresh','','primary'));$('#connection-label').textContent='本地服务连接异常';$('#connection-dot').className='connection-dot error';}}
}
function renderDashboard(){
  const d=state.dashboard||{},s=d.stats||{};const projects=(d.projects||[]).filter(p=>state.projectFilter==='all'||p.domain===state.projectFilter);const running=s.jobs_running??(d.jobs||[]).filter(j=>['running','queued'].includes(j.status)).length;
  $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">BID WORKSPACE</div><h1>每一次投标，从容有序。</h1><p>汇集企业知识，把招标要求转化为有依据、可审核的投标响应。</p></div>${btn(`${icon('plus')}新建投标项目`,'new-project','','primary')}</div>
  <section class="hero"><div><div class="hero-overline">${icon('spark')} 招投标 · 智能投标协作</div><h2>从一份招标文件，到一份专业答卷。</h2><p>围绕会计电子档案与费控系统，连接招标要求、企业能力和交付证据，<br>让专业积累，成为下一次投标的起点。</p></div><div class="hero-flow">${[['file','解析招标'],['library','匹配知识'],['edit','生成响应'],['shield','审核交付']].map((x,i)=>`${i?icon('arrow'):''}<div class="hero-step"><span>${icon(x[0])}</span>${x[1]}</div>`).join('')}</div></section>
  <section class="stats-grid" aria-label="工作空间统计">${[[s.projects??d.projects?.length??0,'投标项目','file','从创建到交付，集中管理'],[s.approved??0,'已批准企业资料','library',`${fmt(s.knowledge??0)} 份资料已入库`],[s.requirements??0,'要求及候选','checkcircle','逐项响应，全程可追溯'],[running,'正在执行的任务','clock',running?'后台处理进行中':'当前没有执行中的任务']].map(([n,label,ic,detail])=>`<div class="stat"><div class="stat-head">${label}${icon(ic)}</div><div class="stat-value">${fmt(n)}<small>${label==='正在执行的任务'?'个':'项'}</small></div><div class="stat-detail">${esc(detail)}</div></div>`).join('')}</section>
  <div id="job-region">${renderJobs(activeJobs(d.jobs||d.recent_jobs||[]))}</div>
  <section><div class="section-heading"><h2>投标项目<span class="count">${fmt(s.projects??d.projects?.length??0)}</span></h2><div class="chips" aria-label="项目类型筛选">${[['all','全部项目'],['archive','电子档案'],['expense','费控系统']].map(([key,label])=>`<button class="chip ${state.projectFilter===key?'active':''}" data-action="project-filter" data-value="${key}">${label}</button>`).join('')}</div></div><div class="panel">${projects.length?`<div class="table-wrap"><table><thead><tr><th>项目名称</th><th>项目类型</th><th>状态</th><th>最近更新</th><th></th></tr></thead><tbody>${projects.map(p=>`<tr><td><div class="project-name"><span class="project-glyph">${icon('file')}</span><div><a href="#project/${esc(p.id)}/analyze">${esc(p.name)}</a><small>${esc(p.company_name||'未设置企业名称')}</small></div></div></td><td class="nowrap muted">${esc(domainNames[p.domain]||p.domain)}</td><td>${badge(p.status)}</td><td class="nowrap small muted">${date(p.updated_at||p.created_at)}</td><td>${btn(`${icon('arrow')}进入`,'open-project',`data-id="${esc(p.id)}"`,'small ghost')}</td></tr>`).join('')}</tbody></table></div>`:empty(state.projectFilter==='all'?'开始准备第一份投标书':'暂无此类投标项目',state.projectFilter==='all'?'创建项目并导入招标文件，招投标将帮助你建立需求台账、匹配企业资料并编写投标书。':'创建对应类型的项目后，即可在这里管理。',btn(`${icon('plus')}创建投标项目`,'new-project','','primary'))}</div></section>
  <div class="below-grid"><section class="panel"><div class="panel-head"><h2>把准备工作，变成持续积累</h2>${icon('library')}</div><div class="panel-body"><div class="guide-row"><span class="guide-number">01</span><div><h3>建立企业知识库</h3><p>汇集企业简介、资质、方案与案例，审核后作为生成依据。</p></div><a class="button small ghost" href="#knowledge">去准备 ${icon('arrow')}</a></div><div class="guide-row"><span class="guide-number">02</span><div><h3>接入 DeepSeek</h3><p>配置模型密钥，开启大篇幅招标解析和章节生成。</p></div><a class="button small ghost" href="#settings">${d.settings?.key_configured?'查看设置':'去配置'} ${icon('arrow')}</a></div></div></section><section class="panel"><div class="panel-head"><h2>专业判断，保留在你的手中</h2>${icon('shield')}</div><div class="panel-body"><p class="long-text">每项响应关联招标原文与企业证据，便于复核。资格、业绩、参数与报价缺项会保留为待补充内容。</p><div class="divider"></div><p class="muted small">完成需求确认和章节批准后，再进行正式交付检查。你始终可以先导出带标识的草稿。</p></div></section></div>`;
}
function documentStatus(d){const now=new Date(),today=[now.getFullYear(),String(now.getMonth()+1).padStart(2,'0'),String(now.getDate()).padStart(2,'0')].join('-');return d.valid_until&&d.valid_until<today?'expired':d.status;}
function filteredKnowledge(){return (state.knowledge?.documents||[]).filter(d=>(state.knowledgeStatus==='all'||documentStatus(d)===state.knowledgeStatus)&&(state.knowledgeScope==='all'||d.scope===state.knowledgeScope));}
function clearKnowledgeSelection(notify=false){state.knowledgeSelected.clear();state.knowledgeSelectionAll=false;if(notify)toast('筛选条件已改变，已清空选择。');}
function knowledgePageDocs(){return filteredKnowledge().slice((state.knowledgePage-1)*20,state.knowledgePage*20);}
function renderKnowledge(){const k=state.knowledge||{},docs=filteredKnowledge();state.knowledgePage=Math.max(1,Math.min(state.knowledgePage,Math.ceil(docs.length/20)||1));const pageDocs=knowledgePageDocs();const stats=k.stats||{};$('#knowledge-count').hidden=false;$('#knowledge-count').textContent=fmt(stats.total??k.documents?.length??0);
  $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">COMPANY KNOWLEDGE</div><h1>企业知识库</h1><p>把企业的专业积累，转化为投标书中可以追溯的依据。</p></div>${btn(`${icon('plus')}导入企业资料`,'import-knowledge','','primary')}</div>
  ${renderKnowledgeTabs('documents')}
  ${note('支持导入本地 PDF、DOCX、Markdown 等资料。新资料进入待审核状态，批准后才能用于投标事实生成。历史投标与内部资料可作为限定用途的参考。')}
  <div class="toolbar" style="margin-top:22px"><form id="knowledge-search" class="search-field">${icon('search')}<label class="visually-hidden" for="knowledge-query">搜索已批准企业证据</label><input id="knowledge-query" name="query" value="${esc(state.query)}" placeholder="检索企业能力、功能、资质或案例，回车搜索"></form><div class="chips">${[['all','全部资料'],['pending','待审核'],['approved','已批准'],['expired','已失效 / 停用']].map(([key,label])=>`<button class="chip ${state.knowledgeStatus===key?'active':''}" data-action="knowledge-filter" data-value="${key}">${label}</button>`).join('')}</div></div>
  <div id="job-region">${renderJobs([...state.jobs.values()].filter(j=>!j.project_id&&['running','queued','failed','interrupted'].includes(j.status)).slice(0,3))}</div>
  ${state.query?`<section class="panel"><div class="panel-head"><div><h2>证据检索结果</h2><p>仅检索已批准且可用的资料 · ${fmt(k.results?.length||0)} 条匹配</p></div>${btn('清除搜索','clear-search','','small')}</div>${k.results?.length?k.results.map(r=>`<div class="knowledge-result"><h3>${esc(r.document_name||'企业资料')}</h3><p>${esc(r.text)}</p><div class="actions"><small>${esc(r.locator||'')} · 匹配度 ${typeof r.score==='number'?r.score.toFixed(2):'—'}</small>${btn('查看来源','view-document',`data-id="${esc(r.document_id)}"`,'small ghost')}</div></div>`).join(''):empty('没有找到匹配证据','尝试使用功能名、产品名或资质关键词；确认相应企业资料已经审核批准。','',true)}</section>`:''}
  <section class="panel" style="margin-top:20px"><div class="panel-head"><div><h2>企业资料<span class="muted small">　${fmt(docs.length)} 份</span></h2><p>查看解析内容、来源与适用范围后再批准使用</p></div><label class="knowledge-scope-label">产品范围 <select id="knowledge-scope" aria-label="产品范围"><option value="all">全部产品范围</option>${Object.entries(scopeNames).map(([key,label])=>`<option value="${key}" ${state.knowledgeScope===key?'selected':''}>${esc(label)}</option>`).join('')}</select></label></div>
  <div class="knowledge-bulk-toolbar"><span id="knowledge-selection-count" aria-live="polite">已选 <strong>${state.knowledgeSelected.size}</strong> 份${state.knowledgeSelectionAll?' · 跨页选择当前筛选结果（ID已固定，不含随后新增资料）':''}</span><div class="actions">${btn('选择当前筛选结果全部资料（'+docs.length+'份）','knowledge-select-all',docs.length?'':'disabled','small')}${btn('清空选择','knowledge-clear-selection',state.knowledgeSelected.size?'':'disabled','small')}${btn('批量批准','knowledge-bulk-preview',state.knowledgeSelected.size?'':'disabled','primary small')}</div></div>${docs.length?`<div class="table-wrap"><table><thead><tr><th class="knowledge-checkbox"><label><input type="checkbox" id="knowledge-select-page" aria-label="全选本页" ${pageDocs.length&&pageDocs.every(d=>state.knowledgeSelected.has(d.id))?'checked':''}> 全选本页</label></th><th>资料名称</th><th>用途</th><th>审批状态</th><th>解析 / 有效期</th><th>操作</th></tr></thead><tbody>${pageDocs.map(d=>`<tr><td class="knowledge-checkbox"><input type="checkbox" data-knowledge-select="${esc(d.id)}" aria-label="选择 ${esc(d.name)}" ${state.knowledgeSelected.has(d.id)?'checked':''}></td><td><div class="project-name"><span class="doc-icon">${esc((d.format||d.name?.split('.').pop()||'FILE').toUpperCase().slice(0,5))}</span><div><button class="button ghost small" style="padding:0;text-align:left;white-space:normal" data-action="view-document" data-id="${esc(d.id)}">${esc(d.name)}</button><small>${fmt(d.page_count)} 页 / ${fmt(d.text_chars)} 字符 · ${date(d.created_at)}</small></div></div></td><td class="small muted nowrap">${esc(scopeNames[d.scope]||d.scope||'通用')}</td><td>${badge(documentStatus(d))}</td><td>${badge(d.parse_status)}<div class="small muted" style="margin-top:5px">${d.valid_until?esc(d.valid_until):'未设置有效期'}</div></td><td><div class="actions">${btn('审核资料','view-document',`data-id="${esc(d.id)}"`,'small')}${d.status==='pending'?btn('批准','approve-document',`data-id="${esc(d.id)}" `,'small soft'):''}</div></td></tr>`).join('')}</tbody></table></div>`:empty('让企业资料成为可靠依据','导入企业简介、资质证书、产品方案和历史案例。逐份审核后，生成时会检索这些资料并保留出处。',btn(`${icon('upload')}导入企业资料`,'import-knowledge','','primary'))}<div class="knowledge-pagination"><span>第 ${state.knowledgePage} / ${Math.ceil(docs.length/20)||1} 页 · 每页20份</span><div class="actions">${btn('上一页','knowledge-page',`data-value="${state.knowledgePage-1}" ${state.knowledgePage===1?'disabled':''}`,'small')}${btn('下一页','knowledge-page',`data-value="${state.knowledgePage+1}" ${state.knowledgePage*20>=docs.length?'disabled':''}`,'small')}</div></div><div class="panel-note">已导入的语雀资料可查看来源；请核对原文中的附件和外部链接，确保本次投标资料完整。</div></section>${renderKnowledgeOperations()}`;
  const pageCheck=$('#knowledge-select-page');if(pageCheck)pageCheck.indeterminate=pageDocs.some(d=>state.knowledgeSelected.has(d.id))&&!pageDocs.every(d=>state.knowledgeSelected.has(d.id));
}
function renderReviewSettings(){
  const config=state.reviewSettings;
  const groups=[...new Set(config.rules.map(r=>r.group))];
  const rows=config.rules.map((r,i)=>`${i===0||config.rules[i-1].group!==r.group?`<tr class="rule-group"><th colspan="3"><div class="rule-group-controls"><label><input type="checkbox" class="review-group-input" data-group="${esc(r.group)}" aria-label="${esc(r.group)}全选"> ${esc(r.group)}</label><span class="small" data-group-count="${esc(r.group)}"></span><div class="actions">${btn('本类全开','review-rules-bulk',`data-group="${esc(r.group)}" data-value="on"`,'small ghost')}${btn('本类全关','review-rules-bulk',`data-group="${esc(r.group)}" data-value="off"`,'small ghost')}</div></div></th></tr>`:''}<tr><td><button type="button" class="rule-name" data-action="review-rule-detail" data-id="${esc(r.id)}">${esc(r.name)}</button><small class="muted">v${esc(r.version)}</small></td><td><p>${esc(r.description)}</p></td><td><label class="rule-switch"><input type="checkbox" role="switch" class="review-rule-input" name="${esc(r.id)}" data-group="${esc(r.group)}" aria-label="${esc(r.name)}是否启用" ${r.enabled?'checked':''}><span class="rule-track" aria-hidden="true"></span><span class="rule-state">${r.enabled?'开启':'关闭'}</span></label></td></tr>`).join('');
  $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">REVIEW SETTINGS</div><h1>审核设置</h1><p>管理本地审核规则。点击规则名称查看说明、示例和版本。</p></div><span class="badge neutral">规则集 ${esc(config.version)}</span></div><section class="panel"><div class="panel-head"><div><h2>审核规则</h2><p>全部规则均可开关；点击保存后应用于整个本地工作空间。</p></div><span id="review-enabled-count" class="small muted" aria-live="polite"></span></div><div class="panel-body"><form id="review-settings-form"><div class="review-bulk-toolbar"><label><input type="checkbox" id="review-select-all"> 全选所有规则</label><span class="small muted">${config.rules.length} 项 · ${groups.length} 类</span><div class="actions">${btn('全部开启','review-rules-bulk','data-value="on"','small')}${btn('全部关闭','review-rules-bulk','data-value="off"','small')}</div></div><div class="table-wrap"><table class="review-rule-table"><thead><tr><th>规则</th><th>说明</th><th>是否启用</th></tr></thead><tbody>${rows}</tbody></table></div><p class="small muted">${esc(config.scope)}</p><p class="small muted">章节批准的风险阈值在<a href="#settings">模型与企业设置 → 总设置</a>中调整，由本页“批准风险阈值”规则控制是否拦截。</p><div id="review-settings-error" class="form-error" role="alert"></div><div class="form-footer"><span id="review-settings-save-label" class="small muted">当前显示已保存设置</span><div class="actions">${btn('重新加载','review-settings-reload')}<button type="submit" class="button primary">保存审核设置</button></div></div></form></div></section>`;
  syncReviewRuleControls();
}
function syncReviewRuleControls(){
  const inputs=$$('#review-settings-form .review-rule-input');
  const count=inputs.filter(el=>el.checked).length;
  $('#review-enabled-count').textContent=count+' / '+inputs.length+' 项规则开启';
  const sync=(control,subset)=>{const n=subset.filter(el=>el.checked).length;control.checked=!!subset.length&&n===subset.length;control.indeterminate=n>0&&n<subset.length;};
  sync($('#review-select-all'),inputs);
  $$('.review-group-input').forEach(control=>sync(control,inputs.filter(el=>el.dataset.group===control.dataset.group)));
  $$('[data-group-count]').forEach(el=>{const subset=inputs.filter(input=>input.dataset.group===el.dataset.groupCount);el.textContent=subset.filter(input=>input.checked).length+' / '+subset.length+' 项开启';});
}
function setReviewRules(group,checked){
  const inputs=$$('#review-settings-form .review-rule-input').filter(el=>group===undefined||el.dataset.group===group);
  inputs.forEach(el=>{el.checked=checked;el.closest('.rule-switch').querySelector('.rule-state').textContent=checked?'开启':'关闭';});
  syncReviewRuleControls();
  $('#review-settings-save-label').textContent='有未保存的规则更改';
  markFormEdited($('#review-settings-form'));state.dirty=true;
}
function showReviewRule(id){
  const r=state.reviewSettings?.rules.find(r=>r.id===id);if(!r)return;
  const list=items=>`<ul>${items.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`;
  openDialog(r.name,`规则版本 v${r.version} · 规则集 ${state.reviewSettings.version}`,`<h3>规则说明</h3><p>${esc(r.description)}</p><h3>什么时候触发</h3>${list(r.triggers)}<h3>什么时候不触发</h3>${list(r.exceptions)}<h3>正确触发示例</h3><pre class="rule-example">${esc(r.correct_example.text)}</pre><p>${esc(r.correct_example.result)}</p><h3>误报示例与正确判断</h3><pre class="rule-example">${esc(r.false_positive_example.text)}</pre><p>${esc(r.false_positive_example.result)}</p><p class="small muted">后端位置：${esc(r.source)} · ${r.editable?'可调整审核项':'固定校验／派生汇总'}</p>`,btn('关闭','close-dialog'),true);
}
function updateReviewRuleSwitch(input){
  input.closest('.rule-switch').querySelector('.rule-state').textContent=input.checked?'开启':'关闭';
  syncReviewRuleControls();
  $('#review-settings-save-label').textContent='有未保存的规则更改';
}
const riskSteps=['low','medium','high','ignore'];
const riskLabels={low:'低风险',medium:'中风险',high:'高风险',ignore:'无视风险'};
const riskRules={low:'拦截低、中、高风险章节；没有可直接批准的风险等级。',medium:'拦截中、高风险章节；低风险章节可批准。',high:'只拦截高风险章节；低、中风险章节可批准。',ignore:'所有风险等级均可人工批准正文，包括高风险和空正文；原风险、缺项及交付检查继续保留。'};
function riskBadge(level){return badge(level==='high'?'error':level==='medium'?'pending':'ready',riskLabels[level]||'风险待计算');}
function riskSettings(s){const policy=riskSteps.includes(s.section_approval_threshold)?s.section_approval_threshold:'medium';return `<fieldset class="risk-settings"><legend>总设置 · 章节正文批准</legend><label for="approval-risk-slider">从哪个风险等级起拦截批准</label><input id="approval-risk-slider" name="section_approval_threshold" type="range" min="0" max="3" step="1" value="${riskSteps.indexOf(policy)}" aria-valuetext="${riskLabels[policy]}" aria-describedby="approval-risk-help"><div class="risk-ticks" aria-hidden="true">${riskSteps.map(x=>`<span>${riskLabels[x]}</span>`).join('')}</div><p id="approval-risk-help" role="status"><strong>${riskLabels[policy]}</strong>：${riskRules[policy]}</p><small>保存设置后应用于本工作空间的后续单章和批量批准。不会自动批准或撤销已批准章节，也不会消除风险、完成附件签章或放开正式导出检查。</small></fieldset>`;}
function updateRiskSlider(){const el=$('#approval-risk-slider');if(!el)return;const policy=riskSteps[Number(el.value)];el.setAttribute('aria-valuetext',riskLabels[policy]);$('#approval-risk-help').textContent=riskLabels[policy]+'：'+riskRules[policy];}
function chapterRisk(id){return state.project?.section_risks?.find(r=>r.section_id===id);}
function renderSettings(){const s=state.settings||{};$('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">WORKSPACE SETTINGS</div><h1>模型与企业设置</h1><p>连接模型能力，配置投标文件中使用的企业主体。</p></div>${badge(s.key_configured?'approved':'pending',s.key_configured?'DeepSeek 密钥已配置':'等待配置 DeepSeek')}</div><div class="settings-layout"><section class="panel"><div class="panel-head"><div><h2>总设置与模型连接</h2><p>密钥保存在本机，保存后不会向浏览器返回明文</p></div>${icon('settings')}</div><div class="panel-body"><form id="settings-form"><div class="fields">${riskSettings(s)}<div class="field"><label for="company-name">企业名称 <span class="required">*</span></label><input id="company-name" name="company_name" required maxlength="200" autocomplete="organization" value="${esc(s.company_name)}" placeholder="输入实际投标企业的完整名称"><small>用于新项目默认主体；请以投标盖章主体的法定全称为准。</small></div><div class="field"><label for="base-url">API 服务地址</label><input id="base-url" name="base_url" type="url" required value="${esc(s.base_url||'https://api.deepseek.com')}" placeholder="https://api.deepseek.com" spellcheck="false" autocomplete="url"></div><div class="field"><label for="model-name">模型名称</label><div class="field-inline"><input id="model-name" name="model" required value="${esc(s.model||'')}" placeholder="输入服务端可用的模型 ID" list="model-options" spellcheck="false"><datalist id="model-options"></datalist>${btn('获取可用模型','load-models','','small')}</div><small>模型可用性由当前 API 账户决定；修改后保存并测试连接。</small></div><div class="field"><label for="api-key">API Key</label><input id="api-key" name="api_key" type="password" autocomplete="new-password" placeholder="${s.key_configured?'已有密钥，留空保留；输入新密钥可替换':'粘贴 DeepSeek API Key'}"><small>${s.key_configured?'已保存密钥不会在页面显示。':'尚未配置密钥。配置前可导入和查看资料，招标分析仅能创建本地候选台账。'}</small></div>${advancedSettings(s)}</div><div id="settings-error" class="form-error inline-error" role="alert"></div><div class="form-footer"><span class="small muted" id="settings-save-label">设置仅作用于本地工作空间</span><div class="actions">${btn(`${icon('checkcircle')}测试已保存连接`,'test-model')}<button type="submit" class="button primary">保存设置</button></div></div></form></div></section><aside class="settings-aside"><h3>模型如何参与投标</h3><p>招标文件和企业知识经本地解析后，相关文本会发送到你配置的 DeepSeek 接口，用于提取需求、生成响应和撰写章节。</p><dl><dt>01 / 解析招标</dt><dd>按文档分段提取要求，保留出处。</dd><dt>02 / 检索企业知识</dt><dd>为每项响应匹配已批准的企业资料。</dd><dt>03 / 生成与复核</dt><dd>草拟投标内容，标识缺项并等待确认。</dd></dl><div class="divider"></div><p>生成结果会保存在项目中。密钥失效或连接中断时，可以查看任务记录并重试。</p></aside></div>`;}
function progressValue(job){return Math.min(100,Math.max(0,Number(job.progress)||0));}
function jobTitle(job){if(job.mode==='generate'&&job.payload?.section_id)return '单章AI重新生成 · '+(job.payload.section_title||'当前章节');if(job.mode==='analyze'){if((job.result?.ai_completed===false&&!job.result?.batches)||/本地候选扫描|全文件本地规则扫描/.test(job.message||''))return '本地候选扫描';if(job.result?.ai_completed===false)return '招标分析（未完整）';}if(job.mode==='review'&&job.result?.ai_completed===false)return '本地规则检查（待 AI 复核）';return modeNames[job.mode]||'资料处理任务';}
function advancedSettings(s){return `<details class="advanced-settings"><summary>高级文档处理</summary><div class="fields" style="margin-top:17px"><label class="checkbox-line"><input type="checkbox" name="ocr" ${s.ocr?'checked':''}><span><strong>开启扫描页 OCR 识别</strong><br>对后续导入的 PDF 扫描页尝试识别文字，处理时间会增加。已有文件需要重新解析。</span></label><div class="two-col"><div class="field"><label for="batch-chars">每批分析文本字符数</label><input id="batch-chars" name="batch_chars" type="number" min="6000" max="32000" step="1" required value="${Number(s.batch_chars)||16000}"><small>范围 6,000–32,000；较小的批次更易重试。</small></div><div class="field"><label for="max-tokens">模型单次最大输出 Token</label><input id="max-tokens" name="max_tokens" type="number" min="1024" max="32768" step="1" required value="${Number(s.max_tokens)||8192}"><small>范围 1,024–32,768；应在模型支持的限制内。</small></div></div></div></details>`;}
function evidenceLinks(ids){return ids.map((id,i)=>btn(`${icon('link')}证据 ${i+1}`,'view-evidence',`data-id="${esc(id)}" title="${esc(id)}"`,'small')).join(' ');}
async function viewEvidence(id){const token=openDialog('企业证据','查看响应所引用的企业原始依据。','<div class="page-loading" style="padding:35px"><span class="spinner"></span>正在读取证据…</div>');if(token===null)return;try{const e=await api('/api/chunks/'+encodeURIComponent(id));if(!dialogIsCurrent(token))return;openDialog(e.document_name||'企业证据',e.locator||'企业资料文本块',`<div class="project-meta" style="margin-bottom:17px">${badge(documentStatus({...e,status:e.document_status}))}<span>${esc(scopeNames[e.scope]||e.scope||'')}</span>${e.valid_until?'<span>有效期至 '+esc(e.valid_until)+'</span>':''}</div><div class="document-detail">${esc(e.text)}</div><p class="small mono muted break" style="margin-top:12px">证据编号：${esc(e.id)}</p>`,`${btn('关闭','close-dialog')}${btn(`${icon('file')}查看完整资料`,'view-document',`data-id="${esc(e.document_id)}"`,'primary')}`);}catch(error){if(!dialogIsCurrent(token))return;$('#dialog-content .dialog-body').innerHTML=note(esc(error.message),'error');}}
function renderCheckDetails(check){const details=check.details;if(!details||!Object.keys(details).length)return '';if(typeof details==='string')return `<p>${esc(details)}</p>`;const links=[];for(const id of (details.requirement_ids||[]).slice(0,6)){const r=(state.project.requirements||[]).find(x=>x.id===id);links.push(btn(esc(r?.number||r?.title?.slice(0,24)||'查看响应'),'review-requirement',`data-id="${esc(id)}"`,'small'));}for(const id of (details.section_ids|| (details.section_id?[details.section_id]:[])).slice(0,6)){const s=(state.project.sections||[]).find(x=>x.id===id);links.push(btn(esc(s?.title||'查看章节'),'review-section',`data-id="${esc(id)}"`,'small'));}for(const doc of (details.documents||[]).slice(0,6)){if(typeof doc==='object'&&doc.id)links.push(btn(esc(doc.name||'查看文件'),'view-document',`data-id="${esc(doc.id)}"`,'small'));else if(typeof doc==='string')links.push(`<span class="small muted">${esc(doc)}</span>`);}if(details.items)for(const item of details.items.slice(0,6))links.push(btn(esc((item.title||'章节')+' · '+(item.value||'')),'review-section',`data-id="${esc(item.section_id)}"`,'small'));if(links.length)return `<div class="actions" style="margin-top:10px">${links.join('')}</div>${Math.max(details.requirement_ids?.length||0,details.section_ids?.length||0,details.items?.length||0)>6?'<p>展示前 6 项；进入对应页面处理全部问题。</p>':''}`;if(details.status)return `<p>分析状态：${esc(statusNames[details.status]||details.status)}</p>`;return `<p>${esc(jsonDetails(details))}</p>`;}
function activeJobs(jobs){
  const latest=new Map();
  const ordered=jobs.map(j=>state.jobs.get(j.id)||j).sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||'')));
  for(const job of ordered){const target=['analyze','generate','review'].includes(job.mode)?'project':job.mode==='reparse'?job.payload?.document_id:job.mode==='import'?job.payload?.path:null;const key=target?JSON.stringify([job.project_id,job.mode,target]):job.id;if(!latest.has(key))latest.set(key,job);}
  return [...latest.values()].filter(j=>['running','queued','failed','interrupted'].includes(j.status)).sort((a,b)=>Number(['running','queued'].includes(b.status))-Number(['running','queued'].includes(a.status))).slice(0,3);
}
function trackJobs(jobs){for(const j of jobs)if(j?.id)state.jobs.set(j.id,{...(state.jobs.get(j.id)||{}),...j});}
function renderJobs(jobs){return jobs.map(job=>{const j=state.jobs.get(job.id)||job;const logs=j.events||j.logs||[];return `<article class="job-card"><div class="job-head"><div class="job-title">${['running','queued'].includes(j.status)?'<span class="spinner"></span>':icon(j.status==='succeeded'?'checkcircle':'clock')}<strong>${esc(jobTitle(j))}</strong>${badge(j.status,j.status==='succeeded'&&jobTitle(j)==='本地候选扫描'?'扫描完成 · 待 AI 分析':undefined)}</div><div class="actions">${['queued','running'].includes(j.status)?btn(`${icon('stop')}取消`,'cancel-job',`data-id="${esc(j.id)}"`,'small ghost'):['failed','cancelled','interrupted'].includes(j.status)?btn(`${icon('refresh')}重试`,'retry-job',`data-id="${esc(j.id)}"`,'small'):''}</div></div><div class="job-message">${esc(j.message||'任务已创建')} <span class="muted">${Math.round(progressValue(j))}%</span></div><div class="progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(progressValue(j))}" aria-label="${esc(jobTitle(j))}进度"><div class="progress-bar" style="width:${progressValue(j)}%"></div></div>${j.error?`<div class="job-error">${esc(j.error)}</div>`:''}<details data-job-details="${esc(j.id)}"><summary>查看执行记录</summary><div class="job-log">${logs.length?logs.map(l=>`${l.created_at?time(l.created_at)+' ':''}${esc(l.message||l)}`).join('\n'):'正在加载执行记录…'}</div></details></article>`;}).join('');}
function projectMetrics(){const d=state.project||{},r=d.requirements||[],secs=d.sections||[];return {req:r.length,confirmed:r.filter(x=>['confirmed','not_applicable'].includes(x.status)).length,gaps:r.filter(x=>x.status==='gap').length,mandatory:r.filter(x=>x.mandatory).length,approved:secs.filter(x=>x.status==='approved').length,sections:secs.length,errors:(d.checks||[]).filter(x=>x.severity==='error').length};}
function renderProject(){const d=state.project,p=d.project,m=projectMetrics();$('#page-name').textContent=p.name;const analysis=p.analysis_status||'pending';const busyJob=d.review_rule_flags?.operation_conflict!==false&&(d.jobs||[]).some(j=>['queued','running'].includes((state.jobs.get(j.id)||j).status));
  $('#main').innerHTML=`<a class="project-back" href="#dashboard">${icon('back')}返回项目总览</a><div class="page-heading project-heading"><div><h1>${esc(p.name)}</h1><div class="project-meta">${badge(p.status)}${p.analysis_status==='local_candidates'?badge('pending','AI 尚未分析'):''}<span>${esc(domainNames[p.domain]||p.domain)}</span><span>投标主体：${esc(p.company_name||'未设置')}</span><span>创建于 ${date(p.created_at)}</span></div></div><div class="actions">${btn(`${icon('settings')}项目信息`,'edit-project','','')}${btn(`${icon('upload')}补充招标文件`,'import-tender','','')}${state.tab==='analyze'?btn(`${icon('spark')}开始解析`,'run',`data-mode="analyze" ${busyJob?'disabled':''}`,'primary'):['requirements','outline','sections'].includes(state.tab)?projectGenerationAction(busyJob):btn(`${icon('shield')}运行交付审核`,'run',`data-mode="review" ${busyJob?'disabled':''}`,'primary')}</div></div>
  <nav class="workflow workflow-five" aria-label="项目工作流程">${[['analyze','招标解析',`${d.documents?.length||0} 份招标文件`],['requirements','需求与响应',`${m.confirmed} / ${m.req} 项已确认`],['outline','调整编制目录',outlineProgressLabel(d.compilation_outline)],['sections','章节编辑',`${m.approved} / ${m.sections} 章已批准`],['review','审核与导出',`${m.errors} 项阻断问题`]].map(([key,title,sub],i)=>`<button class="${state.tab===key?'active':''}" data-action="project-tab" data-value="${key}" ${state.tab===key?'aria-current="step"':''}><span class="workflow-index">0${i+1}</span><span><strong>${title}</strong><small>${sub}</small></span></button>`).join('')}</nav>
  <div id="job-region">${renderJobs((d.jobs||[]).slice(0,3))}</div><div id="project-content">${state.tab==='analyze'?renderAnalysis():state.tab==='requirements'?renderRequirements():state.tab==='outline'?renderProjectOutline():state.tab==='sections'?renderSections():renderReview()}</div>`;
  if(state.tab==='sections')syncSectionSelection();
}
function renderAnalysis(){const d=state.project,p=d.project,m=projectMetrics();const local=p.analysis_status==='local_candidates';const complete=['complete','ai_complete','completed'].includes(p.analysis_status);const candidate=(d.requirements||[]).filter(r=>r.origin==='ai').length;return `<div class="workspace-grid"><div class="content-stack">${local?note('<strong>当前为本地候选台账，尚未完成 AI 招标分析。</strong><p>关键词提取不能覆盖几百页招标文件的完整要求。请先在模型设置中配置 DeepSeek，再重新执行解析。</p>','warning'):p.analysis_status==='partial'?note('招标分析未完整完成，请查看任务日志并重试。当前台账不能代表全部招标要求。','warning'):''}<section class="panel"><div class="panel-head"><div><h2>招标文件</h2><p>导入正文、技术规范、评分办法和补充通知，共同构成分析范围</p></div>${badge(complete?'approved':p.analysis_status,complete?'AI 分析已完成':undefined)}</div>${d.documents?.length?d.documents.map(doc=>`<div class="list-doc"><span class="doc-icon">${esc((doc.format||doc.name?.split('.').pop()||'FILE').toUpperCase().slice(0,5))}</span><div class="doc-info"><strong>${esc(doc.name)}</strong><small>${fmt(doc.page_count)} 页 / ${fmt(doc.text_chars)} 字符 · ${size(doc.size)}</small>${doc.warnings?.length?`<small class="warning-text">${esc(doc.warnings.join('；'))}</small>`:''}</div>${badge(doc.parse_status)}${btn(`${icon('eye')}查看`,'view-document',`data-id="${esc(doc.id)}"`,'small')}</div>`).join(''):empty('先导入本项目的招标文件','支持批量选择文件，或直接导入本机文件夹。扫描件、受密码保护文件及解析异常会在任务中提示。',btn(`${icon('upload')}导入招标文件`,'import-tender','','primary'))}<div class="panel-note">文件发生变化后，请重新解析和审核，确保需求与当前招标版本一致。</div></section><section class="panel"><div class="panel-head"><h2>招标准备进度</h2></div><div class="panel-body"><div class="guide-row"><span class="guide-number">01</span><div><h3>确认文件完整、文本可读</h3><p>核对招标正文、附件、评分表及补充说明；点击“查看”抽查解析文本。</p></div>${d.documents?.length?badge('ready','已导入'):badge('pending','待导入')}</div><div class="guide-row"><span class="guide-number">02</span><div><h3>提取招标要求与评分项</h3><p>分析全部可读内容，建立带有原文位置的需求台账。</p></div>${m.req?badge(complete?'ready':'pending',`${m.req} 项要求`):badge('pending','待解析')}</div><div class="guide-row"><span class="guide-number">03</span><div><h3>逐项生成响应、编辑投标章节</h3><p>匹配已批准企业资料，缺失的信息明确标记，留待人工确认。</p></div><a class="button small ghost" href="#project/${esc(p.id)}/requirements">查看台账 ${icon('arrow')}</a></div></div></section></div><aside class="workspace-aside">${renderProjectBasicsCard()}<div class="aside-card"><h3>本次分析范围</h3><div class="metric-line"><span>招标文件</span><strong>${fmt(d.documents?.length)} 份</strong></div><div class="metric-line"><span>解析页数</span><strong>${fmt((d.documents||[]).reduce((a,x)=>a+(x.page_count||0),0))} 页</strong></div><div class="metric-line"><span>提取要求</span><strong>${fmt(m.req)} 项</strong></div><div class="metric-line"><span>AI 提取要求</span><strong>${fmt(candidate)} 项</strong></div><div class="metric-line"><span>必须响应项</span><strong>${fmt(m.mandatory)} 项</strong></div><div class="divider"></div><p class="small muted">覆盖状态：${esc(complete?'AI 分析已完成':local?'AI 覆盖 0，当前仅为本地候选':statusNames[p.analysis_status]||'尚未分析')}</p></div><div class="aside-card"><h3>先准备企业依据</h3><p class="small muted" style="line-height:1.9">资质、产品能力、实施方案与案例经过审核后，将参与响应生成。</p><a href="#knowledge" class="button small wide" style="margin-top:16px">${icon('library')}前往企业知识库</a></div></aside></div>`;}
function requirementText(r){return r.title||r.text||'未命名要求';}
function renderRequirements(){const d=state.project;let reqs=d.requirements||[];const filter=state.requirementFilter;if(filter!=='all')reqs=reqs.filter(r=>filter==='mandatory'?!!r.mandatory:r.status===filter);if(!reqs.some(r=>r.id===state.selectedReq))state.selectedReq=reqs[0]?.id||null;const selected=reqs.find(r=>r.id===state.selectedReq);return `<div class="toolbar"><div><h2>需求与响应台账 <span class="muted small">${fmt(d.requirements?.length)} 项</span></h2><p class="small muted" style="margin-top:5px">核对招标原文，为每项要求确认响应与依据</p></div><div class="chips">${[['all','全部'],['mandatory','必须响应'],['gap','待补充'],['drafted','待确认'],['confirmed','已确认']].map(([key,label])=>`<button class="chip ${filter===key?'active':''}" data-action="requirement-filter" data-value="${key}">${label}</button>`).join('')}</div></div>${!d.requirements?.length?`<div class="panel">${empty('还没有需求台账','先导入招标文件并完成解析。招投标会提取技术、商务、资格及评分要求。',btn(`${icon('arrow')}返回招标解析`,'project-tab','data-value="analyze"','primary'))}</div>`:!reqs.length?`<div class="panel">${empty('此筛选下暂无需求','选择“全部”查看已提取的招标要求。',btn('查看全部','requirement-filter','data-value="all"'),true)}</div>`:`<div class="requirements-layout"><section class="panel"><div class="requirements-list" aria-label="招标要求列表">${reqs.map((r,i)=>`<button class="requirement-row ${r.id===state.selectedReq?'active':''}" data-action="select-requirement" data-id="${esc(r.id)}"><div class="requirement-top"><span class="requirement-num">${esc(r.number||String(i+1).padStart(3,'0'))} · ${esc(categoryNames[r.category]||r.category||'要求')}</span>${badge(r.status)}</div><p>${esc(requirementText(r))}</p><small>${r.mandatory?'必须响应 · ':''}${esc(r.locator||'待核对原文位置')}${r.origin==='local'?' · 本地候选':''}</small></button>`).join('')}</div></section><section class="panel editor-panel">${selected?renderRequirementEditor(selected):''}</section></div>`}`;}
function renderRequirementEditor(r){return `<div class="panel-head"><div><h2>${esc(r.number||'招标要求')} · ${esc(categoryNames[r.category]||r.category||'要求')}</h2><p>${r.mandatory?'必须响应': '一般响应项'}${r.score?' · 评分 '+esc(r.score):''}</p></div>${badge(r.status)}</div><div class="panel-body"><h3 style="line-height:1.8">${esc(requirementText(r))}</h3><div class="source-quote">${esc(r.quote||r.text)}</div><div class="source-label">${icon('link')}<span>${esc(r.locator||'原文位置待核对')}</span>${r.document_id?btn('查看招标原文','view-document',`data-id="${esc(r.document_id)}"`,'small ghost'):''}</div><div class="divider"></div><form id="requirement-form" data-id="${esc(r.id)}"><div class="fields"><div class="field"><label for="requirement-response">投标响应</label><textarea id="requirement-response" name="response" class="editor-area" placeholder="生成后在此复核，也可以直接填写响应。对不适用项请说明理由。">${esc(r.response)}</textarea></div><div class="two-col"><div class="field"><label for="requirement-status">响应状态</label><select id="requirement-status" name="status">${[['pending','待处理'],['gap','待补充资料'],['drafted','已草拟，待确认'],['confirmed','已人工确认'],['not_applicable','不适用（需说明理由）']].map(([key,label])=>`<option value="${key}" ${r.status===key?'selected':''}>${label}</option>`).join('')}</select></div><div class="field"><label for="evidence-ids">企业证据编号</label><input id="evidence-ids" name="evidence_ids" value="${esc((r.evidence_ids||[]).join(', '))}" placeholder="在下方检索后添加证据"><small>多个编号用逗号分隔；保存时校验证据有效性。</small></div></div></div><div id="requirement-error" class="form-error inline-error" role="alert"></div><div class="editor-actions"><span class="small muted" id="editor-save-state">修改后请保存</span><button type="submit" class="button primary">${icon('check')}保存响应</button></div></form><div class="divider"></div><div class="field"><label for="evidence-query">检索并添加企业证据</label><form id="evidence-search" class="field-inline"><input id="evidence-query" name="query" placeholder="如：电子档案四性检测" required>${btn(`${icon('search')}检索`,'evidence-search','','small')}</form><div id="evidence-results" class="small muted">仅显示已批准的可用资料；点击“添加依据”后保存响应。</div></div>${(r.evidence_ids||[]).length?`<details style="margin-top:14px"><summary class="small muted">已关联证据编号（${r.evidence_ids.length}）</summary><p class="small mono break" style="margin-top:8px">${evidenceLinks(r.evidence_ids)}</p></details>`:''}</div>`;}
// The backend owns grouping and canonical topic names; IDs always refer to leaf sections.
function outlineChineseNumber(number){
  const digits='零一二三四五六七八九';
  if(number<10)return digits[number];
  if(number<100)return (number<20?'':digits[Math.floor(number/10)])+'十'+(number%10?digits[number%10]:'');
  if(number<1000)return digits[Math.floor(number/100)]+'百'+(number%100===0?'':number%100<10?'零'+digits[number%100]:outlineChineseNumber(number%100));
  return String(number);
}
function sectionSuboutline(section,project=state.project){
  let spec=section.spec||{};if(typeof spec==='string'){try{spec=JSON.parse(spec);}catch{spec={};}}
  const reference=(project?.chapter_outline?.groups||[]).flatMap(g=>g.sections||[]).find(s=>s.id===section.id);
  return section.suboutline||spec.suboutline||reference?.children||[];
}
function renderSectionChildren(section,prefix){
  const children=sectionSuboutline(section);if(!children.length)return '';
  return `<ol class="chapter-suboutline" aria-label="${esc(section.title)}的三级四级目录">${children.map((child,i)=>`<li><span>${prefix}.${i+1} ${esc(child.title)}</span>${child.children?.length?`<ol>${child.children.map((leaf,j)=>`<li>${prefix}.${i+1}.${j+1} ${esc(leaf.title)}</li>`).join('')}</ol>`:''}</li>`).join('')}</ol>`;
}
function renderSectionSuboutline(section){
  const children=sectionSuboutline(section);if(!children.length)return '';
  return `<details class="section-suboutline-preview"><summary>本节下级目录（随本节一起编辑、生成和导出）</summary>${renderSectionChildren(section,'本节')}</details>`;
}
function renderRetainedSections(){
  const retained=state.project?.retained_sections||[];if(!retained.length)return '';
  return `<details class="retained-sections panel"><summary>未编入本次目录的章节 · ${retained.length} 节（原文和记录已保留）</summary><div class="panel-body"><p class="small muted">关闭模块只调整本次编制范围。原文、批准和历史记录仍保留；重新启用后可以继续编辑。</p>${retained.map(s=>`<details class="retained-section"><summary>${esc(s.title)} · ${esc(statusNames[s.status]||s.status||'草稿')}</summary><pre>${esc(s.content||'当前正文为空')}</pre></details>`).join('')}<div class="actions">${btn('前往调整编制目录重新启用','project-tab','data-value="outline"')}</div></div></details>`;
}
function sectionOutlineGroups(project=state.project){
  const sections=project?.sections||[],byId=new Map(sections.map(s=>[s.id,s])),seen=new Set(),groups=[];
  for(const group of project?.chapter_outline?.groups||[]){
    const leaves=[];
    for(const reference of group.sections||[]){const section=byId.get(reference.id);if(section&&!seen.has(section.id)){seen.add(section.id);leaves.push(section);}}
    if(leaves.length)groups.push({id:group.id,title:group.title,sections:leaves});
  }
  const remaining=sections.filter(s=>!seen.has(s.id));
  if(remaining.length)groups.push({id:'__unassigned_sections__',title:'章节目录',sections:remaining});
  return groups;
}
function sectionOutlineState(){
  state.sectionOutlineViews??={};
  return state.sectionOutlineViews[state.projectId]??={collapsed:new Set(),selected:null};
}
function syncSectionSelection(){
  const sections=state.project?.sections||[],ids=new Set(sections.map(s=>s.id));
  state.batchSelections??={};
  const selected=new Set((state.batchSelections[state.projectId]||[]).filter(id=>ids.has(id)));
  state.batchSelections[state.projectId]=[...selected];
  const sync=(input,leaves)=>{if(!input)return;const count=leaves.filter(s=>selected.has(s.id)).length;input.checked=leaves.length>0&&count===leaves.length;input.indeterminate=count>0&&count<leaves.length;};
  sync($('#batch-all'),sections);
  $$('.batch-section').forEach(el=>{el.checked=selected.has(el.dataset.id);});
  const groups=sectionOutlineGroups();
  $$('.batch-group').forEach(el=>sync(el,groups.find(g=>g.id===el.dataset.group)?.sections||[]));
  if($('#batch-count'))$('#batch-count').textContent=`已选 ${selected.size} 节`;
}
function updateSectionSelection(input){
  if(input.id!=='batch-all'&&!input.classList.contains('batch-section')&&!input.classList.contains('batch-group'))return;
  const sections=state.project?.sections||[],ids=new Set(sections.map(s=>s.id));
  state.batchSelections??={};
  const selected=new Set((state.batchSelections[state.projectId]||[]).filter(id=>ids.has(id)));
  const affected=input.id==='batch-all'?sections:input.classList.contains('batch-group')?(sectionOutlineGroups().find(g=>g.id===input.dataset.group)?.sections||[]):sections.filter(s=>s.id===input.dataset.id);
  affected.forEach(s=>input.checked?selected.add(s.id):selected.delete(s.id));
  state.batchSelections[state.projectId]=[...selected];
  syncSectionSelection();
}
function toggleChapterGroup(id){
  const group=sectionOutlineGroups().find(g=>g.id===id);if(!group)return;
  const view=sectionOutlineState(),collapsed=!view.collapsed.has(id);
  if(collapsed)view.collapsed.add(id);else view.collapsed.delete(id);
  const el=$$('.chapter-group').find(el=>el.dataset.outlineGroup===id);if(!el)return;
  $('.chapter-group-items',el).hidden=collapsed;
  $('.chapter-group-toggle',el).setAttribute('aria-expanded',String(!collapsed));
}
function selectSection(id){
  if(!state.project?.sections?.some(s=>s.id===id)||id===state.selectedSection||!allowLeave())return;
  const scroll=$('.chapter-nav')?.scrollTop||0;
  state.dirty=false;state.selectedSection=id;
  $('#project-content').innerHTML=renderSections();
  syncSectionSelection();
  $('.chapter-nav').scrollTop=scroll;
}
function sectionTitleLocked(sectionId){return (state.project?.compilation_outline?.groups||[]).some(g=>(g.children||[]).some(c=>c.section_id===sectionId&&c.title_locked));}
function renderSections(){
  state.batchSelections??={};state.batchSelections[state.projectId]??=[];
  const secs=state.project.sections||[],selected=new Set(state.batchSelections[state.projectId]);
  if(!secs.length)return `${renderRetainedSections()}<div class="panel">${empty('先准备投标书目录','在“调整编制目录”中启用所需模块、保存并规划下级目录。章节建立后，可以原样放入产品功能模块，或直接编辑正文。',btn('调整编制目录','project-tab','data-value="outline"','primary'))}</div>`;
  const groups=sectionOutlineGroups(),view=sectionOutlineState();
  if(!secs.some(s=>s.id===state.selectedSection))state.selectedSection=groups[0].sections[0].id;
  const s=secs.find(x=>x.id===state.selectedSection),currentGroup=groups.find(g=>g.sections.some(c=>c.id===s.id));
  if(view.selected!==s.id){view.collapsed.delete(currentGroup.id);view.selected=s.id;}
  return `${renderRetainedSections()}<div class="batch-tools panel"><label class="checkbox-line"><input type="checkbox" id="batch-all" ${secs.every(c=>selected.has(c.id))?'checked':''}><span>全选二级章节</span></label><span id="batch-count" class="small muted">已选 ${secs.filter(c=>selected.has(c.id)).length} 节</span>${btn('预览移出内部提示','batch-preview','data-value="separate_notes"')}${btn('预览清理固定提示','batch-preview','data-value="cleanup"')}${btn('批量批准可用章节','batch-preview','data-value="approve"','primary')}${btn('正文与交付待办','content-todos')}${btn('本项目可信资料','trusted-sources')}</div>
  <div class="chapter-layout"><aside class="panel"><div class="panel-head"><div><h2>投标书目录</h2><p>${groups.length} 个一级分类 · ${secs.length} 个二级主题</p></div></div><div class="chapter-nav" aria-label="投标书编制目录">${groups.map((group,groupIndex)=>`<section class="chapter-group" data-outline-group="${esc(group.id)}"><div class="chapter-group-head"><input type="checkbox" class="batch-group" data-group="${esc(group.id)}" aria-label="选择分类${esc(group.title)}全部${group.sections.length}节" ${group.sections.every(c=>selected.has(c.id))?'checked':''}><button class="chapter-group-toggle" data-action="toggle-chapter-group" data-value="${esc(group.id)}" aria-expanded="${!view.collapsed.has(group.id)}"><span class="chapter-group-chevron" aria-hidden="true">▾</span><span><strong>${state.project.compilation_outline?outlineChineseNumber(groupIndex+1)+'、':(groupIndex+1)+'.'} ${esc(group.title)}</strong><small>${group.sections.length} 节 · ${group.sections.filter(c=>c.status==='approved').length} 节已批准</small></span></button></div><div class="chapter-group-items" ${view.collapsed.has(group.id)?'hidden':''}>${group.sections.map((c,leafIndex)=>`<div class="chapter-selection"><input type="checkbox" class="batch-section" data-id="${esc(c.id)}" aria-label="选择${esc(c.title)}" ${selected.has(c.id)?'checked':''}><button class="chapter-item ${c.id===s.id?'active':''}" data-action="select-section" data-id="${esc(c.id)}" ${c.id===s.id?'aria-current="true"':''}><span class="chapter-num">${groupIndex+1}.${leafIndex+1}</span><span><strong>${esc(c.title)}</strong><small>${chapterRisk(c.id)?.risk_label||'风险待计算'} · ${esc(statusNames[c.status]||'草稿')} · ${fmt(c.content?.length)} 字符</small></span></button></div>${renderSectionChildren(c,`${groupIndex+1}.${leafIndex+1}`)}`).join('')}</div></section>`).join('')}</div></aside>
  <section class="panel"><div class="panel-head"><div><h2>二级主题编辑</h2><p>目录与 Word 导出共用；支持 Markdown 标题、列表与表格</p></div><div class="actions">${chapterRisk(s.id)?riskBadge(chapterRisk(s.id).risk_level):''}${badge(s.status)}</div></div><div class="panel-body"><p class="section-operation-scope">当前范围：<strong>${esc(currentGroup.title)} / ${esc(s.title)}</strong><small>${/评审.*索引/.test(currentGroup.title)?'保存和批准仅作用于索引本节；重新生成时可勾选明确预览的缺项补全范围。':'以下保存、AI 重新生成和批准仅作用于当前 1 节；同分类其他章节保持不变。'}</small></p>${renderProductSectionActions(s)}<div class="actions" style="margin-bottom:16px">${btn('查看本节生成前原文','section-generation-history',`data-id="${esc(s.id)}"`)}${/评审.*索引/.test(currentGroup.title)?btn('查看实际页数与缺项','index-pages-open'):''}</div>${chapterRisk(s.id)?`<p class="small muted">${esc(chapterRisk(s.id).approval_reason)} · 可在模型与企业设置中调整阈值</p>`:''}${renderSectionSuboutline(s)}<form id="section-form" data-id="${esc(s.id)}"><div class="fields"><div class="field"><label for="section-group">一级分类</label><input id="section-group" readonly value="${esc(currentGroup.title)}"><small>按项目共用目录归类；每个二级主题独立保存。</small></div><div class="field"><label for="section-title">二级主题</label><input id="section-title" name="title" required value="${esc(s.title)}" ${sectionTitleLocked(s.id)?'readonly aria-readonly="true"':''}>${sectionTitleLocked(s.id)?'<small>招标规定标题，保留原文；本节正文仍可正常编辑。</small>':''}</div><div class="field"><label for="section-content">本节正文</label><textarea id="section-content" name="content" class="chapter-editor" spellcheck="false">${esc(s.content)}</textarea></div></div><div id="section-error" class="form-error inline-error" role="alert"></div><div class="editor-actions"><span id="editor-save-state" class="small muted">${s.user_edited?'已保存并保护的章节内容':'生成内容待人工复核'}</span><div class="actions"><button type="submit" name="intent" value="draft" class="button">保存草稿</button><button type="submit" name="intent" value="approved" class="button primary">${icon('checkcircle')}保存并批准本节</button></div></div></form>${note('内容批准与附件、签章交付分别处理；批准不代表 AI 审计通过或可正式提交。再次修改正文后需要重新批准。')}${(s.evidence_ids||[]).length?`<details style="margin-top:15px"><summary class="small muted">查看本节关联证据（${s.evidence_ids.length}）</summary><p class="mono small break" style="margin-top:9px">${evidenceLinks(s.evidence_ids)}</p></details>`:''}</div></section></div>`;
}
function renderReview(){const d=state.project,m=projectMetrics(),checks=d.checks||[],exp=d.exports||[];return `<div class="workspace-grid"><div class="content-stack">${note('正式导出需要完整 AI 招标分析、逐项确认响应和章节批准，再运行独立 AI 交付复核。内容或企业依据修改后需重新审查。草稿可提前导出并保留待补充标识。',m.errors?'warning':'info')}${renderQuotationSummary()}<div class="actions">${state.project.project.metadata?.generation_profile==='technical_proposal'?btn('查看实际页数与缺项','index-pages-open'):''}${btn('正文与交付待办','content-todos')}${btn('本项目可信资料','trusted-sources')}</div><section class="panel"><div class="panel-head"><div><h2>交付审核</h2><p>最终人工编辑完成后运行：规则检查 + DeepSeek 独立证据复核</p></div>${btn(`${icon('refresh')}重新审核`,'run','data-mode="review"','small')}</div>${checks.length?`<div class="check-list">${checks.map(c=>`<div class="check-row ${c.severity}">${icon(c.details?.rule_enabled===false?'clock':c.severity==='error'||c.severity==='warning'?'alert':'checkcircle')}<div><h3>${esc(c.message||c.title||c.code)}</h3>${renderCheckDetails(c)}</div><span style="margin-left:auto">${c.details?.rule_enabled===false?badge('draft','已停用'):badge(c.severity==='error'?'error':c.severity==='warning'?'pending':'ready',c.severity==='error'?'阻断':c.severity==='warning'?'提醒':'检查项')}</span></div>`).join('')}</div>`:empty('运行一次交付审核','检查需求覆盖、缺项与批准状态，并通过 DeepSeek 复核事实证据、数字及条件。没有密钥时仅执行本地规则，正式导出仍受限。',btn(`${icon('shield')}开始审核`,'run','data-mode="review"','primary'),true)}</section><section class="panel"><div class="panel-head"><h2>导出投标书</h2></div><div class="panel-body"><label class="checkbox-line" style="margin-bottom:18px"><input type="checkbox" id="final-export"><span><strong>正式交付导出</strong> — 勾选后执行完整准入检查；未满足条件会返回具体原因。未勾选时导出草稿。</span></label>${[['zip','整套投标文件 ZIP','资格、商务技术、报价分册与内部审阅清单；报价须人工确认'],['docx','Word 文档','可编辑的投标书，适合定稿与排版'],['pdf','PDF 文档','固定版式，依赖本机 PDF 转换组件'],['md','Markdown 文本','便于检索、归档和后续修订']].map(([format,name,desc])=>`<div class="export-choice"><span class="doc-icon">${format.toUpperCase()}</span><div><h3>${name}</h3><p>${desc}</p></div>${btn(`${icon('download')}导出`,'export',`data-format="${format}"`,'small')}</div>`).join('')}<div id="export-result" class="inline-error" role="status"></div></div></section></div><aside class="workspace-aside"><div class="aside-card"><h3>交付准备情况</h3><div class="metric-line"><span>已确认需求</span><strong>${m.confirmed} / ${m.req}</strong></div><div class="progress-track"><div class="progress-bar" style="width:${m.req?Math.round(m.confirmed/m.req*100):0}%"></div></div><div class="metric-line"><span>已批准章节</span><strong>${m.approved} / ${m.sections}</strong></div><div class="metric-line"><span>待补充响应</span><strong>${m.gaps}</strong></div><div class="metric-line"><span>当前阻断问题</span><strong>${m.errors}</strong></div><div class="divider"></div><p class="small muted">导出时会重新校验项目的当前状态。</p></div><div class="aside-card"><h3>导出记录</h3>${exp.length?exp.map(x=>`<div class="history-row"><div><strong>${esc(x.format?.toUpperCase())} ${x.final?'正式版':'草稿'}</strong><small style="display:block">${date(x.created_at)} ${time(x.created_at)}</small></div>${safeLocalUrl(x.url)?`<a class="button small ghost" href="${esc(x.url)}" download aria-label="下载${esc(x.name||x.format)}">${icon('download')}</a>`:'<span class="small muted">文件暂不可用</span>'}</div>`).join(''):'<p class="small muted">还没有导出记录</p>'}</div>${renderSnapshots(d.snapshots||[])}</aside></div>`;}
const projectFieldNames={project_number:'项目编号',buyer:'采购人',deadline:'投标截止时间'};
function renderProjectBasicsCard(){const p=state.project.project,meta=p.metadata||{};return `<div class="aside-card"><h3>项目基本信息</h3>${Object.entries(projectFieldNames).map(([key,label])=>`<div class="project-basic-item"><span>${label}</span><strong>${esc(p[key]||'待核对填写')}</strong>${meta.field_conflicts?.[key]?.length&&!meta.field_overrides?.[key]?badge('error','原文存在多个值'):meta.field_overrides?.[key]?badge('confirmed','人工确认'):p[key]?badge('pending','从原文提取，待核对'):''}</div>`).join('')}${btn('核对与编辑','edit-project','','small wide')}</div>`;}
function renderFieldSources(key,meta){const sources=meta.field_sources?.[key]||[],conflicts=meta.field_conflicts?.[key]||[];return `${conflicts.length?note(`原文出现 ${conflicts.length} 个不同值，请核对正文与补充通知后确认采用值。${meta.field_overrides?.[key]?' 当前已有人工作出的选择。':''}`,'warning'):''}${sources.length?`<details class="field-sources"><summary>查看 ${sources.length} 处原文依据${meta.field_overrides?.[key]?' · 当前值已人工确认':''}</summary>${sources.map(s=>`<div class="source-card"><strong>${esc(s.value)}</strong><p>${esc(s.quote)}</p><p>${esc(s.document_name)} · ${esc(s.locator)}</p>${btn('采用此值','apply-project-source',`data-field="${key}" data-value="${esc(s.value)}"`,'small')}</div>`).join('')}</details>`:'<small>尚未提取到可靠的原文值，请对照招标文件填写。</small>'}`;}
function openProjectBasics(){const p=state.project.project,meta=p.metadata||{};openDialog('项目基本信息','核对投标主体、项目编号及采购信息；保存后需重新进行交付审核。',`<form id="project-basics-form"><div class="fields"><div class="field"><label for="basics-name">项目名称</label><input id="basics-name" name="name" required maxlength="160" value="${esc(p.name)}"></div><div class="two-col"><div class="field"><label for="basics-domain">项目类型</label><select id="basics-domain" name="domain">${Object.entries(domainNames).map(([key,label])=>`<option value="${key}" ${p.domain===key?'selected':''}>${label}</option>`).join('')}</select></div><div class="field"><label for="basics-company">投标企业法定全称</label><input id="basics-company" name="company_name" required maxlength="160" value="${esc(p.company_name)}"></div></div>${Object.entries(projectFieldNames).map(([key,label])=>`<div class="field"><label for="basics-${key}">${label}</label><input id="basics-${key}" name="${key}" maxlength="${key==='buyer'?200:key==='deadline'?100:160}" value="${esc(p[key])}" placeholder="${key==='deadline'?'如：2026年10月20日 09:30（北京时间）':'按招标原文填写'}">${renderFieldSources(key,meta)}</div>`).join('')}</div><div id="project-basics-error" class="form-error inline-error" role="alert"></div></form>`,`${btn('取消','close-dialog')}<button type="submit" class="button primary" form="project-basics-form">保存并确认项目信息</button>`,true);}
function renderQuotationSummary(){const q=state.project.project.quotation||{};return `<section class="panel"><div class="panel-head"><div><h2>本项目报价</h2><p>人工录入实际投标价格，作为报价响应和报价分册的依据</p></div>${btn(`${icon('edit')}录入与核对报价`,'edit-quotation','','small')}</div><div class="panel-body"><div class="quotation-summary"><div><span class="small muted">含税总价（人民币）</span><strong>${q.total_including_tax!==undefined&&q.total_including_tax!==''?'¥ '+esc(q.total_including_tax):'待录入'}</strong></div>${badge(q.confirmed?'confirmed':'pending',q.confirmed?'已人工确认':'尚未确认')}</div><p class="small muted">${q.items?.length?`${q.items.length} 项报价明细。`:''}修改报价后，相关响应和章节需要重新确认与审核。</p>${q.issues?.length?note(esc(q.issues.join('；')),'warning'):''}</div></section>`;}
function blankQuotationLine(){return {name:'',quantity:'',unit_price:'',subtotal:'',note:''};}
function openQuotation(){const q=state.project.project.quotation||{};state.quotationDraft={confirmed:!!q.confirmed,total_including_tax:String(q.total_including_tax??''),items:q.items?.length?q.items.map(x=>({...x})): [blankQuotationLine()]};openDialog('本项目报价','单位为人民币元，单价与小计均为含税金额。',`${note('请根据企业授权的实际报价填写。保存草稿可暂留缺项；页面提供金额参考检查，保存时按已保存的审核规则判断。')}<form id="quotation-form"><div class="field"><label for="quote-total">含税总价（人民币元）</label><input id="quote-total" name="total_including_tax" inputmode="decimal" maxlength="30" value="${esc(state.quotationDraft.total_including_tax)}" placeholder="如：250000.00"><small>不填千分位；最多两位小数。只报总价时可以不填写明细。</small></div><div class="section-heading quote-heading"><h3>报价明细（可选）</h3><div class="actions">${btn('添加明细','add-quote-row','','small')}${btn('计算小计与合计','calculate-quotation','','small')}</div></div><div id="quotation-rows"></div><p class="small muted">金额参考检查（是否拦截按已保存审核设置执行）</p><div id="quotation-validation" class="small inline-error" role="status" aria-live="polite"></div><label class="checkbox-line"><input type="checkbox" id="quote-confirmed" name="confirmed" ${state.quotationDraft.confirmed?'checked':''}><span>我已核对实际投标报价、含税金额和明细，并确认将其用于本项目投标文件。</span></label><div id="quotation-error" class="form-error inline-error" role="alert"></div></form>`,`${btn('取消','close-dialog')}<button type="submit" class="button primary" form="quotation-form">保存报价</button>`,true);renderQuotationRows();recalculateQuotation();}
function renderQuotationRows(){const fields=[['name','项目名称'],['quantity','数量'],['unit_price','含税单价'],['subtotal','含税小计'],['note','备注']];$('#quotation-rows').innerHTML=state.quotationDraft.items.map((row,i)=>`<div class="quote-row" data-index="${i}"><div class="quote-row-title"><strong>明细 ${i+1}</strong>${btn('删除','remove-quote-row',`data-index="${i}"`,'small ghost')}</div><div class="quote-row-fields">${fields.map(([key,label])=>`<div class="field quote-${key}"><label for="quote-${key}-${i}">${label}</label><input id="quote-${key}-${i}" data-quote-field="${key}" ${['quantity','unit_price','subtotal'].includes(key)?'inputmode="decimal" maxlength="30"':`maxlength="${key==='name'?300:1000}"`} value="${esc(row[key])}"></div>`).join('')}</div></div>`).join('');}
function captureQuotationDraft(){const f=$('#quotation-form');if(!f)return;state.quotationDraft={confirmed:f.elements.confirmed.checked,total_including_tax:f.elements.total_including_tax.value.trim(),items:$$('.quote-row',f).map(row=>Object.fromEntries($$('[data-quote-field]',row).map(x=>[x.dataset.quoteField,x.value.trim()]))) };}
function markQuotationChanged(){markFormEdited($('#quotation-form'));state.dialogDirty=true;const checkbox=$('#quote-confirmed');if(checkbox)checkbox.checked=false;}
function decimalUnits(raw,label,precision=2){const value=String(raw??'').trim();if(!value)return null;if(!new RegExp('^\\d+(?:\\.\\d{1,'+precision+'})?$').test(value))throw new Error(`${label}须为非负数字，最多 ${precision} 位小数，不支持千分位或科学计数法。`);const [whole,fraction='']=value.split('.');const units=BigInt(whole)*10n**BigInt(precision)+BigInt(fraction.padEnd(precision,'0'));if(units>1000000000000n*10n**BigInt(precision))throw new Error(`${label}超过可接受范围。`);return units;}
function centsText(cents){return `${cents/100n}.${(cents%100n).toString().padStart(2,'0')}`;}
function inspectQuotation(q){const issues=[],total=decimalUnits(q.total_including_tax,'含税总价'),items=q.items.filter(row=>Object.values(row).some(x=>String(x??'').trim()));if(total===null||total<=0n)issues.push('含税总价必须大于 0 元');let sum=0n,complete=true;items.forEach((row,index)=>{const n=index+1,quantity=decimalUnits(row.quantity,`第 ${n} 项数量`,6),price=decimalUnits(row.unit_price,`第 ${n} 项含税单价`),subtotal=decimalUnits(row.subtotal,`第 ${n} 项含税小计`);if(!row.name.trim())issues.push(`第 ${n} 项名称为空`);if(quantity===null||quantity<=0n||price===null||subtotal===null)issues.push(`第 ${n} 项数量须大于 0，单价和小计必须填写`);else if((quantity*price+500000n)/1000000n!==subtotal)issues.push(`第 ${n} 项小计与数量 × 含税单价不一致（四舍五入到分）`);if(subtotal===null)complete=false;else sum+=subtotal;});if(items.length&&total!==null&&(!complete||sum!==total))issues.push('明细小计之和与含税总价不一致');return {issues,items,sum,complete};}
function recalculateQuotation(){captureQuotationDraft();try{const result=inspectQuotation(state.quotationDraft);$('#quotation-validation').textContent=result.issues.length?'待核对：'+result.issues.join('；'):result.items.length?`金额校验通过，明细合计 ¥ ${centsText(result.sum)}。`:'总价格式校验通过；尚未填写明细。';$('#quotation-validation').classList.toggle('warning-text',!!result.issues.length);}catch(error){$('#quotation-validation').textContent=error.message;$('#quotation-validation').classList.add('warning-text');}}
function calculateQuotation(){captureQuotationDraft();const q=structuredClone(state.quotationDraft);let sum=0n,count=0;for(const [i,row] of q.items.entries()){if(!Object.values(row).some(x=>String(x??'').trim()))continue;const quantity=decimalUnits(row.quantity,`第 ${i+1} 项数量`,6),price=decimalUnits(row.unit_price,`第 ${i+1} 项含税单价`);if(quantity===null||quantity<=0n||price===null)throw new Error(`请先填写第 ${i+1} 项数量和含税单价。`);const subtotal=(quantity*price+500000n)/1000000n;row.subtotal=centsText(subtotal);sum+=subtotal;count++;}if(!count)throw new Error('请先录入至少一项报价明细，再计算合计。');q.total_including_tax=centsText(sum);state.quotationDraft=q;renderQuotationRows();$('#quote-total').value=q.total_including_tax;markQuotationChanged();recalculateQuotation();$('#quotation-error').textContent='';}
function buildQuotationPayload(){captureQuotationDraft();const q=state.quotationDraft;return {confirmed:q.confirmed,total_including_tax:q.total_including_tax,items:q.items.filter(row=>Object.values(row).some(x=>String(x??'').trim()))};}
function allowCloseDialog(){if(state.productModuleBusy){toast('正在保存本次模块操作，请稍候。');return false;}if(!state.dialogDirty)return true;if(!window.confirm('此窗口有尚未保存的修改。关闭将丢失这些修改，是否继续？'))return false;state.dialogDirty=false;return true;}
function openDialog(title,subtitle,body,footer='',wide=false){const d=$('#dialog');if(d.open&&!allowCloseDialog())return null;const token=++state.dialogToken;state.dialogDirty=false;d.classList.toggle('wide',wide);$('#dialog-content').innerHTML=`<div class="dialog-head"><div><h2 id="dialog-title">${esc(title)}</h2>${subtitle?`<p>${esc(subtitle)}</p>`:''}</div><button type="button" class="icon-button" data-action="close-dialog" aria-label="关闭对话框">${icon('close')}</button></div><div class="dialog-body">${body}</div>${footer?`<div class="dialog-footer">${footer}</div>`:''}`;if(!d.open)d.showModal();return token;}
function closeDialog(){if($('#dialog').querySelector('[data-uploading="true"]')){toast('文件正在上传，请等待上传完成后关闭。');return;}if(!allowCloseDialog())return;++state.dialogToken;$('#dialog').close();state.files=[];}
async function newProject(){const token=++state.dialogToken,routeToken=state.routeToken;let s=state.settings||state.dashboard?.settings;if(!s){try{s=await api('/api/settings');}catch{s={};}}if(token!==state.dialogToken||routeToken!==state.routeToken)return;openDialog('新建投标项目','先建立项目，再导入招标正文、附件和补充文件。',`<form id="new-project-form"><div class="fields"><div class="field"><label for="project-name">项目名称 <span class="required">*</span></label><input id="project-name" name="name" autofocus required maxlength="160" placeholder="如：某集团会计电子档案系统建设项目"></div><div class="field"><label for="project-domain">项目类型</label><select id="project-domain" name="domain"><option value="archive">会计电子档案系统</option><option value="expense">费控系统</option></select></div><div class="field"><label for="project-company">投标企业主体 <span class="required">*</span></label><input id="project-company" name="company_name" required maxlength="160" value="${esc(s.company_name||'')}" placeholder="请输入企业法定全称"></div></div><div id="new-project-error" class="form-error inline-error" role="alert"></div></form>`,`${btn('取消','close-dialog')}<button class="button primary" type="submit" form="new-project-form">创建项目 ${icon('arrow')}</button>`);}
async function importDialog(kind){const token=++state.dialogToken,routeToken=state.routeToken;if(!state.settings){try{state.settings=await api('/api/settings');}catch{state.settings={};}}if(token!==state.dialogToken||routeToken!==state.routeToken)return;state.importKind=kind;state.importMode='upload';state.files=[];renderImportDialog();}
function renderImportDialog(){const knowledge=state.importKind==='knowledge';const defaultPath=knowledge?(state.settings?.knowledge_path||''):(state.settings?.tender_path||'');openDialog(knowledge?'导入企业资料':'导入招标文件',knowledge?'导入后先核对资料内容、用途和有效性，再批准参与生成。':'支持多份文件；一个项目的招标正文、附件和补充通知可以一起导入。',`<div class="dialog-tabs"><button data-action="import-mode" data-value="upload" class="${state.importMode==='upload'?'active':''}">选择文件</button><button data-action="import-mode" data-value="path" class="${state.importMode==='path'?'active':''}">本地路径导入</button></div><form id="import-form">${state.importMode==='upload'?`<label class="dropzone" id="dropzone">${icon('upload')}<strong>点击选择文件，或将文件拖到这里</strong><small>PDF、DOCX、Markdown、TXT、XLSX、PPTX、CSV、HTML 与常见图片</small><input id="file-input" type="file" multiple accept=".pdf,.docx,.md,.markdown,.txt,.csv,.xlsx,.pptx,.html,.htm,.png,.jpg,.jpeg,.tif,.tiff,.webp,.bmp"></label><div id="selected-files" class="selected-files">${renderSelectedFiles()}</div>`:`<div class="field"><label for="import-path">文件或文件夹的绝对路径</label><input id="import-path" name="path" required value="${esc(defaultPath)}" placeholder="C:\\资料\\文件夹"><small>文件夹将递归扫描受支持的文件。后台逐份导入，进度和失败原因保存在执行记录中。</small></div>`}<div id="import-error" class="form-error inline-error" role="alert"></div><div id="import-progress" class="import-progress" aria-live="polite"></div></form>`,`${btn('取消','close-dialog')}<button class="button primary" type="submit" form="import-form" id="import-submit">${icon('upload')}开始导入</button>`);}
function renderSelectedFiles(){return state.files.map(f=>`<div class="selected-file"><span>${esc(f.name)}</span><span class="nowrap">${size(f.size)}</span></div>`).join('');}
function warningAckForm(d){return `<form id="warning-ack-form" data-id="${esc(d.id)}" style="margin-bottom:20px"><label class="checkbox-line"><input type="checkbox" name="warnings_acknowledged" ${d.metadata?.warnings_acknowledged?'checked':''}><span>我已对照原始文件核对上述解析提示，并确认参与本次投标的内容完整、可用。</span></label><div class="actions" style="margin-top:10px"><button type="submit" class="button small">保存核对状态</button>${d.metadata?.warnings_acknowledged?badge('approved','已人工核对'):badge('pending','尚未核对')}</div><div id="warning-ack-error" class="form-error inline-error" role="alert"></div></form>`;}
function confirmReparse(id,kind){openDialog('重新解析原始文档','按当前模型与企业设置中的 OCR 选项重新提取文本。',`${note(kind==='tender'?'成功后将重置本项目的需求台账和投标章节，再进行招标分析。系统会先保存历史快照与旧版 Markdown 草稿，可在“审核与导出”中查看。':'成功后企业资料将回到待审核状态，相关响应和章节需要重新核对。新文本经批准后才能用于生成。','warning')}<p class="small muted">原始文件副本保持保留；如果重新解析没有得到有效文本，当前内容会保留。</p><div id="reparse-error" class="form-error inline-error" role="alert"></div>`,`${btn('暂不处理','close-dialog')}${btn(`${icon('refresh')}开始重新解析`,'reparse-document',`data-id="${esc(id)}"`,'primary')}`);}
async function reparseDocument(id,button){const token=state.dialogToken,routeToken=state.routeToken;await busy(button,async()=>{try{const result=await api('/api/documents/'+encodeURIComponent(id)+'/reparse',{method:'POST'});trackJobs([result.job||result]);if(dialogIsCurrent(token))closeDialog();toast('重新解析任务已提交，可在执行记录中跟踪。');if(state.routeToken===routeToken&&!$('#dialog').open){if(state.route==='project')await refreshProject(!state.dirty);else if(!state.dirty)await navigate();}}catch(error){if(dialogIsCurrent(token)&&$('#reparse-error'))$('#reparse-error').textContent=error.message;else toast(error.message,true);}});}
function renderSnapshots(items){if(!items.length)return '';return `<div class="aside-card"><h3>历史快照 <span class="muted small">${items.length}</span></h3><p class="small muted">重解析与恢复操作前会保留版本，旧草稿可在导出记录下载。</p>${items.map(s=>`<div class="history-row"><div><strong>${esc(s.label)}</strong><small style="display:block">${date(s.created_at)} ${time(s.created_at)}</small></div>${btn(s.label.startsWith('正文批量操作：')?'撤销本轮':'恢复',s.label.startsWith('正文批量操作：')?'content-undo':'confirm-restore',`data-id="${esc(s.id)}"`,'small')}</div>`).join('')}</div>`;}
function confirmRestore(id){openDialog('恢复项目历史内容','恢复前，系统会再保存当前版本的快照。',`${note('将恢复该版本的需求台账、章节和对应文档的解析内容。若项目文件集合已经变化，系统会阻止直接恢复；你仍可通过导出记录下载旧稿。','warning')}<div id="restore-error" class="form-error inline-error" role="alert"></div>`,`${btn('取消','close-dialog')}${btn('恢复此版本','restore-snapshot',`data-id="${esc(id)}"`,'primary')}`);}
async function restoreSnapshot(id,button){const token=state.dialogToken,routeToken=state.routeToken;await busy(button,async()=>{try{const result=await api('/api/snapshots/'+encodeURIComponent(id)+'/restore',{method:'POST'});if(dialogIsCurrent(token))closeDialog();if(state.routeToken===routeToken)await refreshProject(!state.dirty);toast(result.message||'历史版本已恢复。');}catch(error){if(dialogIsCurrent(token)&&$('#restore-error'))$('#restore-error').textContent=error.message;else toast(error.message,true);}});}
async function viewDocument(id){const token=openDialog('文档内容与来源','核对原文解析、资料范围及适用状态。','<div class="page-loading" style="padding:40px"><span class="spinner"></span>正在读取文档…</div>','',true);if(token===null)return;try{const data=await api('/api/documents/'+encodeURIComponent(id));if(!dialogIsCurrent(token))return;const d=data.document||data;const blocks=data.blocks||data.chunks||[];const knowledge=!d.project_id;openDialog(d.name,'保留来源位置，便于核对生成依据。',`${d.warnings?.length?note(esc(d.warnings.join('；')),'warning'):''}${d.warnings?.length?warningAckForm(d):''}<div class="project-meta" style="margin-bottom:18px">${badge(d.parse_status)}${knowledge?badge(documentStatus(d)):''}<span>${fmt(d.page_count)} 页 / ${fmt(d.text_chars)} 字符</span><span>${size(d.size)}</span></div><p class="small muted break" style="margin-bottom:17px">来源：${esc(d.source_path||d.name)}${d.valid_until?' · 有效期至 '+esc(d.valid_until):''}</p>${knowledge?`<form id="document-form" data-id="${esc(d.id)}"><div class="document-controls"><div class="field"><label for="doc-status">使用状态</label><select id="doc-status" name="status">${[['pending','待审核'],['approved','批准使用'],['expired','停用 / 已失效']].map(([k,v])=>`<option value="${k}" ${d.status===k?'selected':''}>${v}</option>`).join('')}</select></div><div class="field"><label for="doc-scope">资料用途</label><select id="doc-scope" name="scope">${Object.entries(scopeNames).map(([k,v])=>`<option value="${k}" ${d.scope===k?'selected':''}>${v}</option>`).join('')}</select></div><div class="field"><label for="doc-until">有效期截止日（可选）</label><input type="date" id="doc-until" name="valid_until" value="${esc(d.valid_until||'')}"></div><div class="field" style="justify-content:flex-end"><button type="submit" class="button primary">保存审核结果</button></div></div><div id="document-error" class="form-error inline-error" role="alert"></div></form>`:''}<div class="document-detail">${blocks.length?blocks.map(b=>`<div style="margin-bottom:17px"><span class="source-label">${esc(b.locator|| (b.page?'第 '+b.page+' 页':''))}</span><div>${esc(b.text)}</div></div>`).join(''):esc(data.text||'没有可读取的解析文本。请检查文档警告和解析状态。')}</div>`,`${btn('关闭','close-dialog')}${btn(`${icon('refresh')}重新解析`,'confirm-reparse',`data-id="${esc(d.id)}" data-kind="${d.project_id?'tender':'knowledge'}"`)}<a class="button" href="/api/documents/${encodeURIComponent(d.id)}/download" download>${icon('download')}下载原始文件</a>`,true);}catch(error){if(!dialogIsCurrent(token))return;$('#dialog-content .dialog-body').innerHTML=note(esc(error.message),'error');}}
async function refreshProject(render=true){const id=state.projectId,token=state.routeToken,revision=state.projectRefresh=(state.projectRefresh||0)+1;if(!id)return;const data=await api('/api/projects/'+encodeURIComponent(id));if(state.projectId!==id||state.route!=='project'||token!==state.routeToken||revision!==state.projectRefresh)return;state.project=data;trackJobs(data.jobs||[]);if(render&&!state.dirty){if(state.tab==='outline')setOutlineDraft(data.compilation_outline||await api('/api/projects/'+encodeURIComponent(id)+'/outline-plan'));if(token!==state.routeToken)return;renderProject();}}
async function runProject(mode,button){if(mode==='generate'&&outlineNeedsPlanning(state.project?.compilation_outline)){if(allowLeave())location.hash=`project/${state.projectId}/outline`;toast('请先保存一、二级目录，并完成新增模块的下级目录规划。');return;}if(!allowLeave())return;const id=state.projectId,token=state.routeToken;await busy(button,async()=>{try{const result=await api(`/api/projects/${encodeURIComponent(id)}/run`,{method:'POST',body:{mode}});const job=result.job||result;trackJobs([job]);if(state.routeToken===token)await refreshProject(!state.dirty);toast(`${modeNames[mode]}任务已提交，可在执行记录中查看进度。`);}catch(error){toast(error.message,true);}});}
async function exportProject(format,button){
  if(!allowLeave())return;
  const projectId=state.projectId,resultArea=$('#export-result');
  const final=!!$('#final-export')?.checked;
  await busy(button,async()=>{try{
    const result=await api(`/api/projects/${encodeURIComponent(projectId)}/export`,{method:'POST',body:{format,final}});
    const url=safeLocalUrl(result.url);if(!url)throw new Error('服务已响应，但未提供有效的本地下载地址。请检查导出记录。');
    if(resultArea?.isConnected)resultArea.innerHTML=note(`文件已生成：<a href="${esc(url)}" download>${esc(result.name||format.toUpperCase()+' 投标书')} ${icon('download')}</a>${result.warnings?.length?'<p>'+esc(result.warnings.join('；'))+'</p>':''}`,result.warnings?.length?'warning':'info');
    const a=document.createElement('a');a.href=url;a.download='';document.body.append(a);a.click();a.remove();toast('文件已生成，已开始下载。');
    if(state.projectId===projectId)await refreshProject(false);
  }catch(error){if(resultArea?.isConnected)resultArea.innerHTML=note(esc(error.message),'error');toast('导出未完成：'+error.message,true);}});
}
async function jobAction(action,id,button){await busy(button,async()=>{try{const data=await api(`/api/jobs/${encodeURIComponent(id)}/${action}`,{method:'POST'});trackJobs([data.job||data]);if(state.route==='project')await refreshProject(!state.dirty);else if(!state.dirty&&!$('#dialog').open)await navigate();toast(action==='cancel'?'已请求取消任务；当前处理步骤结束后停止。':'已提交重试任务。');}catch(error){toast(error.message,true);}});}
async function searchEvidence(button){const form=$('#evidence-search'),requirementForm=$('#requirement-form'),results=$('#evidence-results');if(!form||!form.reportValidity())return;await busy(button,async()=>{try{const k=await api('/api/knowledge?q='+encodeURIComponent(form.elements.query.value.trim())+'&domain='+encodeURIComponent(state.project.project.domain)+'&project_id='+encodeURIComponent(state.projectId));if(!form.isConnected||requirementForm!==$('#requirement-form'))return;results.innerHTML=k.results?.length?k.results.slice(0,10).map(r=>`<div class="source-card"><strong>${esc(r.document_name||'企业资料')}</strong><p>${esc(r.text)}</p><div class="actions" style="margin-top:7px"><small class="muted">${esc(r.locator)}</small>${btn('添加依据','add-evidence',`data-id="${esc(r.id)}"`,'small soft')}${btn('查看来源','view-document',`data-id="${esc(r.document_id)}"`,'small ghost')}</div></div>`).join(''):'没有找到匹配的已批准证据。';}catch(error){if(form.isConnected&&requirementForm===$('#requirement-form'))results.textContent=error.message;}});}
document.addEventListener('click',async event=>{
  const link=event.target.closest('a[href^="#"]');if(link&&link.getAttribute('href')!=='#main'&&state.dirty&&!allowLeave()){event.preventDefault();return;}
  const button=event.target.closest('[data-action]');if(!button||button.disabled)return;const {action,id,value}=button.dataset;if(button.closest('#dialog')&&$('#dialog [data-uploading="true"]')){toast('文件正在上传，请等待完成。');return;}
  try{
    if(action==='refresh'){if(allowLeave())await navigate(true);}
    else if(action.startsWith('product-'))await handleProductModuleAction(action,button);
    else if(action.startsWith('outline-'))await handleOutlineAction(action,button);
    else if(action==='new-project')await newProject();
    else if(action==='edit-project'){if(allowLeave())openProjectBasics();}
    else if(action==='edit-quotation'){if(allowLeave())openQuotation();}
    else if(action==='apply-project-source'){markFormEdited($('#project-basics-form'));const input=$(`#project-basics-form [name="${button.dataset.field}"]`);if(input){input.value=button.dataset.value;state.dialogDirty=true;}}
    else if(action==='add-quote-row'){captureQuotationDraft();state.quotationDraft.items.push(blankQuotationLine());renderQuotationRows();markQuotationChanged();recalculateQuotation();}
    else if(action==='remove-quote-row'){captureQuotationDraft();state.quotationDraft.items.splice(Number(button.dataset.index),1);if(!state.quotationDraft.items.length)state.quotationDraft.items.push(blankQuotationLine());renderQuotationRows();markQuotationChanged();recalculateQuotation();}
    else if(action==='calculate-quotation'){try{calculateQuotation();}catch(error){$('#quotation-error').textContent=error.message;}}
    else if(action==='close-dialog')closeDialog();
    else if(action==='open-project'){if(allowLeave())location.hash=`project/${id}/analyze`;}
    else if(action==='project-filter'){state.projectFilter=value;renderDashboard();}
    else if(action==='knowledge-filter'){if(state.knowledgeStatus!==value){state.knowledgeStatus=value;state.knowledgePage=1;clearKnowledgeSelection(true);}renderKnowledge();}
    else if(action==='knowledge-select-all'){state.knowledgeSelected=new Set(filteredKnowledge().map(d=>d.id));state.knowledgeSelectionAll=true;renderKnowledge();}
    else if(action==='knowledge-clear-selection'){clearKnowledgeSelection();renderKnowledge();}
    else if(action==='knowledge-page'){state.knowledgePage=Number(value);renderKnowledge();}
    else if(action==='knowledge-bulk-preview')await previewKnowledgeApproval(button);
    else if(action==='knowledge-bulk-apply')await executeKnowledgeApproval(button);
    else if(action==='knowledge-undo-preview')confirmKnowledgeUndo(id);
    else if(action==='knowledge-undo')await undoKnowledgeApproval(id,button);
    else if(action==='clear-search'){state.query='';clearKnowledgeSelection(true);await navigate();}
    else if(action==='import-knowledge')await importDialog('knowledge');
    else if(action==='import-tender')await importDialog('tender');
    else if(action==='import-mode'){state.importMode=value;renderImportDialog();}
    else if(action==='view-document')await viewDocument(id);
    else if(action==='view-evidence')await viewEvidence(id);
    else if(action==='confirm-reparse'){if(allowLeave())confirmReparse(id,button.dataset.kind);}
    else if(action==='reparse-document')await reparseDocument(id,button);
    else if(action==='confirm-restore'){if(allowLeave())confirmRestore(id);}
    else if(action==='restore-snapshot')await restoreSnapshot(id,button);
    else if(action==='approve-document'){await viewDocument(id);const status=$('#doc-status');if(status){status.value='approved';toast('请核对资料内容、用途与有效期，然后保存审核结果。');}}
    else if(action==='project-tab'){if(allowLeave())location.hash=`project/${state.projectId}/${value}`;}
    else if(action==='requirement-filter'){if(allowLeave()){state.dirty=false;state.requirementFilter=value;$('#project-content').innerHTML=renderRequirements();}}
    else if(action==='review-requirement'){if(allowLeave()){state.selectedReq=id;state.requirementFilter='all';location.hash=`project/${state.projectId}/requirements`;}}
    else if(action==='review-section'){if(allowLeave()){state.selectedSection=id;location.hash=`project/${state.projectId}/sections`;}}
    else if(action==='select-requirement'){if(allowLeave()){state.dirty=false;state.selectedReq=id;$('#project-content').innerHTML=renderRequirements();}}
    else if(action==='toggle-chapter-group')toggleChapterGroup(value);
    else if(action==='select-section')selectSection(id);
    else if(action==='batch-preview')await previewBatch(value);
    else if(action==='batch-apply')await applyBatch(button);
    else if(action==='content-todos')await showContentTodos();
    else if(action==='trusted-sources')await showTrustedSources();
    else if(action==='trust-apply')await saveTrustedSources(button);
    else if(action==='content-undo')await undoContentBatch(id,button);
    else if(action==='todo-resolve')await resolveContentTodo(id,button);
    else if(action==='regenerate-section-preview')await previewSectionGeneration(id,button);
    else if(action==='index-pages-open')await showIndexPages();
    else if(action==='index-pages-update')await updateIndexPages(button);
    else if(action==='index-materials-preview'){const target=(state.project.sections||[]).find(s=>/评审.*索引/.test(s.outline_group_title||''));if(target)await previewSectionGeneration(target.id,button,true);}
    else if(action==='regenerate-section-confirm')await confirmSectionGeneration(button);
    else if(action==='section-generation-history')await showSectionHistory(id,button);
    else if(action==='restore-section-preview')confirmSectionRestore(id);
    else if(action==='restore-section-confirm')await restoreSectionOriginal(id,button);
    else if(action==='review-rule-detail')showReviewRule(id);
    else if(action==='review-rules-bulk'){setReviewRules(button.dataset.group,value==='on');}
    else if(action==='review-settings-reload'){if(allowLeave())await navigate();}
    else if(action==='run')await runProject(button.dataset.mode,button);
    else if(action==='export')await exportProject(button.dataset.format,button);
    else if(action==='cancel-job')await jobAction('cancel',id,button);
    else if(action==='retry-job')await jobAction('retry',id,button);
    else if(action==='evidence-search')await searchEvidence(button);
    else if(action==='add-evidence'){markFormEdited($('#requirement-form'));const input=$('#evidence-ids');const ids=input.value.split(/[,，\s]+/).filter(Boolean);if(!ids.includes(id))ids.push(id);input.value=ids.join(', ');state.dirty=true;button.textContent='已添加';button.disabled=true;$('#editor-save-state').textContent='有未保存的修改';}
    else if(action==='load-models'){await busy(button,async()=>{const settingsForm=$('#settings-form');const result=await api('/api/settings/models');if(!settingsForm?.isConnected)return;const models=result.models||result.data||[];$('#model-options').innerHTML=models.map(m=>`<option value="${esc(typeof m==='string'?m:m.id)}"></option>`).join('');toast(`已获取 ${models.length} 个模型，可在模型名称输入框选择。`);});}
    else if(action==='test-model'){await busy(button,async()=>{const settingsForm=$('#settings-form');const result=await api('/api/settings/test',{method:'POST'});if(!settingsForm?.isConnected)return;$('#settings-error').textContent=result.ok===false?(result.message||'连接测试未通过'):'';toast(result.message||'已保存的 DeepSeek 连接测试成功。',result.ok===false);});}
  }catch(error){toast(error.message,true);if(state.route==='settings'&&$('#settings-error'))$('#settings-error').textContent=error.message;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;if(!(form instanceof HTMLFormElement))return;event.preventDefault();if(form.dataset.saving==='true')return;const button=event.submitter||$('button[type="submit"]',form)||$(`button[form="${form.id}"]`);const data=Object.fromEntries(new FormData(form));
  if(form.id==='product-module-form'){await saveProductModule(form,button);return;}
  if(form.id==='outline-module-form'){try{saveOutlineModule(form);}catch(error){$('#outline-module-error').textContent=error.message;}return;}
  if(form.id==='knowledge-search'){const query=String(data.query||'').trim();if(query!==state.query)clearKnowledgeSelection(true);state.query=query;await navigate();return;}
  if(form.id==='evidence-search'){await searchEvidence($('[data-action="evidence-search"]',form));return;}
  const context=saveContext(form);const submits=[...form.elements].filter(el=>el.type==='submit'||['new-project-form','import-form'].includes(form.id));const priorDisabled=submits.map(el=>el.disabled);form.dataset.saving='true';submits.forEach(el=>{if(el!==button)el.disabled=true;});
  try{await busy(button,async()=>{try{
    if(form.id==='new-project-form'){const result=await api('/api/projects',{method:'POST',body:data});const p=result.project||result;if(!p.id)throw new Error('创建项目未返回项目编号，请刷新项目列表确认。');toast('项目已创建，请导入本次招标文件。');if(finishSave(context)){closeDialog();location.hash=`project/${p.id}/analyze`;}}
    else if(form.id==='project-basics-form'){await api('/api/projects/'+encodeURIComponent(context.projectId),{method:'PATCH',body:data});await finishProjectSave(context);toast('项目信息已保存，请重新进行交付审查。');}
    else if(form.id==='quotation-form'){const quotation=buildQuotationPayload();await api('/api/projects/'+encodeURIComponent(context.projectId),{method:'PATCH',body:{quotation}});await finishProjectSave(context);toast(quotation.confirmed?'报价已保存并人工确认，请重新进行交付审查。':'报价草稿已保存。');}
    else if(form.id==='review-settings-form'){const enabled=Object.fromEntries($$('.review-rule-input:not(:disabled)',form).map(el=>[el.name,el.checked]));const result=await api('/api/review-settings',{method:'PATCH',body:{enabled,revision:state.reviewSettings.revision}});if(saveIsCurrent(context)){state.reviewSettings=result;if(finishSave(context))renderReviewSettings();}toast('审核设置已保存；本地检查按新设置计算，历史记录保留。');}
    else if(form.id==='settings-form'){data.section_approval_threshold=riskSteps[Number(data.section_approval_threshold)];data.ocr=form.elements.ocr.checked;data.batch_chars=Number(data.batch_chars);data.max_tokens=Number(data.max_tokens);if(!data.api_key)delete data.api_key;const result=await api('/api/settings',{method:'PATCH',body:data});state.settings=result;updateSidebar(result);if(finishSave(context))renderSettings();toast('设置已保存。');}
    else if(form.id==='requirement-form'){data.evidence_ids=String(data.evidence_ids||'').split(/[,，\s]+/).filter(Boolean);await api('/api/requirements/'+encodeURIComponent(form.dataset.id),{method:'PATCH',body:data});await finishProjectSave(context);toast('响应已保存。');}
    else if(form.id==='section-form'){data.status=event.submitter?.value==='approved'?'approved':'draft';const saved=await api('/api/sections/'+encodeURIComponent(form.dataset.id),{method:'PATCH',body:data});await finishProjectSave(context);toast(saved.moved_notes?'章节已保存，'+saved.moved_notes+'处内部提示已转入正文与交付待办。':data.status==='approved'?'章节已保存并批准。':'章节草稿已保存。');}
    else if(form.id==='warning-ack-form'){await api('/api/documents/'+encodeURIComponent(form.dataset.id),{method:'PATCH',body:{warnings_acknowledged:!!form.elements.warnings_acknowledged.checked}});toast('解析提示核对状态已保存。');if(finishSave(context))await viewDocument(form.dataset.id);if(state.route==='project')await refreshProject(!state.dirty);}
    else if(form.id==='document-form'){await api('/api/knowledge/'+encodeURIComponent(form.dataset.id),{method:'PATCH',body:data});toast('资料审核结果已保存。');if(finishSave(context)){closeDialog();if(state.route==='knowledge')await navigate();else if(state.route==='project')await refreshProject(!state.dirty);}}
    else if(form.id==='import-form'){const knowledge=state.importKind==='knowledge',base=knowledge?'/api/knowledge':`/api/projects/${encodeURIComponent(state.projectId)}`;if(state.importMode==='path'){const result=await api(base+'/import',{method:'POST',body:{path:data.path}});trackJobs([result.job||result]);toast('已创建导入任务，可查看后台执行进度。');if(saveIsCurrent(context)){closeDialog();if(knowledge)await navigate();else await refreshProject(!state.dirty);}}else{if(!state.files.length)throw new Error('请先选择需要导入的文件。');form.dataset.uploading='true';const files=[...state.files],failed=[];let success=0;try{for(let i=0;i<files.length;i++){$('#import-progress').textContent=`正在导入 ${i+1} / ${files.length}：${files[i].name}`;const body=new FormData();body.append('file',files[i]);try{const result=await api(base+(knowledge?'/upload':'/documents'),{method:'POST',body});if(result.job)trackJobs([result.job]);success++;}catch(error){failed.push({file:files[i],error:error.message});}}}finally{form.dataset.uploading='false';}if(failed.length){state.files=failed.map(f=>f.file);$('#selected-files').innerHTML=renderSelectedFiles();$('#import-progress').textContent=`已成功导入 ${success} 份，${failed.length} 份失败。再次导入仅重试失败文件。`;$('#import-error').textContent=failed.map(f=>`${f.file.name}：${f.error}`).join('\n');toast(`已导入 ${success} 份，${failed.length} 份未完成。`,true);}else{closeDialog();toast(`已导入 ${success} 份${knowledge?'企业资料，请审核后批准使用':'招标文件'}。`);if(knowledge)await navigate();else await refreshProject();}}}
  }catch(error){const errorIds={'new-project-form':'new-project-error','settings-form':'settings-error','review-settings-form':'review-settings-error','requirement-form':'requirement-error','section-form':'section-error','document-form':'document-error','import-form':'import-error','warning-ack-form':'warning-ack-error','project-basics-form':'project-basics-error','quotation-form':'quotation-error'};const errorEl=saveIsCurrent(context)?$('#'+errorIds[form.id]):null;if(errorEl)errorEl.textContent=error.message;else toast(error.message,true);}});}finally{delete form.dataset.saving;submits.forEach((el,i)=>{if(el.isConnected)el.disabled=priorDisabled[i];});}
});
document.addEventListener('input',event=>{if(event.target.id==='review-select-all')setReviewRules(undefined,event.target.checked);if(event.target.matches('.review-group-input'))setReviewRules(event.target.dataset.group,event.target.checked);if(event.target.matches('.review-rule-input'))updateReviewRuleSwitch(event.target);if(event.target.id==='approval-risk-slider')updateRiskSlider();markFormEdited(event.target.closest('form'));if(event.target.closest('#document-form,#warning-ack-form'))state.dialogDirty=true;if(event.target.closest('#project-basics-form'))state.dialogDirty=true;if(event.target.closest('#quotation-form')){state.dialogDirty=true;if(event.target.name!=='confirmed'){markQuotationChanged();recalculateQuotation();}}if(event.target.closest('#requirement-form,#section-form,#settings-form,#review-settings-form')){state.dirty=true;if($('#editor-save-state'))$('#editor-save-state').textContent='有未保存的修改';if($('#settings-save-label'))$('#settings-save-label').textContent='有未保存的修改';}});
document.addEventListener('change',event=>{if(event.target.id==='file-input'){state.files=[...event.target.files];$('#selected-files').innerHTML=renderSelectedFiles();}});
document.addEventListener('dragover',event=>{const zone=event.target.closest('#dropzone');if(zone){event.preventDefault();zone.classList.add('dragging');}});
document.addEventListener('dragleave',event=>{event.target.closest('#dropzone')?.classList.remove('dragging');});
document.addEventListener('drop',event=>{const zone=event.target.closest('#dropzone');if(zone){event.preventDefault();zone.classList.remove('dragging');state.files=[...event.dataTransfer.files];$('#selected-files').innerHTML=renderSelectedFiles();}});
document.addEventListener('toggle',async event=>{const el=event.target;if(el instanceof HTMLDetailsElement&&el.open&&el.dataset.jobDetails){try{const data=await api('/api/jobs/'+encodeURIComponent(el.dataset.jobDetails));const j=data.job?{...data.job,events:data.events||data.job.events}:data;trackJobs([j]);const logs=j.events||j.logs||[];$('.job-log',el).textContent=logs.length?logs.map(l=>`${l.created_at?time(l.created_at)+' ':''}${l.message||l}`).join('\n'):j.message||'暂无执行记录';}catch(error){$('.job-log',el).textContent=error.message;}}},true);
$('#dialog').addEventListener('cancel',event=>{if($('#dialog').querySelector('[data-uploading="true"]')){event.preventDefault();toast('正在上传文件，请稍候。');}else if(!allowCloseDialog())event.preventDefault();else{++state.dialogToken;state.files=[];}});
window.addEventListener('beforeunload',event=>{if(state.dirty||state.dialogDirty||state.productModuleBusy){event.preventDefault();event.returnValue='';}});
document.addEventListener('keydown',event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){const form=$('#dialog').open?$('#project-basics-form,#quotation-form,#product-module-form'):$('#requirement-form,#section-form,#settings-form,#review-settings-form');if(form){event.preventDefault();const button=$('button[type="submit"]',form)||$(`button[type="submit"][form="${form.id}"]`);if(button&&!button.disabled)form.requestSubmit(button);}}});
window.addEventListener('hashchange',()=>{if(!allowLeave()){history.replaceState(null,'',state.currentHash||'#dashboard');return;}navigate();});
async function pollJobs(){if(state.polling||document.hidden)return;const active=[...state.jobs.values()].filter(j=>['running','queued'].includes(j.status));if(!active.length)return;state.polling=true;let finished=false;try{await Promise.allSettled(active.map(async old=>{try{const raw=await api('/api/jobs/'+encodeURIComponent(old.id));const job=raw.job?{...raw.job,events:raw.events||raw.job.events}:raw;trackJobs([job]);if(!['running','queued'].includes(job.status)){finished=true;toast(`${jobTitle(job)}${statusNames[job.status]||'已更新'}${job.error?'：'+job.error:''}`,job.status==='failed');}}catch{}}));const region=$('#job-region');if(region){const openIds=$$('details[open]',region).map(d=>d.dataset.jobDetails);const jobs=state.route==='project'?(state.project?.jobs||[]).slice(0,3):state.route==='knowledge'?activeJobs([...state.jobs.values()].filter(j=>!j.project_id)):activeJobs([...state.jobs.values()]);region.innerHTML=renderJobs(jobs);openIds.forEach(id=>{const found=$$('details',region).find(x=>x.dataset.jobDetails===id);if(found)found.open=true;});}if(finished&&!$('#dialog').open){if(state.route==='project')await refreshProject(!state.dirty);else if(!state.dirty)await navigate();}}finally{state.polling=false;}}
setInterval(pollJobs,2200);
api('/api/settings').then(s=>{state.settings=s;updateSidebar(s);}).catch(()=>{});
navigate();

/* Local preview -> explicit confirmation -> durable backend operation. */
function contentTodoList(items){return items.length?items.map(t=>`<div class="content-todo"><strong>${esc(t.message)}</strong><p class="small muted">${t.rule_enabled===false?'规则已停用，不计入当前风险与阻断':t.blocks_content?'阻塞内容批准':'不阻塞内容批准'} · ${t.blocks_delivery?'交付待办':'内部提醒'} · ${t.status==='resolved'?'已核对':'未完成'}</p><p class="small muted break">章节：${(t.section_ids||[]).map(id=>esc(state.project?.sections?.find(s=>s.id===id)?.title||id)).join('、')}<br>对应要求：${(t.requirement_ids||[]).map(id=>esc(state.project?.requirements?.find(r=>r.id===id)?.title||id)).join('、')||'见关联章节'}<br>企业来源：${(t.evidence_ids||[]).length?evidenceLinks(t.evidence_ids):'未关联直接企业证据'}</p></div>`).join(''):note('当前范围没有待办。');}

async function previewBatch(action){
  if(!allowLeave())return;
  const projectId=state.projectId,ids=(state.batchSelections?.[projectId]||[]).filter(id=>state.project.sections.some(s=>s.id===id));
  if(!ids.length){toast('请先选择章节。',true);return;}
  const token=openDialog('批量操作预览','只预览所选项目和章节，尚未修改内容。','<p>正在检查固定提示、实质缺项与批准条件…</p>','',true);if(token===null)return;
  try{
    const plan=await api(`/api/projects/${encodeURIComponent(projectId)}/sections/batch/preview`,{method:'POST',body:{section_ids:ids,action}});
    if(!dialogIsCurrent(token)||state.projectId!==projectId)return;
    state.contentPlan=plan;
    const summary=plan.summary;
    const changes=action==='approve'?'':plan.changes.filter(c=>c.removed.length||c.uncertain.length).map(c=>`<details class="batch-diff"><summary>${esc(c.title)} · 可清理 ${c.removed.length} 处 · ${action==='separate_notes'?'混合正文需核对':'保留待判定'} ${c.uncertain.length} 处</summary><p class="small">拟移出的提示（混有正文时整行保留到待办）：${c.removed.map(r=>esc(r.text)).join('；')||'无'}</p><pre>${esc(c.diff||'无自动修改')}</pre>${c.uncertain.length?note(esc(c.uncertain.map(x=>x.reason+'：'+x.text).join('\n')),'warning'):''}</details>`).join('');
    const assessments=plan.assessments.map(a=>`<tr><td>${esc(a.title)}</td><td>${riskBadge(a.risk_level)}<small style="display:block">${a.status==='approved'?'已批准，跳过':a.eligible?'可批准':'当前阈值拦截'}</small></td><td><p>${esc(a.approval_reason)}</p>${esc(a.risk_reasons.join('；')||'未发现正文阻塞项；交付待办仍单独处理')}</td></tr>`).join('');
    openDialog(action==='approve'?'批量批准预览':action==='separate_notes'?'正文与内部提示分离预览':'清理固定提示预览',`本项目选中 ${summary.selected} 章；清理前会备份数据库并保存内容快照。`,`${note('内容批准不等于 AI 审计通过或可正式提交。附件和签章不会被自动标记完成。')}<p>当前拦截阈值：<strong>${esc(plan.approval_policy.label)}</strong>。低风险 ${summary.risk_counts.low} 章，中风险 ${summary.risk_counts.medium} 章，高风险 ${summary.risk_counts.high} 章。</p><p>本次可批准 ${summary.eligible} 章，已批准 ${summary.already_approved} 章，受阈值拦截 ${summary.blocked} 章。可清理 ${summary.affected} 章、${summary.responses_affected} 条关联响应。</p>${changes}<details open><summary>所选章节与批准原因</summary><div class="table-scroll"><table class="batch-table"><thead><tr><th>章节</th><th>风险及处理</th><th>风险原因</th></tr></thead><tbody>${assessments}</tbody></table></div></details><details><summary>待办与待判定项（${plan.todos.length}项，相同问题集中记录）</summary>${contentTodoList(plan.todos)}</details><label class="checkbox-line"><input type="checkbox" id="batch-confirm"><span>我已核对上述项目、章节、差异与待办，确认按当前风险阈值执行${action==='approve'?'内容批准（保留原风险）':action==='separate_notes'?'内部提示转入待办（不批准正文）':'固定提示清理'}。</span></label><div id="content-operation-error" class="form-error" role="alert"></div>`,`${btn('取消','close-dialog')}${btn(action==='approve'?'批准可用章节':action==='separate_notes'?'确认移入内部待办':'执行本次清理','batch-apply','','primary')}`,true);
  }catch(e){if(dialogIsCurrent(token))$('#dialog-content .dialog-body').innerHTML=note(esc(e.message),'error');}
}

async function applyBatch(button){
  const plan=state.contentPlan;if(!plan||plan.project_id!==state.projectId)throw new Error('项目已变化，请重新预览。');
  if(!$('#batch-confirm')?.checked){$('#content-operation-error').textContent='请先勾选确认本次执行范围。';return;}
  const token=state.dialogToken,route=state.routeToken;
  await busy(button,async()=>{try{
    const result=await api(`/api/projects/${encodeURIComponent(plan.project_id)}/sections/batch/apply`,{method:'POST',body:{section_ids:plan.section_ids,action:plan.action,token:plan.token,confirmed:true}});
    if(dialogIsCurrent(token))openDialog('批量操作结果',`已清理 ${result.changed} 章，已批准 ${result.approved} 章，跳过 ${result.skipped} 章，失败 ${result.failed} 章。`,`${result.results.map(r=>`<p><strong>${esc(r.title)}</strong>：${esc(r.reason)}</p>`).join('')}${result.snapshot_id?`<p class="small muted break">快照编号：${esc(result.snapshot_id)}。可撤销本轮操作；后续编辑变化时会阻止覆盖恢复。</p>`:''}`,`${btn('关闭','close-dialog')}${result.snapshot_id?btn('撤销本轮操作','content-undo',`data-id="${esc(result.snapshot_id)}"`):''}`,true);
    state.contentPlan=null;if(route===state.routeToken)await refreshProject(!state.dirty);
  }catch(e){if(dialogIsCurrent(token))$('#content-operation-error').textContent=e.message;else toast(e.message,true);}});
}

async function showContentTodos(){
  const id=state.projectId,token=openDialog('正文与交付待办','相同问题集中核对，保留章节和要求关联。','<p>正在读取…</p>','',true);if(token===null)return;
  const data=await api(`/api/projects/${encodeURIComponent(id)}/content-todos`);if(!dialogIsCurrent(token)||id!==state.projectId)return;
  state.contentTodoData=data;
  openDialog('正文与交付待办','声明认可、附件和签章分别留存；不自动完成交付。',data.todos.map(t=>`${contentTodoList([t])}${t.status!=='resolved'&&t.can_resolve?`<details><summary>记录本项核对</summary><label for="todo-note-${esc(t.id)}">核对依据或实际交付记录（至少10字）</label><textarea id="todo-note-${esc(t.id)}" rows="3"></textarea><label class="checkbox-line"><input type="checkbox" id="todo-confirm-${esc(t.id)}"><span>我确认本项已实际核对或完成，并对记录负责。</span></label>${btn('保存核对记录','todo-resolve',`data-id="${esc(t.id)}"`,'small')}</details>`:''}`).join('')||note('当前没有待办。'),btn('关闭','close-dialog'),true);
}

async function resolveContentTodo(id,button){
  const item=state.contentTodoData?.todos.find(t=>t.id===id);if(!item)return;
  if(!$('#todo-confirm-'+id)?.checked){toast('请明确确认此项实际核对结果。',true);return;}
  const projectId=state.projectId,token=state.dialogToken,noteText=$('#todo-note-'+id)?.value||'';
  await busy(button,async()=>{await api(`/api/projects/${encodeURIComponent(projectId)}/content-todos/${encodeURIComponent(id)}/resolve`,{method:'POST',body:{signature:item.signature,note:noteText,confirmed:true}});if(dialogIsCurrent(token)&&projectId===state.projectId)await showContentTodos();});
}

async function showTrustedSources(){
  const id=state.projectId,token=openDialog('本项目可信资料','批量选择认可的企业内容来源，不修改企业原资料。','<p>正在读取…</p>','',true);if(token===null)return;
  const plan=await api(`/api/projects/${encodeURIComponent(id)}/trusted-sources`);if(!dialogIsCurrent(token)||id!==state.projectId)return;state.trustPlan=plan;
  openDialog('本项目可信资料','已批准且适用的资料自动可复用；这里可一次认可本项目补充资料。',`${note('仅认可未改变事实含义的内容复用，不把采购要求、失败案例、其他客户专属事实或 AI 新增内容当作企业事实。附件装入和签章仍单独核对。')}<div>${plan.documents.map(d=>`<label class="checkbox-line"><input class="trusted-source" type="checkbox" value="${esc(d.id)}" ${d.trusted?'checked':''} ${d.eligible?'':'disabled'}><span>${esc(d.name)}<small class="muted"> · ${esc(d.reason)}</small></span></label>`).join('')}</div><label class="checkbox-line"><input id="trust-confirm" type="checkbox"><span>我认可选中资料作为本项目的内容来源，已核对其适用范围。</span></label>`,`${btn('取消','close-dialog')}${btn('确认本项目资料范围','trust-apply','','primary')}`,true);
}

async function saveTrustedSources(button){
  if(!$('#trust-confirm')?.checked){toast('请先确认本项目资料范围。',true);return;}
  const plan=state.trustPlan,token=state.dialogToken,ids=$$('.trusted-source:checked:not(:disabled)').map(x=>x.value);
  if(plan.project_id!==state.projectId)throw new Error('项目已切换，请重新选择。');
  await busy(button,async()=>{await api(`/api/projects/${encodeURIComponent(plan.project_id)}/trusted-sources`,{method:'POST',body:{document_ids:ids,token:plan.token,confirmed:true}});if(dialogIsCurrent(token))closeDialog();if(plan.project_id===state.projectId)await refreshProject(!state.dirty);toast('本项目资料范围已保存。');});
}

async function undoContentBatch(id,button){
  if(!window.confirm('确认撤销这一次批量操作？将恢复该轮修改的章节、关联响应和批准状态；有后续变化时停止恢复。'))return;
  const token=state.dialogToken,projectId=state.projectId;
  await busy(button,async()=>{const r=await api(`/api/content-operations/${encodeURIComponent(id)}/undo`,{method:'POST',body:{confirmed:true}});if(dialogIsCurrent(token))closeDialog();if(projectId===state.projectId)await refreshProject(!state.dirty);toast(r.message);});
}

document.addEventListener('change',event=>updateSectionSelection(event.target));

// Enterprise knowledge selection uses a concrete ID set, never a filter predicate at submit time.
document.addEventListener('change',event=>{
  const el=event.target;
  if(el.id==='knowledge-scope'){state.knowledgeScope=el.value;state.knowledgePage=1;clearKnowledgeSelection(true);renderKnowledge();}
  else if(el.id==='knowledge-select-page'){knowledgePageDocs().forEach(d=>el.checked?state.knowledgeSelected.add(d.id):state.knowledgeSelected.delete(d.id));state.knowledgeSelectionAll=false;renderKnowledge();}
  else if(el.dataset.knowledgeSelect){el.checked?state.knowledgeSelected.add(el.dataset.knowledgeSelect):state.knowledgeSelected.delete(el.dataset.knowledgeSelect);state.knowledgeSelectionAll=false;renderKnowledge();}
});
const knowledgeResultNames={eligible:'可批准',approved:'已批准',already_approved:'已批准，跳过',changed:'内容变化，跳过',blocked:'暂不可批准',failed:'执行失败',undone:'已撤销',conflict:'冲突，跳过'};
function renderKnowledgeOperations(){const ops=state.knowledgeOperations||[];return ops.length?`<section class="panel" style="margin-top:20px"><div class="panel-head"><h2>最近批量批准</h2></div>${ops.map(op=>`<div class="knowledge-operation"><span>${date(op.created_at)} · 批准 ${op.counts.approved} 份${op.undone?' · 已执行撤销':''}</span>${op.can_undo?btn('撤销本次批准','knowledge-undo-preview',`data-id="${esc(op.id)}"`,'small'):''}</div>`).join('')}</section>`:'';}
function knowledgeResultTable(items,preview=false){return `<div class="table-wrap"><table class="knowledge-preview-table"><thead><tr><th>资料名称 / 产品范围</th><th>${preview?'本次认可内容范围':'实际结果'}</th><th>处理原因</th></tr></thead><tbody>${items.map(item=>`<tr><td><strong>${esc(item.name)}</strong><small class="block muted">${esc(scopeNames[item.scope]||item.scope||'')}</small></td><td>${esc(preview?item.recognized_scope:knowledgeResultNames[item.status])}${preview?`<small class="block muted">${item.readable_blocks} 段 · ${item.readable_chars} 字符${item.expanded?' · 认可版本改变':''}</small>`:''}</td><td>${preview?`<strong>${esc(knowledgeResultNames[item.status])}</strong><br>`:''}${esc(item.reason)}${item.attachment_note?`<p class="small">${esc(item.attachment_note)}</p>`:''}${preview&&Object.keys(item.constraints||{}).length?`<details><summary>版本及适用条件</summary><pre class="knowledge-excerpt">${esc(JSON.stringify(item.constraints,null,2))}</pre></details>`:''}${preview&&item.content_preview?.length?`<details><summary>查看本次认可的实际正文</summary>${item.content_preview.map(part=>`<div class="knowledge-excerpt"><small class="source-label">${esc(part.locator)}</small><p>${esc(part.text)}</p></div>`).join('')}</details>`:''}</td></tr>`).join('')}</tbody></table></div>`;}
async function previewKnowledgeApproval(button){
  const ids=[...state.knowledgeSelected];if(!ids.length)return;
  await busy(button,async()=>{const response=await api('/api/knowledge/approval/preview',{method:'POST',body:{document_ids:ids}});state.knowledgePreview=response;
    const n=response.counts.eligible;
    openDialog('批量批准企业资料',`已选 ${response.selected} 份 · 可批准 ${n} 份 · 已批准跳过 ${response.counts.already_approved} 份 · 暂不可批准 ${response.counts.blocked} 份`,`${note(esc(response.confirmation))}${knowledgeResultTable(response.items,true)}<div id="knowledge-batch-error" class="form-error" role="alert"></div>`,`${btn('取消','close-dialog')}${btn('确认批准'+n+'份','knowledge-bulk-apply',n?'':'disabled','primary')}`,true);
  });
}
async function refreshKnowledgeBehindDialog(){const [k,ops]=await Promise.all([api('/api/knowledge'+(state.query?'?q='+encodeURIComponent(state.query):'')),api('/api/knowledge/approval/operations')]);state.knowledge=k;state.knowledgeOperations=ops.operations;if(state.route==='knowledge')renderKnowledge();}
function showKnowledgeResult(result,undo=false){openDialog(undo?'撤销本次批准结果':'批量批准结果',`处理 ${result.selected} 份 · ${undo?'已撤销 '+result.counts.undone+' 份 · 冲突跳过 '+result.counts.conflict+' 份':'已批准 '+result.counts.approved+' 份 · 已批准跳过 '+result.counts.already_approved+' 份 · 内容变化跳过 '+result.counts.changed+' 份 · 暂不可批准 '+result.counts.blocked+' 份 · 失败 '+result.counts.failed+' 份'}`,`${note('此处仅为企业资料使用状态；投标章节、签章、附件和历史审计未被批准或改写。')}${knowledgeResultTable(result.items)}`,`${result.can_undo?btn('撤销本次批准','knowledge-undo-preview',`data-id="${esc(result.operation_id)}"`):''}${btn('关闭','close-dialog','','primary')}`,true);}
async function executeKnowledgeApproval(button){const plan=state.knowledgePreview;if(!plan)return;await busy(button,async()=>{try{const result=await api('/api/knowledge/approval/apply',{method:'POST',body:{ticket:plan.ticket,confirmed:true}});clearKnowledgeSelection();showKnowledgeResult(result);await refreshKnowledgeBehindDialog();}catch(error){const el=$('#knowledge-batch-error');if(el)el.textContent=error.message;else toast(error.message,true);}});}
function confirmKnowledgeUndo(id){openDialog('撤销本次资料批准','只恢复本次实际修改资料的批准状态及认可范围。',note('后续被编辑、重新解析或再次审核的资料会跳过，保留后续决定。不会恢复整个数据库或修改投标正文。'),`${btn('取消','close-dialog')}${btn('确认撤销','knowledge-undo',`data-id="${esc(id)}"`,'primary')}`,false);}
async function undoKnowledgeApproval(id,button){await busy(button,async()=>{const result=await api('/api/knowledge/approval/'+encodeURIComponent(id)+'/undo',{method:'POST',body:{confirmed:true}});showKnowledgeResult(result,true);await refreshKnowledgeBehindDialog();});}

// Report the build that actually served this page, never a frontend release literal.
let pageBuild = null;
async function loadBuildInfo(){
  const label=document.querySelector('#build-label'),button=document.querySelector('#copy-test-info');
  if(!label||!button)return;
  try{
    const response=await fetch('/api/build',{cache:'no-store'});
    if(!response.ok)throw Error('服务尚未提供构建信息');
    const build=await response.json();
    if(build.build_id!==document.documentElement.dataset.build)throw Error('服务版本已变化，请刷新页面');
    pageBuild=build;
    label.textContent='当前验收版本：'+build.acceptance_version;
    label.title='构建：'+build.build_id+'；启动：'+build.started_at;
    button.disabled=false;
  }catch(error){label.textContent='版本未确认：'+error.message;button.disabled=true;}
}
document.querySelector('#copy-test-info')?.addEventListener('click',async()=>{
  const build=pageBuild;if(!build)return;
  const p=state.route==='project'?state.project?.project:null;
  const object=p?`${p.name}／项目ID：${p.id}`:'当前页面：'+document.querySelector('#page-name').textContent;
  const text=`测试版本：${build.acceptance_version}

测试对象：${object}／生成任务编号：待填写／导出文件名：待填写

问题编号：待填写

操作：

实际结果：

期望结果：

证据：截图／出错文字／相关文件

通过版本：待复测

测试时间：${new Date().toISOString()}
页面地址：${location.origin}${location.pathname}${location.hash}
构建指纹：${build.build_id}
代码提交：${build.commit||'无Git信息'}（${build.dirty===null?'状态未知':build.dirty?'含未提交修改':'干净'}）
服务启动时间：${build.started_at}`;
  try{await navigator.clipboard.writeText(text);toast('测试信息已复制，可粘贴到反馈 Markdown 文件');}
  catch{openDialog('复制测试信息','自动复制不可用，请选中以下文本复制。',`<textarea readonly style="width:100%;min-height:320px">${esc(text)}</textarea>`,btn('关闭','close-dialog'));}
});
loadBuildInfo();

function renderIndexMaterialPlan(plan){
  const m=plan.index_materials;if(!m)return '';
  return `<div class="notice"><label><input type="checkbox" id="index-update-pages" checked> 完成后自动排版并更新实际页数（不调用模型）</label></div>
  ${m.items.length?`<h3>评分关联缺项</h3><table><thead><tr><th>章节</th><th>实际情况与资料查找</th><th>本次处理</th></tr></thead><tbody>${m.items.map(x=>`<tr><td>${esc(x.group_title)} / ${esc(x.title)}</td><td>${esc(x.reason)}${x.candidates?.length?`<details><summary>找到 ${x.candidates.length} 条相关来源，仍须核对是否能支持本项</summary>${x.candidates.map(c=>`<p><strong>${esc(c.name)}</strong> · ${esc(c.locator)}<br>${esc(c.excerpt)}</p>`).join('')}</details>`:''}</td><td>${esc(x.action||'需要项目负责人提供实际信息')}</td></tr>`).join('')}</tbody></table>`:'<p>当前评分关联章节没有检测到空正文或缺失附件，直接更新索引和页数。</p>'}
  ${m.fillable_ids.length?`<p><label><input type="checkbox" id="index-fill-gaps" checked> 同时补全预览中的 ${m.fillable_ids.length} 节</label><br><small>检索后最多 ${m.model_requests} 次正文生成主请求，每节校验修复最多2次，连接尝试沿用最多3次；逐节保存草稿和原文快照。找不到的依据仍保留缺项，不自动批准。</small></p>`:''}`;
}

async function showIndexPages(){
  const projectId=state.projectId;
  const token=openDialog('实际页数与资料缺项','页数来自当前内容实际排版后的导出文件。','<p>正在核对正文版本与页数…</p>','',true);
  if(token===null)return;
  try{
    const data=await api(`/api/projects/${encodeURIComponent(projectId)}/index-pages`);
    if(!dialogIsCurrent(token)||projectId!==state.projectId)return;
    const url=safeLocalUrl(data.export?.url);
    openDialog('实际页数与资料缺项',data.reason,
      `${data.export?`<p>对应文件：${esc(data.export.name)} · ${date(data.export.created_at)} ${time(data.export.created_at)} ${url?`<a href="${url}" download>下载这份已排版文件</a>`:''}</p>`:''}
       <table><thead><tr><th>章节</th><th>正文／附件</th><th>实际页数</th></tr></thead><tbody>${(data.items||[]).map(r=>`<tr><td>${esc(r.group_title)} / ${esc(r.title)}</td><td>${esc(r.reason)}</td><td>${r.page!=null?esc(r.page):esc(r.page_status)}</td></tr>`).join('')}</tbody></table>
       <p class="small muted">正文、资料范围或排版设置变化后，旧页数自动失效。签章和最终提交仍需单独核对。</p>`,
      `${btn('关闭','close-dialog')}${btn('检查并补全缺项','index-materials-preview')}${btn('仅排版并更新页数','index-pages-update','','primary')}`,true);
  }catch(error){if(dialogIsCurrent(token)){$('#dialog-content .dialog-body').innerHTML=note(esc(error.message),'error');}}
}

async function updateIndexPages(button){
  if(state.dirty){toast('请先保存当前编辑，再更新页数。',true);return;}
  await busy(button,async()=>{try{
    const result=await api(`/api/projects/${encodeURIComponent(state.projectId)}/index-pages`,{method:'POST',body:{}});
    trackJobs([result.job]);closeDialog();await refreshProject(!state.dirty);toast('正在按当前正文和附件排版；完成后可查看实际页数。');
  }catch(error){toast(error.message,true);}});
}

async function previewSectionGeneration(id,button,projectScope=false){
  if(state.dirty){toast('请先保存本节编辑，再发起AI重新生成。',true);return;}
  const routeToken=state.routeToken;
  await busy(button,async()=>{try{
    const plan=await api('/api/sections/'+encodeURIComponent(id)+'/regeneration');
    if(routeToken!==state.routeToken||(!projectScope&&state.selectedSection!==id))return;
    state.sectionGenerationPlan={...plan,request_id:crypto.randomUUID()};
    openDialog('AI重新生成本节',plan.index_materials?'本次范围：索引及下方明确选中的缺项':'本次范围：仅1节 · '+plan.title,
      `${note(esc(plan.notice),'warning')}${renderIndexMaterialPlan(plan)}${plan.uses_saved_module_draft?note(`本次将使用已保存的 ${fmt(plan.saved_draft_chars)} 字符正文及放入时的模块素材重新编写本章。完成后替换当前草稿，并保留生成前原文快照供恢复。`):''}<p>关联招标要求：${plan.requirement_count}条。${plan.user_edited?'本章已有人工编辑，将以新草稿替换。':''}${plan.status==='approved'?'本章已批准，重新生成后需再次批准。':''}</p><div class="field"><label for="section-instruction">本节补充写作要求（可选）</label><textarea id="section-instruction" maxlength="2000" placeholder="${esc(plan.instruction_placeholder||'例如：按功能点逐项展开，并保留表格结构。不得要求模型编造企业事实。')}"></textarea></div><div id="section-generation-error" class="form-error" role="alert"></div>`,
      `${btn('取消','close-dialog')}${btn(plan.index_materials?'按所选范围执行':'确认调用AI，仅重写本节','regenerate-section-confirm','','primary')}`);
  }catch(error){toast(error.message,true);}});
}
async function confirmSectionGeneration(button){
  const plan=state.sectionGenerationPlan;if(!plan)return;
  const instruction=$('#section-instruction').value,routeToken=state.routeToken,dialogToken=state.dialogToken;
  const extra=plan.index_materials?{update_index_pages:!!$('#index-update-pages')?.checked,...($('#index-fill-gaps')?.checked?{complete_index_gaps:true,completion_revision:plan.index_materials.revision,completion_ids:plan.index_materials.fillable_ids}:{})}:{};
  await busy(button,async()=>{try{
    const result=await api('/api/sections/'+encodeURIComponent(plan.section_id)+'/regenerate',{method:'POST',body:{revision:plan.revision,outline_revision:plan.outline_revision,request_id:plan.request_id,instruction,confirmed:true,...extra}});
    trackJobs([result.job]);if(dialogIsCurrent(dialogToken))closeDialog();
    if(routeToken===state.routeToken)await refreshProject(!state.dirty);
    toast(plan.index_materials?'索引任务已提交；可查看补全和排版进度。':'单章AI任务已提交；可查看进度或取消。');
  }catch(error){if(dialogIsCurrent(dialogToken)&&$('#section-generation-error'))$('#section-generation-error').textContent=error.message;else toast(error.message,true);}});
}
async function showSectionHistory(id,button){
  const routeToken=state.routeToken;
  await busy(button,async()=>{try{
    const data=await api('/api/sections/'+encodeURIComponent(id)+'/generation-history');
    if(routeToken!==state.routeToken)return;
    openDialog('本章生成前原文','每次成功重新生成前的原文；恢复后为草稿，需重新审核。',data.snapshots.length?data.snapshots.map(s=>`<details><summary>${esc(s.title)} · ${date(s.created_at)} ${time(s.created_at)}</summary><pre style="white-space:pre-wrap;max-height:320px;overflow:auto">${esc(s.content)}</pre>${btn('恢复此原文','restore-section-preview',`data-id="${esc(s.id)}"`)}</details>`).join(''):note('本章还没有单章重新生成快照。'),btn('关闭','close-dialog'),true);
  }catch(error){toast(error.message,true);}});
}
function confirmSectionRestore(id){
  if(state.dirty){toast('请先保存本章当前编辑。',true);return;}
  openDialog('恢复本章原文','只恢复这次生成前的正文，恢复后为草稿。',note('若本章已被再次编辑、批准或生成，将拒绝恢复，保留后续修改。'),`${btn('取消','close-dialog')}${btn('确认恢复原文','restore-section-confirm',`data-id="${esc(id)}"`,'primary')}`);
}
async function restoreSectionOriginal(id,button){
  const token=state.routeToken;
  await busy(button,async()=>{try{const result=await api('/api/section-generation/'+encodeURIComponent(id)+'/restore',{method:'POST'});closeDialog();if(token===state.routeToken)await refreshProject(!state.dirty);toast(result.message);}catch(error){toast(error.message,true);}});
}

/* Compilation outline: local selection, explicit save, then separately confirmed AI planning. */
function outlineNeedsPlanning(plan){return !!plan&&(!plan.confirmed||(plan.generation?.pending_group_ids||[]).length>0);}
function outlineProgressLabel(plan){if(!plan)return '选择一级模块';return `${(plan.groups||[]).filter(g=>g.enabled&&!g.duplicate_of).length} 个已启用${plan.confirmed?' · 已保存':' · 待确认'}`;}
function projectGenerationAction(disabled=false){
  if(state.tab==='outline')return btn(`${icon('arrow')}进入章节编辑`,'outline-enter',disabled?'disabled':'','primary');
  if(outlineNeedsPlanning(state.project?.compilation_outline))return btn('调整编制目录','project-tab','data-value="outline"','primary');
  return state.tab==='sections'?btn('选择本节产品功能模块','product-picker-open',`data-id="${esc(state.project?.sections?.find(s=>s.id===state.selectedSection)?.id||state.project?.sections?.[0]?.id||'')}" ${disabled||!state.project?.sections?.length?'disabled':''}`,'primary'):btn('进入章节，准备内容','project-tab',`data-value="sections" ${disabled?'disabled':''}`,'primary');
}
function setOutlineDraft(plan){if(state.outlineDraftProjectId!==state.projectId)state.outlineExpanded=new Set();state.outlineExpanded??=new Set();state.outlineDraft=JSON.parse(JSON.stringify(plan));state.outlineDraftProjectId=state.projectId;state.outlineFilter='all';state.outlineEditVersion=0;}
function outlineEdited(){state.dirty=true;state.outlineEditVersion=(state.outlineEditVersion||0)+1;}
function outlineTitleKey(title){return String(title||'').normalize('NFKC').replace(/^\s*(?:第[一二三四五六七八九十百零〇\d]+[章节部分]|[一二三四五六七八九十百零〇\d]+[、.．])\s*/,'').replace(/\s+/g,'').toLocaleLowerCase();}
function outlineDuplicate(group,groups){
  if(group.origin!=='generic')return null;
  return group.duplicate_of||(groups.find(g=>['tender','scoring'].includes(g.origin)&&outlineTitleKey(g.title)===outlineTitleKey(group.title))?.id)||null;
}
function outlineVisibleGroups(plan=state.outlineDraft,filter=state.outlineFilter||'all'){return (plan?.groups||[]).filter(g=>filter==='all'||g.origin===filter);}
function outlineNumberedGroups(groups){let count=0;return groups.map(g=>({...g,display_number:g.enabled&&!outlineDuplicate(g,groups)?outlineChineseNumber(++count)+'、':'未启用'}));}
function reorderOutlineRows(rows,visibleIds,fromId,toId){
  const visible=new Set(visibleIds),order=rows.filter(row=>visible.has(row.id));
  const from=order.findIndex(row=>row.id===fromId),to=order.findIndex(row=>row.id===toId);
  if(from<0||to<0||from===to)return rows;
  order.splice(to,0,order.splice(from,1)[0]);let index=0;
  return rows.map(row=>visible.has(row.id)?order[index++]:row);
}
function applyOutlinePreset(groups,preset){
  const modules=new Set(preset.module_ids||[]),skipped=[];
  const next=groups.map(group=>{
    if(group.origin!=='generic'||!group.module_id)return {...group};
    const selected=modules.has(group.module_id),duplicate=outlineDuplicate(group,groups);
    if(selected&&duplicate)skipped.push(group.title);
    return {...group,enabled:selected&&!duplicate};
  });return {groups:next,skipped};
}
function outlineSourceDescription(ref){
  if(typeof ref==='string')return ref;
  return [ref.document_name||ref.name,ref.locator||ref.location,ref.title,ref.quote||ref.text||ref.third_column].filter(Boolean).join(' · ')||'已关联招标原文';
}
function outlineMoveButtons(kind,id,index,length){return `<div class="outline-move"><span class="outline-drag" draggable="true" data-outline-drag="${kind}" data-id="${esc(id)}" title="拖动调整顺序" aria-label="拖动调整顺序">⠿</span>${btn('↑','outline-move',`data-kind="${kind}" data-id="${esc(id)}" data-direction="-1" aria-label="上移" ${index===0?'disabled':''}`,'small ghost')}${btn('↓','outline-move',`data-kind="${kind}" data-id="${esc(id)}" data-direction="1" aria-label="下移" ${index===length-1?'disabled':''}`,'small ghost')}</div>`;}
function renderProjectOutline(){
  const plan=state.outlineDraftProjectId===state.projectId?state.outlineDraft:state.project?.compilation_outline;
  if(!plan)return `<section class="panel">${empty('目录建议尚未加载','请重新打开当前步骤获取目录模块。',btn('重新加载','refresh'))}</section>`;
  const all=outlineNumberedGroups(plan.groups||[]),visible=all.filter(g=>(state.outlineFilter||'all')==='all'||g.origin===state.outlineFilter),enabled=all.filter(g=>g.enabled&&!outlineDuplicate(g,all)),pending=plan.generation?.pending_group_ids||[],running=(state.project?.jobs||[]).some(j=>['queued','running'].includes((state.jobs.get(j.id)||j).status));
  return `<div class="outline-page"><section class="panel"><div class="panel-head"><div><h2>调整编制目录</h2><p>确认一、二级标题、顺序和编制范围；已有目录可以直接保存，无需 AI。</p></div>${badge(state.dirty||!plan.confirmed?'draft':'approved',state.dirty?'有未保存的调整':plan.confirmed?'选择已保存':'待确认选择')}</div><div class="panel-body">
    ${renderSecondarySourceStrategy(plan)}
    <div class="toolbar outline-toolbar"><div class="chips" aria-label="目录类型筛选">${[['all','全部类型'],['tender','招标文件提取'],['scoring','评分细则建议'],['generic','通用模块']].map(([value,label])=>`<button class="chip ${(state.outlineFilter||'all')===value?'active':''}" data-action="outline-filter" data-value="${value}">${label}</button>`).join('')}</div><div class="field-inline"><label for="outline-preset">应用预设</label><select id="outline-preset" ${running?'disabled':''}><option value="">选择一个预设</option>${(plan.presets||[]).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></div></div>
    <p class="small muted outline-scope">显示 ${visible.length} / ${all.length} 个模块 · 已启用 ${enabled.length} 个。筛选不改变选择；拖动或上下移动只重排当前可见项，保留其他项的位置。相关度为本地关键词匹配建议，不代表内容已获证实。</p>
    <fieldset class="outline-fieldset" ${running||state.outlineBusy?'disabled':''}><div class="table-wrap"><table class="outline-table"><thead><tr><th>顺序</th><th>标题名称</th><th>类型</th><th>启用 / 不启用</th></tr></thead><tbody>${visible.map((g,index)=>{const duplicate=outlineDuplicate(g,all);return `<tr data-outline-drop="project" data-id="${esc(g.id)}" class="${g.enabled&&!duplicate?'outline-enabled':'outline-disabled'}"><td>${outlineMoveButtons('project',g.id,index,visible.length)}</td><td><span class="outline-number">${esc(g.display_number)}</span><strong>${esc(g.title)}</strong><div class="outline-child-toggle">${btn(`${state.outlineExpanded?.has(g.id)?'收起':'展开'}二级目录（${outlineChildren(g).length}）`,'outline-child-toggle',`data-group="${esc(g.id)}" aria-expanded="${!!state.outlineExpanded?.has(g.id)}"`,'small ghost')}</div>${g.existing_count?`<small class="block muted">已有 ${fmt(g.existing_count)} 节，沿用现有正文与记录</small>`:''}${duplicate?'<small class="block warning-text">与招标目录同名，沿用原目录，不重复创建。</small>':''}${g.source_refs?.length?`<details class="outline-source"><summary>查看提取位置</summary>${g.source_refs.map(ref=>`<p>${esc(outlineSourceDescription(ref))}</p>`).join('')}</details>`:''}</td><td>${g.origin==='tender'?badge('ready','招标文件提取'):g.origin==='scoring'?badge('ready','评分细则建议'):badge('draft',g.existing_only?'通用模块（已有目录）':'通用模块')}${g.origin==='generic'&&!g.existing_only?`<small class="block muted">关键词相关度 ${esc(g.relevance??0)}${g.matched_keywords?.length?` · ${esc(g.matched_keywords.join('、'))}`:''}</small>`:''}</td><td><label class="rule-switch"><input type="checkbox" data-outline-enabled="${esc(g.id)}" ${g.enabled&&!duplicate?'checked':''} ${duplicate?'disabled':''} aria-label="启用${esc(g.title)}"><span class="rule-track"></span><span class="rule-state">${g.enabled&&!duplicate?'启用':'不启用'}</span></label></td></tr>${state.outlineExpanded?.has(g.id)?`<tr class="outline-child-container"><td colspan="4">${renderOutlineChildren(g,all)}</td></tr>`:''}`;}).join('')||'<tr><td colspan="4">当前类型没有目录模块。</td></tr>'}</tbody></table></div></fieldset>
    ${(plan.fixed_groups||[]).length?`<details class="outline-fixed"><summary>独立分册及内部内容（${plan.fixed_groups.length} 组）</summary><p class="small muted">以下内容由项目分册保留，不标记为招标文件提取的技术目录。</p>${plan.fixed_groups.map(g=>`<p>${esc(g.title)} · ${(g.section_ids||g.sections||[]).length} 节</p>`).join('')}</details>`:''}
    ${plan.retained_count?`<p class="small muted">${fmt(plan.retained_count)} 个已有章节暂未编入本次目录，原文、批准和历史仍保留，可在章节编辑中查看。</p>`:''}
    <div id="outline-error" class="form-error" role="alert"></div><div class="outline-footer"><div><strong>${enabled.length} 个一级模块启用</strong><small>保存目录与新建空章节，不生成或追加正文；停用保留原文与历史。</small></div><div class="actions">${state.outlineLastOperation?.projectId===state.projectId?btn('撤销本次目录调整','outline-undo-preview',`data-id="${esc(state.outlineLastOperation.id)}" ${running?'disabled':''}`):''}${btn('保存一、二级目录','outline-save',running||state.outlineBusy?'disabled':'','primary')}${btn(`AI规划新增下级目录${!state.dirty&&pending.length?'（'+pending.length+'个模块）':''}`,'outline-generate-preview',running||state.dirty||!plan.confirmed||!pending.length?'disabled':'')}${btn('进入章节编辑','outline-enter',running?'disabled':'')}</div></div>
  </div></section></div>`;
}
function rerenderOutline(){if(state.route==='project'&&state.tab==='outline')$('#project-content').innerHTML=renderProjectOutline();else if(state.route==='outline-library')renderOutlineLibrary();}
function renderOutlineLibrary(){
  const library=state.outlineLibrary;if(!library)return;
  const modules=library.modules||[],presets=library.presets||[];
  $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">OUTLINE LIBRARY</div><h1>通用目录库</h1><p>配置一级模块、模块下的二级标题和三个常用组合。新项目按本库顺序推荐，已有项目保留自己的目录快照。</p></div><div class="actions">${btn(`${icon('plus')}新增模块`,'outline-module-add',state.outlineBusy?'disabled':'')}${btn('保存目录库','outline-library-save',state.outlineBusy?'disabled':'','primary')}</div></div>
    ${note('本库只定义可选目录，不作为企业能力证据。没有招标规定目录时，通用模块同样默认全部关闭，由你手动选择。')}
    <fieldset class="outline-fieldset" ${state.outlineBusy?'disabled':''}><section class="panel outline-library-panel"><div class="panel-head"><div><h2>一级目录模块 <span class="small muted">${modules.length} 项</span></h2><p>拖动或使用上下按钮调整默认顺序。</p></div><span class="small muted">${state.dirty?'有未保存的调整':'当前目录库已保存'}</span></div><div class="table-wrap"><table class="outline-table"><thead><tr><th>顺序</th><th>一级标题 / 用途</th><th>适用产品 / 关键词</th><th>操作</th></tr></thead><tbody>${modules.map((module,index)=>`<tr data-outline-drop="library" data-id="${esc(module.id)}"><td>${outlineMoveButtons('library',module.id,index,modules.length)}</td><td><strong>${esc(module.title)}</strong><small class="block muted">${(module.children||[]).length} 个二级标题${module.children?.length?' · '+module.children.map(c=>esc(c.title)).join('、'):''}</small><small class="block muted">${esc(module.description||'暂无用途说明')}</small></td><td><span>${(module.domains||[]).map(d=>esc(domainNames[d]||d)).join('、')||'所有产品'}</span><small class="block muted">${esc((module.keywords||[]).join('、')||'暂无关键词')}</small></td><td><div class="actions">${btn('编辑','outline-module-edit',`data-id="${esc(module.id)}"`,'small')}${btn('移除','outline-module-remove-preview',`data-id="${esc(module.id)}"`,'small ghost')}</div></td></tr>`).join('')||'<tr><td colspan="4">还没有通用模块，请点击“新增模块”。</td></tr>'}</tbody></table></div></section>
    <section class="outline-preset-section"><div class="section-heading"><div><h2>常用预设</h2><p class="small muted">一个预设可以包含多个一级模块；应用时只调整通用库模块，保留招标目录的人工开关。</p></div></div><div class="outline-preset-grid">${presets.map((preset,index)=>`<section class="panel outline-preset-card"><label for="outline-preset-name-${index}">预设${outlineChineseNumber(index+1)}</label><input id="outline-preset-name-${index}" data-outline-preset-name="${esc(preset.id)}" maxlength="60" value="${esc(preset.name)}" aria-label="预设${index+1}名称"><div class="outline-preset-modules">${modules.map(module=>`<label class="checkbox-line"><input type="checkbox" data-outline-preset-module="${esc(module.id)}" data-preset-id="${esc(preset.id)}" ${(preset.module_ids||[]).includes(module.id)?'checked':''}><span>${esc(module.title)}</span></label>`).join('')||'<p class="small muted">先添加通用模块。</p>'}</div><small class="muted">已选 ${(preset.module_ids||[]).filter(id=>modules.some(m=>m.id===id)).length} 项</small></section>`).join('')}</div></section></fieldset><div id="outline-error" class="form-error" role="alert"></div>`;
}
function openOutlineModule(id){
  const module=id?(state.outlineLibrary?.modules||[]).find(m=>m.id===id):null;if(id&&!module)return;
  state.outlineModuleChildren=JSON.parse(JSON.stringify(module?.children||[]));
  openDialog(module?'编辑通用模块':'新增通用模块','先加入本页调整，点击“保存目录库”后持久保存。',`<form id="outline-module-form" data-id="${esc(id||'')}"><div class="fields"><div class="field"><label for="outline-module-title">一级目录标题</label><input id="outline-module-title" name="title" required maxlength="160" value="${esc(module?.title||'')}" placeholder="例如：实施与交付方案"></div><div class="field"><label for="outline-module-description">用途说明</label><textarea id="outline-module-description" name="description" maxlength="2000">${esc(module?.description||'')}</textarea></div><div class="field"><label>适用产品（不选表示通用）</label>${Object.entries(domainNames).map(([key,label])=>`<label class="checkbox-line"><input type="checkbox" name="domain_${key}" ${module?.domains?.includes(key)?'checked':''}>${label}</label>`).join('')}</div><div class="field"><label for="outline-module-keywords">匹配关键词</label><input id="outline-module-keywords" name="keywords" maxlength="1500" value="${esc((module?.keywords||[]).join('、'))}" placeholder="实施、培训、验收"><small>用逗号、顿号或换行分隔；仅用于推荐相关度。</small></div><div class="field"><label>二级标题与默认顺序</label><p class="small muted">这里只维护目录标题，不填写产品功能正文。新项目按此顺序提供建议。</p><div id="outline-library-children">${renderLibraryOutlineChildren()}</div>${btn('新增二级标题','outline-library-child-add','','small')}</div></div><div id="outline-module-error" class="form-error" role="alert"></div></form>`,`${btn('取消','close-dialog')}<button type="submit" form="outline-module-form" class="button primary">加入本页调整</button>`);
}
function saveOutlineModule(form){
  const values=Object.fromEntries(new FormData(form)),title=String(values.title||'').trim();if(!title)throw new Error('请填写一级目录标题。');
  const id=form.dataset.id||crypto.randomUUID(),modules=state.outlineLibrary.modules||[];
  if(modules.some(m=>m.id!==id&&outlineTitleKey(m.title)===outlineTitleKey(title)))throw new Error('目录库中已有同名模块，请编辑原有模块。');
  const previous=modules.find(m=>m.id===id)||{};const children=validatedLibraryOutlineChildren(state.outlineModuleChildren||previous.children||[]);
  const module={...previous,id,title,children,description:String(values.description||'').trim(),domains:[...(previous.domains||[]).filter(d=>!Object.hasOwn(domainNames,d)),...Object.keys(domainNames).filter(key=>form.elements['domain_'+key]?.checked)],keywords:[...new Set(String(values.keywords||'').split(/[,，、\n]+/).map(x=>x.trim()).filter(Boolean))]};
  state.outlineLibrary.modules=modules.some(m=>m.id===id)?modules.map(m=>m.id===id?module:m):[...modules,module];outlineEdited();state.dialogDirty=false;closeDialog();renderOutlineLibrary();toast('模块已加入本页调整，请保存目录库。');
}
async function saveOutlineLibrary(button){
  if(state.outlineBusy)return;const payload=JSON.parse(JSON.stringify(state.outlineLibrary)),route=state.routeToken;
  if((payload.presets||[]).some(p=>!p.name?.trim()))throw new Error('请填写三个预设的名称。');
  state.outlineBusy=true;renderOutlineLibrary();try{const result=await api('/api/outline-library',{method:'PUT',body:{revision:payload.revision,modules:payload.modules,presets:payload.presets}});if(route===state.routeToken&&state.route==='outline-library'){state.outlineLibrary=result;state.dirty=false;state.outlineEditVersion=0;}toast('通用目录库已保存，已有项目目录未被改写。');}finally{state.outlineBusy=false;if(route===state.routeToken&&state.route==='outline-library')renderOutlineLibrary();}
}
async function saveProjectOutline(button){
  if(state.outlineBusy||!state.outlineDraft)return;
  const plan=state.outlineDraft,projectId=state.projectId,route=state.routeToken,payload={revision:plan.revision,confirmed:true,groups:secondaryOutlinePayload(plan)};
  state.outlineBusy=true;rerenderOutline();try{const result=await api('/api/projects/'+encodeURIComponent(projectId)+'/outline-plan',{method:'PUT',body:payload});if(route!==state.routeToken||projectId!==state.projectId)return;state.dirty=false;setOutlineDraft(result);if(result.snapshot_id)state.outlineLastOperation={projectId,id:result.snapshot_id};await refreshProject();toast('一、二级目录已保存；新增章节为空正文，改名章节需重新批准，其余正文和历史记录保留。');}finally{state.outlineBusy=false;if(route===state.routeToken)rerenderOutline();}
}
async function previewOutlineGeneration(button){
  if(state.dirty){toast('请先保存一、二级目录。',true);return;}
  const projectId=state.projectId,route=state.routeToken;
  await busy(button,async()=>{const latest=await api('/api/projects/'+encodeURIComponent(projectId)+'/outline-plan');if(route!==state.routeToken)return;
    if(latest.revision!==state.outlineDraft?.revision){setOutlineDraft(latest);rerenderOutline();toast('目录版本已变化，已加载最新内容，请核对后再规划。',true);return;}
    const pending=new Set(latest.generation?.pending_group_ids||[]),groups=(latest.groups||[]).filter(g=>pending.has(g.id)&&g.enabled&&!outlineDuplicate(g,latest.groups));
    if(!latest.confirmed||!groups.length){toast('当前没有需要规划的新模块。');return;}
    state.outlineGenerationPreview={projectId,revision:latest.revision,route};
    openDialog('确认 AI 规划下级目录',`本次仅处理 ${groups.length} 个新增一级模块`,`${note('将调用已配置的模型，可能产生费用。只规划所列模块的二、三、四级目录，不重新生成现有正文，也不重新规划已有章节。')}<ul class="outline-generation-list">${groups.map(g=>`<li>${esc(g.title)}</li>`).join('')}</ul><p class="small muted">预计 ${esc(latest.generation?.model_requests??groups.length)} 次模型主请求；结构校验失败时可有限修复，实际进度与失败原因记录在任务中。</p><div id="outline-generation-error" class="form-error" role="alert"></div>`,`${btn('取消','close-dialog')}${btn('确认调用 AI 规划目录','outline-generate-confirm','','primary')}`);
  });
}
async function confirmOutlineGeneration(button){
  const preview=state.outlineGenerationPreview;if(!preview||preview.projectId!==state.projectId||preview.route!==state.routeToken)return;
  await busy(button,async()=>{try{const result=await api('/api/projects/'+encodeURIComponent(preview.projectId)+'/outline-plan/generate',{method:'POST',body:{revision:preview.revision,confirmed:true}});state.outlineGenerationPreview=null;trackJobs([result.job]);closeDialog();if(preview.route===state.routeToken)await refreshProject();toast('已提交新增模块的目录规划任务，完成后可进入章节编辑。');}catch(error){const el=$('#outline-generation-error');if(el)el.textContent=error.message;else toast(error.message,true);}});
}
function moveOutlineRow(kind,id,direction){
  const rows=kind==='library'?state.outlineLibrary?.modules:state.outlineDraft?.groups,visible=kind==='library'?rows:outlineVisibleGroups();if(!rows||!visible)return;
  const index=visible.findIndex(row=>row.id===id),target=visible[index+direction];if(!target)return;
  const reordered=reorderOutlineRows(rows,visible.map(row=>row.id),id,target.id);
  if(kind==='library')state.outlineLibrary.modules=reordered;else state.outlineDraft.groups=reordered;outlineEdited();rerenderOutline();
}
async function handleOutlineAction(action,button){
  const {id,value,kind}=button.dataset;if(state.outlineBusy)return;
  if(action.startsWith('outline-child-')||action.startsWith('outline-library-child-'))handleSecondaryOutlineAction(action,button);
  else if(action==='outline-filter'){state.outlineFilter=value;rerenderOutline();}
  else if(action==='outline-move')moveOutlineRow(kind,id,Number(button.dataset.direction));
  else if(action==='outline-save')await saveProjectOutline(button);
  else if(action==='outline-library-save')await saveOutlineLibrary(button);
  else if(action==='outline-module-add')openOutlineModule();
  else if(action==='outline-module-edit')openOutlineModule(id);
  else if(action==='outline-module-remove-preview'){const module=state.outlineLibrary.modules.find(m=>m.id===id);if(!module)return;openDialog('移除通用模块',module.title,note('将从本页目录库和三个预设中移除该模块。已有项目的目录快照、章节和历史记录保留；点击“保存目录库”后生效。'),`${btn('取消','close-dialog')}${btn('确认移除','outline-module-remove',`data-id="${esc(id)}"`,'primary')}`);}
  else if(action==='outline-module-remove'){state.outlineLibrary.modules=state.outlineLibrary.modules.filter(m=>m.id!==id);state.outlineLibrary.presets.forEach(p=>p.module_ids=(p.module_ids||[]).filter(x=>x!==id));outlineEdited();closeDialog();renderOutlineLibrary();}
  else if(action==='outline-generate-preview')await previewOutlineGeneration(button);
  else if(action==='outline-generate-confirm')await confirmOutlineGeneration(button);
  else if(action==='outline-enter'){if(state.dirty){toast('请先保存当前目录选择。',true);return;}if(outlineNeedsPlanning(state.project?.compilation_outline||state.outlineDraft)){toast('请先保存一、二级目录，并完成新增模块的下级目录规划。',true);return;}location.hash=`project/${state.projectId}/sections`;}
  else if(action==='outline-undo-preview')openDialog('撤销本次目录调整','只恢复本次目录字段，保留正文和其他操作。',note('后续已经调整的目录或编辑冲突会跳过，不覆盖新修改。'),`${btn('取消','close-dialog')}${btn('确认撤销','outline-undo',`data-id="${esc(id)}"`,'primary')}`);
  else if(action==='outline-undo')await busy(button,async()=>{const result=await api('/api/outline-operations/'+encodeURIComponent(id)+'/undo',{method:'POST',body:{confirmed:true}});state.outlineLastOperation=null;state.dirty=false;closeDialog();await refreshProject();const details=result.results||result.items||[];openDialog('目录撤销结果',result.message||'已完成本次撤销检查',details.length?details.map(item=>`<p>${esc(item.title||item.section_id||item.id)}：${esc(item.reason||item.message||item.status)}</p>`).join(''):note(esc(result.message||'已恢复可安全撤销的目录字段；正文和历史记录保留。')),btn('关闭','close-dialog'));});
}
document.addEventListener('input',event=>{
  const el=event.target;if(state.outlineBusy)return;if(el.dataset.outlineChildTitle){editSecondaryOutlineTitle(el);return;}if(el.dataset.outlineLibraryChildTitle){const child=(state.outlineModuleChildren||[]).find(c=>c.id===el.dataset.outlineLibraryChildTitle);if(child)child.title=el.value;state.dialogDirty=true;return;}if(el.closest('#outline-module-form')){state.dialogDirty=true;return;}
  if(el.dataset.outlinePresetName){if(state.outlineBusy)return;const preset=state.outlineLibrary?.presets?.find(p=>p.id===el.dataset.outlinePresetName);if(preset){preset.name=el.value;outlineEdited();}}
});
document.addEventListener('change',event=>{
  const el=event.target;if(state.outlineBusy)return;
  if(el.dataset.outlineChildEnabled){toggleSecondaryOutlineChild(el);}
  else if(el.dataset.outlineEnabled){const group=state.outlineDraft?.groups?.find(g=>g.id===el.dataset.outlineEnabled);if(!group||outlineDuplicate(group,state.outlineDraft.groups))return;group.enabled=el.checked;outlineEdited();rerenderOutline();}
  else if(el.id==='outline-preset'){const preset=state.outlineDraft?.presets?.find(p=>p.id===el.value);if(!preset)return;const result=applyOutlinePreset(state.outlineDraft.groups,preset);state.outlineDraft.groups=result.groups;outlineEdited();rerenderOutline();toast(`已应用“${preset.name}”，请保存目录选择。${result.skipped.length?'已跳过与招标目录同名的模块：'+result.skipped.join('、')+'。':''}`);}
  else if(el.dataset.outlinePresetModule){const preset=state.outlineLibrary?.presets?.find(p=>p.id===el.dataset.presetId);if(!preset)return;const selected=new Set(preset.module_ids||[]);el.checked?selected.add(el.dataset.outlinePresetModule):selected.delete(el.dataset.outlinePresetModule);preset.module_ids=[...selected];outlineEdited();renderOutlineLibrary();}
});
document.addEventListener('dragstart',event=>{
  const handle=event.target.closest('[data-outline-drag]');if(!handle)return;
  if(state.outlineBusy||handle.closest('fieldset')?.disabled){event.preventDefault();return;}
  state.outlineDrag={kind:handle.dataset.outlineDrag,id:handle.dataset.id,route:state.routeToken};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',handle.dataset.id);
});
document.addEventListener('dragover',event=>{const row=event.target.closest('[data-outline-drop]');if(row&&state.outlineDrag?.route===state.routeToken&&row.dataset.outlineDrop===state.outlineDrag.kind){event.preventDefault();event.dataTransfer.dropEffect='move';}});
document.addEventListener('drop',event=>{
  const row=event.target.closest('[data-outline-drop]'),drag=state.outlineDrag;if(!row||!drag||drag.route!==state.routeToken||row.dataset.outlineDrop!==drag.kind||state.outlineBusy)return;
  event.preventDefault();state.outlineDrag=null;
  const rows=drag.kind==='library'?state.outlineLibrary.modules:state.outlineDraft.groups,visible=drag.kind==='library'?rows:outlineVisibleGroups();
  const result=reorderOutlineRows(rows,visible.map(g=>g.id),drag.id,row.dataset.id);if(result===rows)return;
  if(drag.kind==='library')state.outlineLibrary.modules=result;else state.outlineDraft.groups=result;outlineEdited();rerenderOutline();
});
document.addEventListener('dragend',()=>{state.outlineDrag=null;});

/* Secondary outline nodes share the saved project definition with the editor and export. */
function outlineChildren(group){
  if(Array.isArray(group.children))return group.children;
  const sections=[...(state.project?.sections||[]),...(state.project?.retained_sections||[])];
  return (group.section_ids||[]).map(id=>{const section=sections.find(s=>s.id===id);return {id,section_id:id,title:section?.title||id,origin:'existing',enabled:true,source_refs:[]};});
}
function secondaryOutlineGroup(id){return state.outlineDraft?.groups?.find(g=>g.id===id);}
function writableOutlineChildren(group){if(!Array.isArray(group.children))group.children=outlineChildren(group);return group.children;}
function secondaryOriginLabel(origin){return {existing:'已有章节',tender:'招标明确规定',scoring:'评分细则建议',generic:'通用目录',custom:'手工添加'}[origin]||'已有目录';}
function secondaryOutlineNumbering(group,all){const parent=all.filter(g=>g.enabled&&!outlineDuplicate(g,all)).findIndex(g=>g.id===group.id)+1;let next=0;return outlineChildren(group).map(c=>({...c,display_number:parent>0&&c.enabled!==false?`${parent}.${++next}`:'未编入'}));}
function renderSecondarySourceStrategy(plan){
  const labels={explicit:'招标已完整规定目录',partial:'招标部分规定目录',scoring:'招标未规定目录，按评分细则建议',empty:'未发现明确目录或可用评分目录'},mode=plan.source_mode;
  const explanation={explicit:'沿用标书的一、二级标题与顺序；明确规定的二级标题保留原名。',partial:'保留标书已规定部分；评分细则补充建议单独展示，由您加入，不拆分已有正文。',scoring:'依据评分表第二列组织一级标题、第三列具体响应需求组织二级建议；完整评分原句可展开核对。',empty:'请手动选择通用目录，或在一级模块下新增二级标题。'};
  return `<div class="secondary-source-strategy">${note(`<strong>${esc(labels[mode]||'沿用当前编制目录')}</strong><p>${esc(explanation[mode]||plan.source_notice||'请核对现有目录和来源，再选择需要补充的模块。')}</p>${plan.completeness_reason?`<p>${esc(plan.completeness_reason)}</p>`:''}<p>招标提取 ${fmt(plan.source_count)} 个一级模块 · 评分建议 ${fmt(plan.scoring_count)} 项。通用模块默认全部关闭，由您手动选择。</p>`)}${plan.source_notices?.length?`<details class="outline-source"><summary>查看来源识别说明（${plan.source_notices.length}）</summary>${plan.source_notices.map(n=>`<p>${esc(typeof n==='string'?n:n.message||n.reason||n.quote||JSON.stringify(n))}</p>`).join('')}</details>`:''}</div>`;
}
function secondaryMoveButtons(kind,groupId,childId,index,length){return `<div class="outline-move"><span class="outline-drag" draggable="true" data-secondary-drag="${kind}" data-group="${esc(groupId)}" data-id="${esc(childId)}" aria-label="仅在当前一级模块内拖动二级标题" title="只在当前一级模块内调整顺序">⠿</span>${btn('↑',kind==='library'?'outline-library-child-move':'outline-child-move',`data-group="${esc(groupId)}" data-id="${esc(childId)}" data-direction="-1" ${index===0?'disabled':''} aria-label="上移二级标题"`,'small ghost')}${btn('↓',kind==='library'?'outline-library-child-move':'outline-child-move',`data-group="${esc(groupId)}" data-id="${esc(childId)}" data-direction="1" ${index===length-1?'disabled':''} aria-label="下移二级标题"`,'small ghost')}</div>`;}
function renderOutlineChildren(group,all){
  const children=secondaryOutlineNumbering(group,all),ids=new Set(children.map(c=>c.id)),suggestions=(group.suggested_children||[]).filter(c=>!ids.has(c.id));
  return `<div class="secondary-outline" data-secondary-group="${esc(group.id)}"><div class="secondary-outline-head"><div><h3>${esc(group.title)} · 二级目录</h3><p>同模块内独立排序与启停；停用后原正文、批准与历史保留。保存后可逐节选择产品素材。</p></div>${btn('新增自定义二级标题','outline-child-add',`data-group="${esc(group.id)}"`,'small')}</div>${!group.enabled?'<p class="small muted secondary-parent-disabled">当前一级模块未启用，以下二级章节暂不编入；设置仍会保留。</p>':''}<div class="table-wrap"><table class="secondary-outline-table"><thead><tr><th>顺序</th><th>二级标题</th><th>来源 / 原句</th><th>编入</th></tr></thead><tbody>${children.map((child,index)=>{
    const section=[...(state.project?.sections||[]),...(state.project?.retained_sections||[])].find(s=>s.id===child.section_id),renamed=section&&child.title!==section.title;
    return `<tr data-secondary-drop="project" data-group="${esc(group.id)}" data-id="${esc(child.id)}"><td>${secondaryMoveButtons('project',group.id,child.id,index,children.length)}<span class="secondary-number">${esc(child.display_number)}</span></td><td><label class="visually-hidden" for="outline-child-title-${esc(child.id)}">${esc(child.title||'新增')}二级标题</label><input id="outline-child-title-${esc(child.id)}" data-outline-child-title="${esc(child.id)}" data-group="${esc(group.id)}" value="${esc(child.title)}" maxlength="240" ${child.title_locked?'readonly aria-readonly="true"':''} placeholder="填写二级标题"><small class="secondary-title-note">${child.title_locked?'标书明确规定，标题保持原文。':child.section_id?'修改标题后本节转为草稿，需要重新批准。':'保存后建立独立空章节，不生成正文。'}</small>${section?.status==='approved'?`<small data-outline-rename-note class="${renamed?'warning-text':'muted'}">${renamed?'已批准标题将修改，保存后需要重新批准。':'当前章节已批准；仅排序或启停不改变批准状态。'}</small>`:''}${String(child.id).startsWith('new-')&&!child.section_id?btn('移除未保存标题','outline-child-remove-new',`data-group="${esc(group.id)}" data-id="${esc(child.id)}"`,'small ghost'):''}</td><td><span class="small">${esc(secondaryOriginLabel(child.origin))}</span>${child.source_refs?.length?`<details class="outline-source"><summary>查看完整来源原句</summary>${child.source_refs.map(ref=>`<p>${esc(outlineSourceDescription(ref))}</p>`).join('')}</details>`:''}${child.section_id?'<small class="block muted">沿用现有章节 ID 与正文</small>':''}</td><td><label class="checkbox-line"><input type="checkbox" data-outline-child-enabled="${esc(child.id)}" data-group="${esc(group.id)}" ${child.enabled!==false?'checked':''}><span>${child.enabled!==false?'启用':'不启用'}</span></label></td></tr>`;
  }).join('')||'<tr><td colspan="4">尚无二级标题。可添加自定义标题、加入下方建议，或保存后选择 AI 规划。</td></tr>'}</tbody></table></div>${suggestions.length?`<div class="secondary-suggestions"><h4>可加入的来源建议 <span class="small muted">${suggestions.length} 项</span></h4><p class="small muted">这些建议尚未编入。加入只新增目录，已有正文不会被拆分或覆盖。</p>${suggestions.map(child=>`<article><div><strong>${esc(child.title)}</strong><small>${esc(secondaryOriginLabel(child.origin))}${child.title_locked?' · 标书原名':''}</small>${child.source_refs?.length?`<details class="outline-source"><summary>查看完整来源原句</summary>${child.source_refs.map(ref=>`<p>${esc(outlineSourceDescription(ref))}</p>`).join('')}</details>`:''}</div>${btn('加入二级目录','outline-child-add-suggestion',`data-group="${esc(group.id)}" data-id="${esc(child.id)}"`,'small')}</article>`).join('')}</div>`:''}</div>`;
}
function secondaryOutlinePayload(plan){return (plan.groups||[]).map(group=>{
  const children=outlineChildren(group),seen=new Set();
  for(const child of children){const title=String(child.title||'').trim(),key=outlineTitleKey(title);if(!title)throw new Error(`“${group.title}”下有尚未填写的二级标题。`);if(seen.has(key))throw new Error(`“${group.title}”下有重复二级标题：${title}。`);seen.add(key);}
  return {id:group.id,enabled:!!group.enabled&&!outlineDuplicate(group,plan.groups),children:children.map(child=>({id:child.id,title:String(child.title||'').trim(),enabled:child.enabled!==false}))};
});}
function editSecondaryOutlineTitle(el){const group=secondaryOutlineGroup(el.dataset.group);if(!group)return;const child=writableOutlineChildren(group).find(c=>c.id===el.dataset.outlineChildTitle);if(!child||child.title_locked)return;child.title=el.value;outlineEdited();const section=[...(state.project?.sections||[]),...(state.project?.retained_sections||[])].find(s=>s.id===child.section_id),label=el.closest('td')?.querySelector('[data-outline-rename-note]');if(label&&section?.status==='approved'){label.textContent=child.title!==section.title?'已批准标题将修改，保存后需要重新批准。':'当前章节已批准；仅排序或启停不改变批准状态。';label.className=child.title!==section.title?'warning-text':'muted';}}
function toggleSecondaryOutlineChild(el){const group=secondaryOutlineGroup(el.dataset.group);if(!group)return;const child=writableOutlineChildren(group).find(c=>c.id===el.dataset.outlineChildEnabled);if(!child)return;child.enabled=el.checked;outlineEdited();rerenderOutline();}
function moveSecondaryChild(kind,groupId,id,direction){const group=kind==='project'?secondaryOutlineGroup(groupId):null,rows=kind==='library'?state.outlineModuleChildren:group?writableOutlineChildren(group):null;if(!rows)return;const index=rows.findIndex(c=>c.id===id),target=rows[index+direction];if(!target)return;reorderSecondaryChildren(kind,groupId,id,target.id);}
function reorderSecondaryChildren(kind,groupId,fromId,toId){const group=kind==='project'?secondaryOutlineGroup(groupId):null,rows=kind==='library'?state.outlineModuleChildren:group?writableOutlineChildren(group):null;if(!rows)return;const updated=reorderOutlineRows(rows,rows.map(c=>c.id),fromId,toId);if(updated===rows)return;if(kind==='library'){state.outlineModuleChildren=updated;state.dialogDirty=true;rerenderLibraryOutlineChildren();}else{group.children=updated;outlineEdited();rerenderOutline();}}
function renderLibraryOutlineChildren(){const children=state.outlineModuleChildren||[];return `<div class="library-secondary-list">${children.map((child,index)=>`<div class="library-secondary-row" data-secondary-drop="library" data-group="library" data-id="${esc(child.id)}">${secondaryMoveButtons('library','library',child.id,index,children.length)}<label class="visually-hidden" for="library-child-title-${esc(child.id)}">二级标题 ${index+1}</label><input id="library-child-title-${esc(child.id)}" data-outline-library-child-title="${esc(child.id)}" maxlength="240" value="${esc(child.title)}" placeholder="二级标题 ${index+1}">${btn('移除','outline-library-child-remove',`data-id="${esc(child.id)}"`,'small ghost')}</div>`).join('')||'<p class="small muted">尚无二级标题，可按实际需要新增。</p>'}</div>`;}
function rerenderLibraryOutlineChildren(){const el=$('#outline-library-children');if(el)el.innerHTML=renderLibraryOutlineChildren();}
function validatedLibraryOutlineChildren(children){if(children.length>100)throw new Error('每个一级模块最多维护 100 个二级标题。');const seen=new Set();return children.map(child=>{const title=String(child.title||'').trim(),key=outlineTitleKey(title);if(!title)throw new Error('请填写二级标题，或移除空白行。');if(seen.has(key))throw new Error('同一模块内二级标题不能重复：'+title);seen.add(key);return {id:child.id,title};});}
function handleSecondaryOutlineAction(action,button){const {id,group:groupId}=button.dataset;
  if(action==='outline-child-toggle'){state.outlineExpanded??=new Set();state.outlineExpanded.has(groupId)?state.outlineExpanded.delete(groupId):state.outlineExpanded.add(groupId);rerenderOutline();return;}
  if(action==='outline-library-child-add'){state.outlineModuleChildren??=[];if(state.outlineModuleChildren.length>=100){toast('每个一级模块最多维护 100 个二级标题。',true);return;}state.outlineModuleChildren.push({id:'new-'+crypto.randomUUID(),title:''});state.dialogDirty=true;rerenderLibraryOutlineChildren();return;}
  if(action==='outline-library-child-remove'){state.outlineModuleChildren=(state.outlineModuleChildren||[]).filter(c=>c.id!==id);state.dialogDirty=true;rerenderLibraryOutlineChildren();return;}
  if(action==='outline-library-child-move'){moveSecondaryChild('library','library',id,Number(button.dataset.direction));return;}
  const group=secondaryOutlineGroup(groupId);if(!group)return;const children=writableOutlineChildren(group);
  if(action==='outline-child-add'){children.push({id:'new-'+crypto.randomUUID(),section_id:null,title:'',origin:'custom',enabled:true,source_refs:[]});}
  else if(action==='outline-child-add-suggestion'){const source=(group.suggested_children||[]).find(c=>c.id===id);if(!source||children.some(c=>c.id===id))return;if(children.some(c=>outlineTitleKey(c.title)===outlineTitleKey(source.title))){toast('本模块已有同名二级标题，请核对现有标题与来源。',true);return;}children.push({...JSON.parse(JSON.stringify(source)),enabled:true});}
  else if(action==='outline-child-remove-new'){const child=children.find(c=>c.id===id);if(!child||child.section_id||!String(id).startsWith('new-')||child.origin!=='custom')return;group.children=children.filter(c=>c.id!==id);}
  else if(action==='outline-child-move'){moveSecondaryChild('project',groupId,id,Number(button.dataset.direction));return;}
  else return;outlineEdited();rerenderOutline();
}
document.addEventListener('dragstart',event=>{const handle=event.target.closest('[data-secondary-drag]');if(!handle)return;if(state.outlineBusy||handle.closest('fieldset')?.disabled){event.preventDefault();return;}state.secondaryDrag={kind:handle.dataset.secondaryDrag,group:handle.dataset.group,id:handle.dataset.id,route:state.routeToken};event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',handle.dataset.id);});
document.addEventListener('dragover',event=>{const row=event.target.closest('[data-secondary-drop]'),drag=state.secondaryDrag;if(!row||!drag)return;event.preventDefault();event.dataTransfer.dropEffect=!state.outlineBusy&&drag.route===state.routeToken&&drag.kind===row.dataset.secondaryDrop&&drag.group===row.dataset.group?'move':'none';});
document.addEventListener('drop',event=>{const row=event.target.closest('[data-secondary-drop]'),drag=state.secondaryDrag;if(!row||!drag)return;event.preventDefault();state.secondaryDrag=null;if(state.outlineBusy||drag.route!==state.routeToken||drag.kind!==row.dataset.secondaryDrop||drag.group!==row.dataset.group)return;reorderSecondaryChildren(drag.kind,drag.group,drag.id,row.dataset.id);});
document.addEventListener('dragend',()=>{state.secondaryDrag=null;});
/* End compilation outline. */

/* Product modules: explicit manual material selection, preview, append and undo. */
function renderKnowledgeTabs(active){return `<nav class="knowledge-tabs" aria-label="企业知识库分类"><a href="#knowledge" ${active==='documents'?'aria-current="page"':''}>企业资料</a><a href="#knowledge/modules" ${active==='modules'?'aria-current="page"':''}>产品功能模块</a></nav>`;}
function productModuleScope(scope){return ({general:'通用',archive:'会计电子档案系统',expense:'费控系统'})[scope]||scope||'通用';}
function productModuleRows(){const query=String(state.productModuleQuery||'').trim().toLocaleLowerCase();return (state.productModules?.modules||[]).filter(m=>!m.deleted_at&&(state.productModuleScope==null||state.productModuleScope==='all'||m.scope===state.productModuleScope)&&(!query||(m.title+'\n'+m.content).toLocaleLowerCase().includes(query)));}
function renderProductModules(){
  $('#main').innerHTML=`<div class="page-heading"><div><div class="eyebrow">COMPANY KNOWLEDGE</div><h1>企业知识库</h1><p>维护可重复使用的产品功能描述，按需原样放入投标章节。</p></div>${btn(`${icon('plus')}新建产品功能模块`,'product-create','','primary')}</div>${renderKnowledgeTabs('modules')}${note('模块名称与正文由您编辑。选择后原样追加到当前章节，不调用 AI。模块属于人工编写素材，企业事实仍按实际依据核对。')}<div class="toolbar product-module-toolbar"><label class="search-field">${icon('search')}<span class="visually-hidden">搜索产品功能模块</span><input id="product-module-query" value="${esc(state.productModuleQuery||'')}" placeholder="搜索模块名称或正文"></label><label class="knowledge-scope-label">产品范围 <select id="product-module-scope"><option value="all">全部产品范围</option>${Object.entries({general:'通用',archive:'会计电子档案系统',expense:'费控系统'}).map(([key,label])=>`<option value="${esc(key)}" ${state.productModuleScope===key?'selected':''}>${esc(label)}</option>`).join('')}</select></label></div><div id="product-module-list">${renderProductModuleList()}</div>`;
}
function renderProductModuleList(){const modules=productModuleRows();return modules.length?`<section class="panel"><div class="panel-head"><h2>产品功能模块 <span class="small muted">${modules.length} 份</span></h2></div><div class="product-module-grid">${modules.map(m=>`<article class="product-module-card"><div><h3>${esc(m.title)}</h3><span class="small muted">${esc(productModuleScope(m.scope))} · v${esc(m.version)} · ${fmt(m.content?.length)} 字符</span></div><p class="product-module-snippet">${esc(m.content||'暂无正文')}</p><div class="actions">${btn('预览全文','product-preview',`data-id="${esc(m.id)}"`,'small')}${btn('编辑','product-edit',`data-id="${esc(m.id)}"`,'small')}${btn('删除','product-delete-preview',`data-id="${esc(m.id)}"`,'small ghost')}</div></article>`).join('')}</div></section>`:empty((state.productModules?.modules||[]).length?'没有匹配的产品功能模块':'还没有产品功能模块','您可以手动新建模块，填写名称、适用产品范围与完整正文。模块保存后即可在章节中选择使用。',btn('新建产品功能模块','product-create','','primary'));}
async function refreshProductModules(){const route=state.routeToken,data=await api('/api/product-modules');if(route!==state.routeToken)return;state.productModules=data;if(state.route==='knowledge'&&state.knowledgeTab==='modules')renderProductModules();}
function productModuleForm(module){
  state.productModuleEdit=module?{...module}:null;
  openDialog(module?'编辑产品功能模块':'新建产品功能模块',module?`当前 v${module.version}；保存后不改写已放入章节的旧版本。`:'直接填写名称和正文，不调用模型。',`<form id="product-module-form"><div class="fields"><div class="field"><label for="product-module-title">模块名称</label><input id="product-module-title" name="title" required maxlength="160" value="${esc(module?.title||'')}" placeholder="例如：电子会计档案四性检测"></div><div class="field"><label for="product-module-form-scope">产品范围</label><select id="product-module-form-scope" name="scope">${Object.entries({general:'通用',archive:'会计电子档案系统',expense:'费控系统'}).map(([key,label])=>`<option value="${esc(key)}" ${(module?.scope||'general')===key?'selected':''}>${esc(label)}</option>`).join('')}</select></div><div class="field"><label for="product-module-content">模块正文</label><textarea class="product-module-editor" id="product-module-content" name="content" required maxlength="250000" placeholder="粘贴或编写完整功能描述，支持 Markdown 标题、列表和表格。">${esc(module?.content||'')}</textarea><small>按原文追加，不自动插入模块名称或改写内容；需要标题时，请直接写在正文中。</small></div></div><div id="product-module-error" class="form-error" role="alert"></div></form>`,`${btn('取消','close-dialog')}<button type="submit" form="product-module-form" class="button primary">保存模块</button>`,true);
}
async function saveProductModule(form,button){
  if(state.productModuleBusy||!form.reportValidity())return;
  const module=state.productModuleEdit,values=Object.fromEntries(new FormData(form)),dialog=state.dialogToken,context=saveContext(form);
  await busy(button,async()=>{state.productModuleBusy=true;let result;
    try{result=await api('/api/product-modules'+(module?'/'+encodeURIComponent(module.id):''),{method:module?'PATCH':'POST',body:{title:values.title,content:values.content,scope:values.scope,...(module?{revision:module.revision}:{})}});}
    catch(error){if(dialogIsCurrent(dialog)&&$('#product-module-error'))$('#product-module-error').textContent=error.message;else toast(error.message,true);return;}
    finally{state.productModuleBusy=false;}
    if(dialogIsCurrent(dialog)){state.productModuleEdit=result.module;if(finishSave(context))closeDialog();}toast(`产品功能模块已保存 · v${result.module?.version||''}`);
    try{await refreshProductModules();}catch(error){toast('模块已保存，列表刷新失败：'+error.message,true);}
  });
}
async function openProductModule(id,mode,button){const route=state.routeToken,dialog=state.dialogToken;await busy(button,async()=>{const module=await api('/api/product-modules/'+encodeURIComponent(id));if(route!==state.routeToken||dialog!==state.dialogToken)return;if(mode==='edit'){productModuleForm(module);return;}state.productModuleInspect=module;
  if(mode==='delete')openDialog('删除产品功能模块',module.title,note('删除后不再供新的章节选择。已经放入章节的正文及当时使用的模块版本会保留。'),`${btn('取消','close-dialog')}${btn('确认删除','product-delete-confirm',`data-id="${esc(id)}"`,'primary')}`);
  else openDialog(module.title,`${productModuleScope(module.scope)} · v${module.version} · ${fmt(module.content?.length)} 字符`,`<pre class="product-module-full">${esc(module.content)}</pre>`,`${btn('关闭','close-dialog')}${btn('编辑模块','product-edit',`data-id="${esc(id)}"`,'primary')}`,true);
});}
function productModuleSources(sectionId){const sources=state.project?.project?.metadata?.product_module_sources?.[sectionId];return Array.isArray(sources)?sources:[];}
function renderProductSectionActions(section){
  const running=(state.project?.jobs||[]).some(j=>['queued','running'].includes((state.jobs.get(j.id)||j).status)),sources=productModuleSources(section.id),operations=[...new Set(sources.map(s=>s.operation_id).filter(Boolean))];
  return `<div class="product-section-steps"><div class="product-section-step"><span class="guide-number">01</span><div><h3>先放入产品功能模块</h3><p>多选模块，预览后原样追加；当前正文保留。</p>${btn('选择产品功能模块','product-picker-open',`data-id="${esc(section.id)}" ${running?'disabled':''}`,'primary')}</div></div><div class="product-section-step"><span class="guide-number">02</span><div><h3>内容不足时使用 AI 生成本章 <span class="small muted">可选</span></h3><p>根据本章已保存正文和所选素材重新编写，确认后才调用模型。</p>${btn(`${icon('spark')}AI 生成本章`,'regenerate-section-preview',`data-id="${esc(section.id)}" ${running?'disabled':''}`,'small')}</div></div></div>${sources.length?`<details class="product-section-sources"><summary>本节已放入 ${sources.length} 份产品功能模块</summary><p class="small muted">显示放入时的版本；模块库后续编辑或删除不会替换这里的素材。</p>${sources.map(s=>`<details><summary>${esc(s.title||s.module_id)} · v${esc(s.version)} · ${esc(productModuleScope(s.scope))}</summary><pre class="product-module-full">${esc(s.content||'')}</pre></details>`).join('')}<div class="actions">${operations.map((id,i)=>btn(`撤销第 ${i+1} 次素材追加`,'product-undo-preview',`data-id="${esc(id)}" data-section="${esc(section.id)}" ${running?'disabled':''}`,'small')).join('')}</div></details>`:''}`;
}
function productPickerCurrent(picker=state.productPicker){return !!picker&&state.route==='project'&&state.projectId===picker.projectId&&state.selectedSection===picker.sectionId&&state.routeToken===picker.route;}
function applicableProductModules(modules,domain){return modules.filter(m=>!m.deleted_at&&(m.scope==='general'||m.scope===domain));}
async function openProductPicker(sectionId,button){
  if(state.dirty){toast('请先保存当前章节编辑，再选择产品功能模块。',true);return;}
  const section=state.project?.sections?.find(s=>s.id===sectionId);if(!section)return;
  const route=state.routeToken,projectId=state.projectId,domain=state.project.project.domain;
  await busy(button,async()=>{const data=await api('/api/product-modules');if(route!==state.routeToken||state.projectId!==projectId||state.selectedSection!==sectionId||state.dirty)return;state.productPicker={sectionId,projectId,domain,route,title:section.title,modules:applicableProductModules(data.modules||[],domain),selected:[],query:''};state.productPreview=null;renderProductPicker();});
}
function productPickerRows(){const p=state.productPicker,query=p.query.trim().toLocaleLowerCase();return p.modules.filter(m=>!query||(m.title+'\n'+m.content).toLocaleLowerCase().includes(query));}
function renderProductPickerList(){const p=state.productPicker,rows=productPickerRows();return rows.length?rows.map(m=>`<article class="product-picker-row"><label class="checkbox-line"><input type="checkbox" data-product-select="${esc(m.id)}" ${p.selected.includes(m.id)?'checked':''}><span><strong>${esc(m.title)}</strong><small>${esc(productModuleScope(m.scope))} · v${esc(m.version)} · ${fmt(m.content?.length)} 字符</small></span></label><details><summary>预览正文</summary><pre class="product-module-full">${esc(m.content)}</pre></details></article>`).join(''):note(p.modules.length?'没有匹配的模块。已选清单仍保留，可清空搜索查看全部。':'尚无适用模块。请到企业知识库 → 产品功能模块，手动添加通用或本项目产品范围的模块。');}
function renderProductPickerOrder(){const p=state.productPicker;return `<div class="product-picker-selection"><strong>已选 ${p.selected.length} 份 · 按以下顺序追加</strong>${p.selected.length?`<ol>${p.selected.map((id,i)=>{const m=p.modules.find(x=>x.id===id);return `<li><span>${esc(m?.title||id)} <small>v${esc(m?.version)}</small></span><div class="actions">${btn('↑','product-picker-move',`data-id="${esc(id)}" data-value="-1" ${i===0?'disabled':''}`,'small')}${btn('↓','product-picker-move',`data-id="${esc(id)}" data-value="1" ${i===p.selected.length-1?'disabled':''}`,'small')}${btn('移除','product-picker-remove',`data-id="${esc(id)}"`,'small ghost')}</div></li>`;}).join('')}</ol>`:'<p class="small muted">请勾选要放入本章的产品功能模块。</p>'}</div>`;}
function refreshProductPickerChoices(){const list=$('#product-picker-list'),order=$('#product-picker-order'),button=$('[data-action="product-append-preview"]');if(list)list.innerHTML=renderProductPickerList();if(order)order.innerHTML=renderProductPickerOrder();if(button)button.disabled=!state.productPicker.selected.length;}
function renderProductPicker(){const p=state.productPicker;if(!productPickerCurrent(p))return;state.productPreview=null;openDialog('选择产品功能模块',`仅追加到当前 1 节：${p.title}`,`${note('原有正文保持不变。勾选模块后先预览，再一次确认追加；此操作不调用 AI。')}<label class="search-field product-picker-search">${icon('search')}<span class="visually-hidden">搜索可用模块</span><input id="product-picker-query" value="${esc(p.query)}" placeholder="搜索名称或正文"></label><div class="product-picker-layout"><div id="product-picker-list">${renderProductPickerList()}</div><div id="product-picker-order">${renderProductPickerOrder()}</div></div><div id="product-picker-error" class="form-error" role="alert"></div>`,`${btn('取消','close-dialog')}${btn('清空选择','product-picker-clear')}${btn('预览追加结果','product-append-preview',p.selected.length?'':'disabled','primary')}`,true);}
async function previewProductAppend(button){
  const p=state.productPicker;if(!productPickerCurrent(p)||!p.selected.length)return;if(state.dirty){toast('请先保存本节编辑。',true);return;}
  const ids=[...p.selected],dialog=state.dialogToken;
  await busy(button,async()=>{try{const plan=await api('/api/sections/'+encodeURIComponent(p.sectionId)+'/product-modules/preview',{method:'POST',body:{module_ids:ids}});if(!productPickerCurrent(p)||dialog!==state.dialogToken||state.dirty||JSON.stringify(ids)!==JSON.stringify(p.selected))return;
    state.productPreview={...plan,module_ids:ids,projectId:p.projectId,route:p.route,request_id:crypto.randomUUID()};
    openDialog('确认追加产品功能模块',`仅当前 1 节：${plan.title} · 可追加 ${plan.appended_count} 份 · 跳过 ${plan.skipped_count} 份`,`${note(esc(plan.notice||'模块正文按所选顺序追加，已有正文不变。保存后为草稿，需重新批准。本次不调用 AI。'))}<div class="table-wrap"><table><thead><tr><th>模块 / 版本</th><th>产品范围</th><th>处理结果</th></tr></thead><tbody>${(plan.modules||[]).map(m=>`<tr><td>${esc(m.title)} · v${esc(m.version)}</td><td>${esc(productModuleScope(m.scope))}</td><td>${esc(m.reason||m.status)}</td></tr>`).join('')}</tbody></table></div><div class="product-append-comparison"><details><summary>追加前 · ${fmt(plan.before?.length)} 字符</summary><pre class="product-module-full">${esc(plan.before)}</pre></details><details open><summary>追加后 · ${fmt(plan.after?.length)} 字符（含保留的原正文）</summary><pre class="product-module-full">${esc(plan.after)}</pre></details></div><div id="product-append-error" class="form-error" role="alert"></div>`,`${btn('返回选择','product-picker-back')}${btn('取消','close-dialog')}${btn('确认追加'+plan.appended_count+'份','product-append-confirm',plan.appended_count?'':'disabled','primary')}`,true);
  }catch(error){if(dialog===state.dialogToken&&$('#product-picker-error'))$('#product-picker-error').textContent=error.message;else toast(error.message,true);}});
}
async function applyProductAppend(button){
  const plan=state.productPreview;if(!plan||state.productModuleBusy||!productPickerCurrent()||plan.section_id!==state.selectedSection)return;if(state.dirty){toast('本节出现未保存编辑，请先保存并重新预览。',true);return;}
  const dialog=state.dialogToken;await busy(button,async()=>{state.productModuleBusy=true;let result;
    try{result=await api('/api/sections/'+encodeURIComponent(plan.section_id)+'/product-modules/apply',{method:'POST',body:{module_ids:[...plan.module_ids],revision:plan.revision,request_id:plan.request_id,confirmed:true}});}
    catch(error){if(dialogIsCurrent(dialog)&&$('#product-append-error'))$('#product-append-error').textContent=error.message+'。可返回选择后重新预览；原选择清单保留。';else toast(error.message,true);return;}
    finally{state.productModuleBusy=false;}
    state.productPreview=null;state.productLastOperation={id:result.snapshot_id,sectionId:plan.section_id,projectId:plan.projectId};
    if(dialogIsCurrent(dialog))closeDialog();let refreshError='';try{if(plan.route===state.routeToken)await refreshProject(!state.dirty);}catch(error){refreshError='章节已保存，页面刷新失败，请刷新页面查看最新正文：'+error.message;}
    if(plan.route===state.routeToken&&!state.dirty)showProductAppendResult(result,plan,refreshError);else toast(result.message||'产品功能模块已追加到所选章节。');
  });
}
function showProductAppendResult(result,plan,refreshError=''){openDialog('模块追加结果',`实际追加 ${result.appended_count} 份 · 跳过 ${result.skipped_count} 份`,`${note(esc(result.message||'模块正文已原样追加，当前章节保存为草稿。'))}${refreshError?note(esc(refreshError),'warning'):''}<p>本次仅作用于：<strong>${esc(plan.title)}</strong>。可返回编辑检查内容；内容不足时，再单独选择 AI 生成本章。</p>${(result.results||[]).length?`<ul>${result.results.map(r=>`<li>${esc(r.title||r.module_id||r.id)}：${esc(r.reason||r.status)}</li>`).join('')}</ul>`:''}`,`${result.snapshot_id?btn('撤销本次追加','product-undo-preview',`data-id="${esc(result.snapshot_id)}" data-section="${esc(plan.section_id)}"`):''}${btn('返回章节编辑','close-dialog','','primary')}`,true);}
function confirmProductUndo(operationId,sectionId){if(state.dirty){toast('请先保存本节编辑。',true);return;}if(!state.project?.sections?.some(s=>s.id===sectionId))return;state.productUndo={operationId,sectionId,projectId:state.projectId,route:state.routeToken};openDialog('撤销本次模块追加','只恢复本次操作涉及的章节正文及模块来源记录。',note('如果章节后来被编辑、批准或再次生成，撤销会跳过并说明原因，保留后续修改。撤销成功后为草稿，需重新批准。'),`${btn('取消','close-dialog')}${btn('确认撤销','product-undo-confirm','','primary')}`);}
async function undoProductAppend(button){const context=state.productUndo;if(!context||state.productModuleBusy||state.dirty||context.projectId!==state.projectId)return;await busy(button,async()=>{state.productModuleBusy=true;let result;try{result=await api('/api/product-module-operations/'+encodeURIComponent(context.operationId)+'/undo',{method:'POST',body:{confirmed:true}});}catch(error){toast(error.message,true);return;}finally{state.productModuleBusy=false;}closeDialog();if(context.route===state.routeToken){try{await refreshProject(!state.dirty);}catch(error){toast('撤销检查已完成，页面刷新失败：'+error.message,true);}openDialog('模块追加撤销结果',result.message||'已完成撤销检查',note(esc(result.reason||result.message||result.status||'已完成；请查看本节最新正文。')),btn('关闭','close-dialog','','primary'));}});}
async function handleProductModuleAction(action,button){const {id,value}=button.dataset;
  if(state.productModuleBusy){toast('正在保存本次模块操作，请稍候。');return;}
  if(action==='product-create')productModuleForm(null);
  else if(action==='product-edit'||action==='product-preview'||action==='product-delete-preview')await openProductModule(id,action==='product-edit'?'edit':action==='product-preview'?'preview':'delete',button);
  else if(action==='product-delete-confirm'){const m=state.productModuleInspect;if(!m||m.id!==id||state.productModuleBusy)return;await busy(button,async()=>{state.productModuleBusy=true;try{await api('/api/product-modules/'+encodeURIComponent(id),{method:'DELETE',body:{revision:m.revision,confirmed:true}});}finally{state.productModuleBusy=false;}closeDialog();toast('模块已删除，已放入章节的正文和版本保留。');try{await refreshProductModules();}catch(error){toast('删除已完成，列表刷新失败：'+error.message,true);}});}
  else if(action==='product-picker-open')await openProductPicker(id,button);
  else if(action==='product-picker-back')renderProductPicker();
  else if(action==='product-picker-clear'){state.productPicker.selected=[];refreshProductPickerChoices();}
  else if(action==='product-picker-remove'){state.productPicker.selected=state.productPicker.selected.filter(x=>x!==id);refreshProductPickerChoices();}
  else if(action==='product-picker-move'){const ids=state.productPicker.selected,index=ids.indexOf(id),next=index+Number(value);if(index>=0&&next>=0&&next<ids.length){[ids[index],ids[next]]=[ids[next],ids[index]];refreshProductPickerChoices();}}
  else if(action==='product-append-preview')await previewProductAppend(button);
  else if(action==='product-append-confirm')await applyProductAppend(button);
  else if(action==='product-undo-preview')confirmProductUndo(id,button.dataset.section);
  else if(action==='product-undo-confirm')await undoProductAppend(button);
}
document.addEventListener('input',event=>{const el=event.target;if(el.closest('#product-module-form'))state.dialogDirty=true;if(el.id==='product-module-query'){state.productModuleQuery=el.value;$('#product-module-list').innerHTML=renderProductModuleList();}if(el.id==='product-picker-query'&&state.productPicker){state.productPicker.query=el.value;refreshProductPickerChoices();}});
document.addEventListener('change',event=>{const el=event.target;if(el.closest('#product-module-form'))state.dialogDirty=true;if(el.id==='product-module-scope'){state.productModuleScope=el.value;$('#product-module-list').innerHTML=renderProductModuleList();}if(el.dataset.productSelect&&state.productPicker){const p=state.productPicker,id=el.dataset.productSelect;if(!p.modules.some(m=>m.id===id))return;if(el.checked&&!p.selected.includes(id)){if(p.selected.length>=100){el.checked=false;toast('每次最多选择 100 份模块。',true);return;}p.selected.push(id);}else if(!el.checked)p.selected=p.selected.filter(x=>x!==id);refreshProductPickerChoices();}});
/* End product modules. */
