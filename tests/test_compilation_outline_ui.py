"""Exercise actual outline UI functions with synthetic state. No server or model calls."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="UI handler tests require Node.js on PATH")


def run_js(body):
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    helpers = source[source.index("// The backend owns grouping"):source.index("function renderReview(){")]
    product_actions = source[source.index("function productModuleSources("):source.index("function productPickerCurrent(")]
    material = source[source.index("function renderIndexMaterialPlan("):source.index("async function showIndexPages(")]
    generation = source[source.index("async function previewSectionGeneration("):source.index("async function showSectionHistory(")]
    module = source[source.index("/* Compilation outline:"):source.index("/* End compilation outline. */")]
    script = r"""
const assert=require('node:assert/strict');
const state={route:'project',projectId:'project/1',routeToken:1,tab:'outline',jobs:new Map(),project:{jobs:[],sections:[]},dirty:false};
const nodes={'#main':{},'#project-content':{},'#outline-error':{},'#outline-generation-error':{},'#outline-module-error':{}};
const $=(selector)=>nodes[selector]||null;
const $$=()=>[],chapterRisk=()=>null,evidenceLinks=()=>'',allowLeave=()=>true,dialogIsCurrent=()=>true;
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,action,attrs='')=>`<button data-action="${action}" ${attrs}>${label}</button>`;
const icon=()=>'',badge=(s,label)=>`<span>${esc(label||s)}</span>`,note=x=>x,fmt=x=>String(x||0),empty=(a,b,c)=>a+b+c;
const domainNames={archive:'会计电子档案系统',expense:'费控系统'},statusNames={approved:'已批准',draft:'草稿'};
const events={};const document={addEventListener:(type,listener)=>(events[type]??=[]).push(listener)};
const requests=[],dialogs=[],messages=[];let closes=0,refreshes=0,mockedResponse={};
const api=async(path,options)=>{requests.push({path,options});return typeof mockedResponse==='function'?mockedResponse(path,options):mockedResponse;};
const busy=async(button,action)=>action();
const openDialog=(...args)=>dialogs.push(args),closeDialog=()=>{closes++;};
const toast=(...args)=>messages.push(args);
const trackJobs=jobs=>jobs.forEach(j=>{if(j?.id)state.jobs.set(j.id,j);});
const refreshProject=async()=>{refreshes++;};
const location={hash:''};const crypto={randomUUID:()=> 'new-id'};
class FormData{constructor(form){this.form=form;}[Symbol.iterator](){return Object.entries(this.form.values)[Symbol.iterator]();}}
const fire=(type,target,extra={})=>{for(const listener of events[type]||[])listener({target,...extra});};
const clone=value=>JSON.parse(JSON.stringify(value));
const sample=()=>({revision:'plan-v1',confirmed:false,source_count:1,source_notice:'原文位置已提取',groups:[
 {id:'t1',title:'技术方案',origin:'tender',enabled:true,section_ids:['s1'],existing_count:1,source_refs:[{document_name:'采购原件',locator:'第4章'}]},
 {id:'m1',module_id:'implementation',title:'实施方案',origin:'generic',enabled:false,relevance:48,matched_keywords:['实施']},
 {id:'t2',title:'培训',origin:'tender',enabled:true},
 {id:'m2',module_id:'service',title:'服务',origin:'generic',enabled:false,relevance:20},
 {id:'mdup',module_id:'duplicate',title:'一、技术方案',origin:'generic',enabled:false},
 {id:'legacy',title:'既有独立目录',origin:'generic',enabled:true,existing_only:true}
],presets:[{id:'p1',name:'预设一',module_ids:['implementation','duplicate']}],generation:{pending_group_ids:[],model_requests:0},fixed_groups:[],retained_count:0});
""" + helpers + product_actions + module + material + generation + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-"], input=script, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_filtered_reorder_preserves_hidden_positions_and_all_ids():
    run_js(r"""
const plan=sample(),original=clone(plan.groups);
const reordered=reorderOutlineRows(plan.groups,['m1','m2','mdup','legacy'],'m2','m1');
assert.deepEqual(reordered.map(g=>g.id),['t1','m2','t2','m1','mdup','legacy']);
assert.deepEqual(plan.groups,original);
assert.deepEqual(reorderOutlineRows(plan.groups,['m1','m2'],'t1','m2'),plan.groups);
assert.equal(new Set(reordered.map(g=>g.id)).size,plan.groups.length);
setOutlineDraft(plan);state.outlineFilter='generic';moveOutlineRow('project','m2',-1);
assert.deepEqual(state.outlineDraft.groups.map(g=>g.id),['t1','m2','t2','m1','mdup','legacy']);
assert.ok(state.dirty);assert.equal(state.outlineFilter,'generic');
""")


def test_numbering_updates_immediately_using_enabled_global_order():
    run_js(r"""
const plan=sample();plan.groups[1].enabled=true;
assert.deepEqual(outlineNumberedGroups(plan.groups).map(g=>g.display_number),['一、','二、','三、','未启用','未启用','四、']);
const ordered=reorderOutlineRows(plan.groups,plan.groups.map(g=>g.id),'t2','t1');
const labels=outlineNumberedGroups(ordered);assert.equal(labels[0].title,'培训');assert.equal(labels[0].display_number,'一、');
assert.equal(outlineChineseNumber(10),'十');assert.equal(outlineChineseNumber(21),'二十一');
assert.equal(outlineChineseNumber(100),'一百');assert.equal(outlineChineseNumber(105),'一百零五');
assert.equal(plan.groups[1].title,'实施方案');
""")


def test_preset_preserves_tender_decisions_and_skips_duplicate_titles():
    run_js(r"""
const plan=sample();plan.groups[0].enabled=false;plan.groups[3].enabled=true;
const result=applyOutlinePreset(plan.groups,plan.presets[0]);
assert.equal(result.groups[0].enabled,false); // Applying preset never changes human tender switches.
assert.equal(result.groups[1].enabled,true);assert.equal(result.groups[3].enabled,false);
assert.equal(result.groups[4].enabled,false);assert.equal(result.groups[5].enabled,true);
assert.deepEqual(result.skipped,['一、技术方案']);assert.equal(plan.groups[1].enabled,false);
assert.deepEqual(result.groups[0].source_refs,plan.groups[0].source_refs);
""")


def test_no_source_defaults_stay_off_and_markup_escapes_all_titles():
    run_js(r"""
const plan=sample();plan.source_count=0;plan.source_notice='';plan.groups=plan.groups.filter(g=>g.module_id);plan.groups[0].title='<script>alert(1)</script>';plan.presets[0].name='"<预设>';
setOutlineDraft(plan);const html=renderProjectOutline();
assert.ok(html.includes('默认全部关闭'));assert.ok(html.includes('已启用 0 个'));
assert.ok(html.includes('&lt;script&gt;alert(1)&lt;/script&gt;'));assert.ok(!html.includes('<script>'));
assert.ok(html.includes('&quot;&lt;预设&gt;'));assert.ok(html.includes('关键词相关度 48'));
assert.ok(!state.outlineDraft.groups.some(g=>g.enabled));
""")


def test_filter_changes_do_not_drop_hidden_selection_from_saved_request():
    run_js(r"""
const plan=sample();setOutlineDraft(plan);state.outlineDraft.groups[1].enabled=true;state.dirty=true;
await handleOutlineAction('outline-filter',{dataset:{value:'generic'}});
assert.equal(outlineVisibleGroups().length,4);assert.ok(state.dirty);
mockedResponse={...plan,confirmed:true,revision:'v2',snapshot_id:'operation-1'};
await saveProjectOutline({});assert.equal(requests.length,1);
assert.equal(requests[0].path,'/api/projects/project%2F1/outline-plan');
assert.deepEqual(requests[0].options.body.groups.map(g=>g.id),plan.groups.map(g=>g.id));
assert.equal(requests[0].options.body.groups.find(g=>g.id==='t1').enabled,true);
assert.equal(requests[0].options.body.groups.find(g=>g.id==='m1').enabled,true);
assert.equal(requests[0].options.body.revision,'plan-v1');assert.equal(requests[0].options.body.confirmed,true);
assert.equal(state.outlineLastOperation.id,'operation-1');assert.ok(!state.dirty);
assert.equal(refreshes,1);assert.ok(!requests.some(r=>r.path.endsWith('/generate')));
""")


def test_save_failure_keeps_local_selection_and_is_not_optimistic():
    run_js(r"""
setOutlineDraft(sample());state.dirty=true;state.outlineDraft.groups[1].enabled=true;
mockedResponse=()=>{throw new Error('版本已变化');};
await assert.rejects(()=>saveProjectOutline({}),/版本已变化/);
assert.ok(state.dirty);assert.ok(state.outlineDraft.groups[1].enabled);assert.ok(!state.outlineBusy);
assert.equal(refreshes,0);assert.equal(messages.length,0);
""")


def test_generation_is_preview_then_explicit_confirm_and_targets_only_new_modules():
    run_js(r"""
const plan=sample();plan.confirmed=true;plan.groups[1].enabled=true;plan.generation={pending_group_ids:['m1'],model_requests:1};
setOutlineDraft(plan);mockedResponse=plan;
await previewOutlineGeneration({});assert.equal(requests.length,1);assert.ok(!requests[0].options);
assert.equal(dialogs.length,1);assert.ok(dialogs[0][2].includes('<li>实施方案</li>'));
assert.ok(!dialogs[0][2].includes('<li>技术方案</li>'));assert.ok(dialogs[0][2].includes('可能产生费用'));
mockedResponse={job:{id:'job-1',mode:'outline',status:'queued'}};
await confirmOutlineGeneration({});assert.equal(requests.length,2);
assert.equal(requests[1].path,'/api/projects/project%2F1/outline-plan/generate');
assert.deepEqual(requests[1].options.body,{revision:'plan-v1',confirmed:true});
assert.equal(state.jobs.get('job-1').mode,'outline');assert.equal(state.outlineGenerationPreview,null);
await confirmOutlineGeneration({});assert.equal(requests.length,2);
""")


def test_dirty_or_changed_revision_prevents_unpreviewed_generation():
    run_js(r"""
const plan=sample();plan.confirmed=true;setOutlineDraft(plan);state.dirty=true;
await previewOutlineGeneration({});assert.equal(requests.length,0);assert.equal(dialogs.length,0);
state.dirty=false;mockedResponse={...plan,revision:'new-revision'};
await previewOutlineGeneration({});assert.equal(dialogs.length,0);assert.equal(requests.length,1);
assert.equal(state.outlineDraft.revision,'new-revision');assert.ok(messages.some(m=>m[0].includes('版本已变化')));
""")


def test_editor_entry_requires_selection_and_planning_but_legacy_is_not_forced():
    run_js(r"""
state.project.compilation_outline=sample();await handleOutlineAction('outline-enter',{dataset:{}});assert.equal(location.hash,'');
state.project.compilation_outline.confirmed=true;state.project.compilation_outline.generation.pending_group_ids=['m1'];
await handleOutlineAction('outline-enter',{dataset:{}});assert.equal(location.hash,'');
state.project.compilation_outline.generation.pending_group_ids=[];
await handleOutlineAction('outline-enter',{dataset:{}});assert.equal(location.hash,'project/project/1/sections');
assert.equal(outlineNeedsPlanning(null),false);assert.ok(projectGenerationAction().includes('进入章节编辑'));
""")


def test_retained_content_and_suboutline_use_actual_records_without_mutation():
    run_js(r"""
const retained={id:'old',title:'原目录',content:'批准过的正文 123 | 参数 | <值>',status:'approved'};
state.project.retained_sections=[retained];const original=clone(retained);
const html=renderRetainedSections();assert.ok(html.includes('未编入'));assert.ok(html.includes('已批准'));
assert.ok(html.includes('批准过的正文 123 | 参数 | &lt;值&gt;'));assert.ok(html.includes('重新启用'));
assert.deepEqual(retained,original);assert.ok(!html.includes('data-action="regenerate-section-preview"'));
const section={id:'s1',title:'实施',spec:{suboutline:[{id:'third',title:'三级<主题>',children:[{id:'fourth',title:'四级参数'}]}]}};
const children=renderSectionChildren(section,'2.1');assert.ok(children.includes('2.1.1 三级&lt;主题&gt;'));assert.ok(children.includes('2.1.1.1 四级参数'));
delete section.spec;state.project.chapter_outline={groups:[{sections:[{id:'s1',children:[{title:'后端下级'}]}]}]};assert.equal(sectionSuboutline(section)[0].title,'后端下级');
""")


def test_editor_renders_shared_three_four_levels_and_only_real_leaf_operations():
    run_js(r"""
state.project={sections:[{id:'s1',title:'当前主题',content:'正文123',status:'draft',spec:{suboutline:[{title:'方案原则',children:[{title:'具体参数'}]}]}},{id:'s2',title:'另一个主题',content:'不可覆盖',status:'approved'}],chapter_outline:{groups:[{id:'g1',title:'一级方案',sections:[{id:'s1'},{id:'s2'}]}]},compilation_outline:{confirmed:true,groups:[]},jobs:[]};
state.selectedSection='s1';const before=clone(state.project.sections);const html=renderSections();
assert.ok(html.includes('一、 一级方案'));assert.ok(html.includes('1.1.1 方案原则'));assert.ok(html.includes('1.1.1.1 具体参数'));
assert.equal((html.match(/class="chapter-selection"/g)||[]).length,2);
assert.ok(html.includes('id="section-form" data-id="s1"'));assert.ok(html.includes('data-action="regenerate-section-preview" data-id="s1"'));
assert.ok(!html.includes('data-id="具体参数"'));assert.deepEqual(state.project.sections,before);
""")


def test_single_section_regeneration_keeps_id_and_revision_after_deeper_outline_added():
    run_js(r"""
state.selectedSection='s/1';state.project.sections=[{id:'s/1',title:'当前主题',content:'当前原文',status:'approved'},{id:'s2',title:'其他节',content:'不要修改',status:'approved'}];
nodes['#section-instruction']={value:'按三级目录展开'};
mockedResponse={section_id:'s/1',title:'当前主题',revision:'content-2',outline_revision:'outline-3',notice:'当前来源',requirement_count:2};
await previewSectionGeneration('s/1',{});assert.ok(dialogs[0][1].includes('仅1节'));
mockedResponse={job:{id:'section-job',mode:'generate'}};await confirmSectionGeneration({});
assert.equal(requests[0].path,'/api/sections/s%2F1/regeneration');assert.equal(requests[1].path,'/api/sections/s%2F1/regenerate');
assert.deepEqual(requests[1].options.body,{revision:'content-2',outline_revision:'outline-3',request_id:'new-id',instruction:'按三级目录展开',confirmed:true});
assert.equal(state.project.sections[1].content,'不要修改');assert.equal(state.project.sections[1].status,'approved');
""")


def test_library_render_has_three_editable_presets_and_add_is_local_until_save():
    run_js(r"""
state.route='outline-library';state.outlineLibrary={revision:'lib-v1',modules:[],presets:[1,2,3].map(i=>({id:'p'+i,name:'预设'+i,module_ids:[]}))};
renderOutlineLibrary();assert.equal((nodes['#main'].innerHTML.match(/data-outline-preset-name=/g)||[]).length,3);
const form={dataset:{id:''},values:{title:'交付方案',description:'交付',keywords:'实施、培训,实施'},elements:{domain_archive:{checked:true}}};
saveOutlineModule(form);assert.equal(state.outlineLibrary.modules.length,1);assert.equal(requests.length,0);assert.ok(state.dirty);
assert.deepEqual(state.outlineLibrary.modules[0].keywords,['实施','培训']);assert.deepEqual(state.outlineLibrary.modules[0].domains,['archive']);
const result=clone(state.outlineLibrary);result.revision='lib-v2';mockedResponse=result;
await saveOutlineLibrary({});assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/outline-library');assert.equal(requests[0].options.method,'PUT');
assert.equal(requests[0].options.body.presets.length,3);assert.equal(state.outlineLibrary.revision,'lib-v2');assert.ok(!state.dirty);
""")


def test_busy_save_ignores_control_changes_and_preserves_other_route_state():
    run_js(r"""
setOutlineDraft(sample());state.outlineBusy=true;
fire('change',{dataset:{outlineEnabled:'m1'},checked:true});assert.equal(state.outlineDraft.groups[1].enabled,false);
await handleOutlineAction('outline-filter',{dataset:{value:'generic'}});assert.equal(state.outlineFilter,'all');
state.outlineBusy=false;state.dirty=true;
mockedResponse=async()=>{state.routeToken=2;state.projectId='other';state.dirty=true;return {...sample(),confirmed:true};};
await saveProjectOutline({});assert.equal(state.projectId,'other');assert.ok(state.dirty);assert.equal(refreshes,0);
""")


def test_undo_preview_never_writes_until_explicit_confirmation():
    run_js(r"""
await handleOutlineAction('outline-undo-preview',{dataset:{id:'op/1'}});
assert.equal(requests.length,0);assert.equal(dialogs.length,1);assert.ok(dialogs[0][2].includes('后续已经调整'));
mockedResponse={message:'一项已恢复，一项后续编辑跳过',items:[{title:'实施',reason:'有后续编辑，跳过'}]};
await handleOutlineAction('outline-undo',{dataset:{id:'op/1'}});
assert.equal(requests[0].path,'/api/outline-operations/op%2F1/undo');assert.deepEqual(requests[0].options.body,{confirmed:true});
assert.ok(dialogs[1][2].includes('有后续编辑，跳过'));
""")


def test_routes_sidebar_and_five_workflow_stages_are_wired():
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert html.index('href="#knowledge"') < html.index('href="#outline-library"') < html.index('href="#settings"')
    assert "['analyze','requirements','outline','sections','review'].includes(bits[2])" in source
    assert "state.tab==='outline'?renderProjectOutline()" in source
    assert "if(form.id==='outline-module-form')" in source
