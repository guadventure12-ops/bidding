"""Offline tests of real secondary-directory UI rendering and handlers."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="UI handler tests require Node.js on PATH")


def run_js(body):
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    outline = source[source.index("/* Compilation outline:"):source.index("/* End compilation outline. */")]
    helpers = source[source.index("// The backend owns grouping"):source.index("function renderReview(){")]
    products = source[source.index("function productModuleSources("):source.index("function productPickerCurrent(")]
    script = r"""
const assert=require('node:assert/strict');
const state={route:'project',projectId:'p/1',routeToken:1,tab:'outline',jobs:new Map(),dirty:false,project:{jobs:[],project:{metadata:{}},sections:[{id:'s1',title:'现有主题',content:'原文123\n| A | 20 |',status:'approved'}]}};
const nodes={'#project-content':{},'#main':{},'#outline-module-error':{},'#outline-library-children':{}};
const $=s=>nodes[s]||null,$$=()=>[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,action,attrs='')=>`<button data-action="${action}" ${attrs}>${label}</button>`;
const icon=()=>'',badge=(s,t)=>t||s,note=x=>x,fmt=x=>String(x||0),empty=(a,b,c)=>a+b+c,chapterRisk=()=>null,evidenceLinks=()=>'';
const domainNames={archive:'电子档案',expense:'费控'},statusNames={approved:'已批准',draft:'草稿'};
const requests=[],messages=[],dialogs=[];let mockedResponse={},refreshes=0,closes=0;
const api=async(path,options)=>{requests.push({path,options});return typeof mockedResponse==='function'?mockedResponse(path,options):mockedResponse;};
const busy=async(b,fn)=>fn(),toast=(...args)=>messages.push(args),openDialog=(...args)=>dialogs.push(args),closeDialog=()=>{closes++;},refreshProject=async()=>{refreshes++;};
const events={};const document={addEventListener:(type,fn)=>(events[type]??=[]).push(fn)};
const fire=(type,el,extra={})=>{el.closest??=()=>null;for(const fn of events[type]||[])fn({target:el,...extra});};
let uuid=0;const crypto={randomUUID:()=>String(++uuid)},location={hash:''};
class FormData{constructor(form){this.form=form;}[Symbol.iterator](){return Object.entries(this.form.values)[Symbol.iterator]();}}
const clone=x=>JSON.parse(JSON.stringify(x));
const sample=()=>({revision:'r1',confirmed:true,source_count:1,scoring_count:1,source_mode:'partial',completeness_reason:'有一级规定，未逐项规定二级',source_notices:['评分第三列已保留完整原句'],generation:{pending_group_ids:[]},presets:[],groups:[
 {id:'g1',title:'技术方案',origin:'tender',enabled:true,section_ids:['s1'],children:[{id:'c1',section_id:'s1',title:'现有主题',origin:'existing',enabled:true,source_refs:[]},{id:'c2',section_id:null,title:'标书规定主题',origin:'tender',enabled:true,title_locked:true,source_refs:[{document_name:'采购文件',locator:'第4页',title:'规定标题',quote:'完整来源第三列文字123'}]}],suggested_children:[{id:'sg1',title:'评分建议主题',origin:'scoring',enabled:true,source_refs:[{quote:'完整评分第三列 50%，不提供不得分。'}]}]},
 {id:'g2',title:'培训',origin:'scoring',enabled:true,children:[{id:'tc',section_id:null,title:'培训安排',origin:'scoring',enabled:true,source_refs:[]}]},
 {id:'g3',title:'通用服务',origin:'generic',module_id:'m1',enabled:false,children:[{id:'lib',title:'服务计划',origin:'generic',enabled:true}]}
]});
const setup=()=>{const p=sample();setOutlineDraft(p);state.project.compilation_outline=clone(p);return state.outlineDraft;};
""" + helpers + products + outline + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-"], input=script, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_expand_second_level_shows_locked_source_and_existing_editable_title():
    run_js(r"""
setup();await handleOutlineAction('outline-child-toggle',{dataset:{group:'g1'}});const html=renderProjectOutline();
assert.ok(html.includes('data-outline-child-title="c1"'));assert.match(html,/data-outline-child-title="c2"[^>]*readonly/);
assert.ok(html.includes('完整来源第三列文字123'));assert.ok(html.includes('保存后需要重新批准')||html.includes('修改标题后本节转为草稿'));
assert.ok(html.includes('可加入的来源建议'));assert.ok(html.includes('完整评分第三列 50%'));
assert.ok(!state.dirty);assert.equal(requests.length,0);
""")


def test_origin_strategy_covers_complete_partial_scoring_empty_and_notices():
    run_js(r"""
const p=sample();for(const [mode,label] of [['explicit','完整规定'],['partial','部分规定'],['scoring','按评分细则建议'],['empty','未发现明确目录']]){const html=renderSecondarySourceStrategy({...p,source_mode:mode});assert.ok(html.includes(label));assert.ok(html.includes('通用模块默认全部关闭'));}
const html=renderSecondarySourceStrategy(p);assert.ok(html.includes('评分第三列已保留完整原句'));assert.ok(html.includes('有一级规定，未逐项规定二级'));
""")


def test_full_source_quote_is_not_hidden_when_reference_has_a_title():
    run_js(r"""
assert.equal(outlineSourceDescription({document_name:'原件',locator:'表3',title:'标题',quote:'完整第三列评分细则'}),'原件 · 表3 · 标题 · 完整第三列评分细则');
assert.equal(outlineSourceDescription({third_column:'仅第三列原句'}),'仅第三列原句');
""")


def test_scoring_filter_keeps_hidden_parents_and_child_ids_in_save_payload():
    run_js(r"""
setup();state.outlineFilter='scoring';assert.deepEqual(outlineVisibleGroups().map(g=>g.id),['g2']);
const payload=secondaryOutlinePayload(state.outlineDraft);assert.deepEqual(payload.map(g=>g.id),['g1','g2','g3']);assert.deepEqual(payload[0].children.map(c=>c.id),['c1','c2']);assert.equal(payload[2].enabled,false);
const html=renderProjectOutline();assert.ok(html.includes('评分细则建议'));assert.ok(!html.includes('data-outline-enabled="g1"'));
""")


def test_child_toggle_preserves_records_and_live_numbering_skips_disabled():
    run_js(r"""
const p=setup(),before=clone(state.project.sections);fire('change',{dataset:{outlineChildEnabled:'c1',group:'g1'},checked:false});
assert.equal(p.groups[0].children[0].enabled,false);assert.equal(p.groups[0].children.length,2);
assert.deepEqual(secondaryOutlineNumbering(p.groups[0],p.groups).map(c=>c.display_number),['未编入','1.1']);
assert.deepEqual(state.project.sections,before);assert.ok(state.dirty);assert.equal(requests.length,0);
p.groups[0].enabled=false;assert.deepEqual(secondaryOutlineNumbering(p.groups[1],p.groups).map(c=>c.display_number),['1.1']);
""")


def test_same_parent_reorder_preserves_ids_and_locked_titles():
    run_js(r"""
const p=setup(),other=clone(p.groups[1]);await handleOutlineAction('outline-child-move',{dataset:{group:'g1',id:'c2',direction:'-1'}});
assert.deepEqual(p.groups[0].children.map(c=>c.id),['c2','c1']);assert.equal(p.groups[0].children[0].title,'标书规定主题');assert.deepEqual(p.groups[1],other);
assert.deepEqual(secondaryOutlineNumbering(p.groups[0],p.groups).map(c=>c.display_number),['1.1','1.2']);
""")


def test_drag_across_parent_is_rejected_and_same_parent_is_accepted():
    run_js(r"""
const p=setup(),before=clone(p.groups);state.secondaryDrag={kind:'project',group:'g1',id:'c2',route:1};let prevented=0;
const row={dataset:{secondaryDrop:'project',group:'g2',id:'tc'}},el={closest:s=>s==='[data-secondary-drop]'?row:null};
fire('drop',el,{preventDefault:()=>{prevented++;}});assert.deepEqual(p.groups,before);assert.equal(prevented,1);assert.equal(state.secondaryDrag,null);
state.secondaryDrag={kind:'project',group:'g1',id:'c2',route:1};row.dataset.group='g1';row.dataset.id='c1';fire('drop',el,{preventDefault:()=>{prevented++;}});assert.deepEqual(p.groups[0].children.map(c=>c.id),['c2','c1']);assert.equal(prevented,2);
""")


def test_locked_title_ignores_injected_input_but_existing_title_is_editable():
    run_js(r"""
const p=setup();fire('input',{dataset:{outlineChildTitle:'c2',group:'g1'},value:'绕过标题'});assert.equal(p.groups[0].children[1].title,'标书规定主题');assert.equal(state.dirty,false);
fire('input',{dataset:{outlineChildTitle:'c1',group:'g1'},value:'改名主题'});assert.equal(p.groups[0].children[0].title,'改名主题');assert.ok(state.dirty);assert.equal(state.project.sections[0].status,'approved');assert.equal(state.project.sections[0].title,'现有主题');
""")


def test_add_custom_child_has_new_stable_id_without_network_or_body_generation():
    run_js(r"""
const p=setup();await handleOutlineAction('outline-child-add',{dataset:{group:'g1'}});const child=p.groups[0].children.at(-1);assert.equal(child.id,'new-1');assert.equal(child.origin,'custom');assert.equal(child.section_id,null);assert.ok(child.enabled);
fire('input',{dataset:{outlineChildTitle:child.id,group:'g1'},value:'手工主题'});assert.equal(secondaryOutlinePayload(p)[0].children.at(-1).title,'手工主题');assert.equal(requests.length,0);
""")


def test_remove_only_new_unsaved_custom_child_never_existing_or_source():
    run_js(r"""
const p=setup();await handleOutlineAction('outline-child-remove-new',{dataset:{group:'g1',id:'c1'}});await handleOutlineAction('outline-child-remove-new',{dataset:{group:'g1',id:'c2'}});assert.equal(p.groups[0].children.length,2);
await handleOutlineAction('outline-child-add',{dataset:{group:'g1'}});await handleOutlineAction('outline-child-remove-new',{dataset:{group:'g1',id:'new-1'}});assert.equal(p.groups[0].children.length,2);
""")


def test_source_suggestion_is_explicitly_added_once_without_touching_old_body():
    run_js(r"""
const p=setup(),before=clone(state.project.sections),source=clone(p.groups[0].suggested_children[0]);await handleOutlineAction('outline-child-add-suggestion',{dataset:{group:'g1',id:'sg1'}});await handleOutlineAction('outline-child-add-suggestion',{dataset:{group:'g1',id:'sg1'}});
assert.equal(p.groups[0].children.length,3);assert.deepEqual(p.groups[0].children[2].source_refs,source.source_refs);assert.deepEqual(state.project.sections,before);assert.equal(requests.length,0);
assert.ok(!renderOutlineChildren(p.groups[0],p.groups).includes('data-action="outline-child-add-suggestion"'));
""")


def test_duplicate_and_empty_titles_are_rejected_before_save_request():
    run_js(r"""
const p=setup();p.groups[0].children[1].title='现有主题';assert.throws(()=>secondaryOutlinePayload(p),/重复二级标题/);
p.groups[0].children[1].title='';await assert.rejects(()=>saveProjectOutline({}),/尚未填写/);assert.equal(requests.length,0);assert.ok(!state.outlineBusy);
""")


def test_save_contains_complete_explicit_child_set_and_only_directory_fields():
    run_js(r"""
const p=setup();state.outlineFilter='scoring';state.dirty=true;p.groups[0].children[0].enabled=false;mockedResponse={...clone(p),snapshot_id:'operation1'};await saveProjectOutline({});
assert.equal(requests[0].path,'/api/projects/p%2F1/outline-plan');assert.equal(requests[0].options.method,'PUT');const payload=requests[0].options.body;
assert.equal(payload.confirmed,true);assert.equal(payload.groups.length,3);assert.deepEqual(payload.groups[0].children,[{id:'c1',title:'现有主题',enabled:false},{id:'c2',title:'标书规定主题',enabled:true}]);
assert.ok(!JSON.stringify(payload).includes('content'));assert.ok(!JSON.stringify(payload).includes('source_refs'));assert.equal(requests.length,1);assert.equal(refreshes,1);assert.equal(state.outlineLastOperation.id,'operation1');
""")


def test_failed_save_preserves_child_changes_and_uncommitted_selection():
    run_js(r"""
setup();state.dirty=true;state.outlineDraft.groups[0].children[0].title='保留草稿标题';mockedResponse=()=>{throw Error('目录版本变化');};
await assert.rejects(()=>saveProjectOutline({}),/目录版本变化/);assert.ok(state.dirty);assert.equal(state.outlineDraft.groups[0].children[0].title,'保留草稿标题');assert.equal(refreshes,0);assert.equal(state.outlineBusy,false);
""")


def test_busy_save_ignores_title_toggle_and_move_events():
    run_js(r"""
const p=setup(),before=clone(p);state.outlineBusy=true;fire('input',{dataset:{outlineChildTitle:'c1',group:'g1'},value:'丢弃'});fire('change',{dataset:{outlineChildEnabled:'c1',group:'g1'},checked:false});await handleOutlineAction('outline-child-move',{dataset:{group:'g1',id:'c2',direction:'-1'}});assert.deepEqual(p,before);assert.equal(state.dirty,false);
""")


def test_legacy_group_without_children_keeps_existing_section_ids_and_titles():
    run_js(r"""
const group={id:'old',title:'原分类',enabled:true,origin:'generic',section_ids:['s1']},before=clone(group);assert.deepEqual(outlineChildren(group),[{id:'s1',section_id:'s1',title:'现有主题',origin:'existing',enabled:true,source_refs:[]}]);assert.deepEqual(group,before);
assert.equal(secondaryOutlinePayload({groups:[group]})[0].children[0].id,'s1');
""")


def test_library_children_are_ordered_editable_and_only_saved_with_library():
    run_js(r"""
state.route='outline-library';state.outlineLibrary={revision:'lib1',modules:[{id:'lib',title:'通用实施',domains:['archive'],keywords:['实施'],children:[{id:'a',title:'启动'},{id:'b',title:'交付'}]}],presets:[]};openOutlineModule('lib');assert.equal(state.outlineModuleChildren.length,2);assert.ok(dialogs.at(-1)[2].includes('二级标题与默认顺序'));
await handleOutlineAction('outline-library-child-move',{dataset:{id:'b',direction:'-1'}});fire('input',{dataset:{outlineLibraryChildTitle:'b'},value:'验收交付'});
saveOutlineModule({dataset:{id:'lib'},values:{title:'通用实施',description:'说明',keywords:'实施'},elements:{domain_archive:{checked:true}}});
assert.deepEqual(state.outlineLibrary.modules[0].children,[{id:'b',title:'验收交付'},{id:'a',title:'启动'}]);assert.equal(requests.length,0);assert.ok(state.dirty);
mockedResponse=clone(state.outlineLibrary);await saveOutlineLibrary({});assert.equal(requests[0].options.body.modules[0].children[0].id,'b');assert.equal(requests.length,1);
""")


def test_library_missing_children_empty_add_remove_and_validation():
    run_js(r"""
state.outlineLibrary={modules:[{id:'old',title:'旧模块'}],presets:[]};openOutlineModule('old');assert.deepEqual(state.outlineModuleChildren,[]);
await handleOutlineAction('outline-library-child-add',{dataset:{}});assert.equal(state.outlineModuleChildren[0].id,'new-1');assert.throws(()=>validatedLibraryOutlineChildren(state.outlineModuleChildren),/填写二级标题/);
await handleOutlineAction('outline-library-child-remove',{dataset:{id:'new-1'}});assert.deepEqual(state.outlineModuleChildren,[]);
assert.throws(()=>validatedLibraryOutlineChildren([{id:'a',title:'重复'},{id:'b',title:'重复'}]),/不能重复/);
""")


def test_preset_retains_ordered_library_children_without_auto_enabling_all_generics():
    run_js(r"""
const p=setup(),before=clone(p.groups[2].children);const applied=applyOutlinePreset(p.groups,{module_ids:['m1']});assert.equal(applied.groups[2].enabled,true);assert.deepEqual(applied.groups[2].children,before);assert.equal(p.groups[2].enabled,false);assert.equal(requests.length,0);
""")


def test_source_and_custom_titles_quotes_escape_html():
    run_js(r"""
const p=setup();p.groups[0].children[0].title='<img src=x onerror=1>';p.groups[0].suggested_children[0].source_refs[0].quote='<script>bad</script>';const html=renderOutlineChildren(p.groups[0],p.groups);
assert.ok(html.includes('&lt;img'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(!html.includes('<script>'));assert.ok(!html.includes('<img'));
""")


def test_single_section_title_source_lock_without_disabling_body_or_materials():
    run_js(r"""
setup();state.project.compilation_outline.groups[0].children[0].title_locked=true;state.project.chapter_outline={groups:[{id:'g1',title:'技术方案',sections:[{id:'s1'}]}]};state.selectedSection='s1';
assert.equal(sectionTitleLocked('s1'),true);let html=renderSections();assert.match(html,/id="section-title"[^>]*readonly/);assert.ok(html.includes('招标规定标题'));assert.ok(html.includes('data-action="product-picker-open"'));assert.ok(html.includes('data-action="regenerate-section-preview"'));assert.match(html,/id="section-content"[^>]*class="chapter-editor"/);
state.project.compilation_outline.groups[0].children[0].title_locked=false;html=renderSections();assert.ok(!/id="section-title"[^>]*readonly/.test(html));
""")


def test_existing_source_titles_not_replanned_or_regenerated_by_directory_save():
    run_js(r"""
const p=setup(),original=clone(state.project.sections);mockedResponse=clone(p);await saveProjectOutline({});assert.deepEqual(state.project.sections,original);assert.equal(requests.length,1);assert.ok(!requests.some(r=>/generate|regenerate|product-modules/.test(r.path)));assert.equal(state.jobs.size,0);
""")
