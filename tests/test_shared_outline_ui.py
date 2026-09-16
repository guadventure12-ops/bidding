"""Offline Node checks of the real editor functions; synthetic state, no browser/model/service."""
import json
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
    generation = source[source.index("async function previewSectionGeneration("):source.index("async function showSectionHistory(")]
    material = source[source.index("function renderIndexMaterialPlan("):source.index("async function showIndexPages(")]
    submit = source[source.index("document.addEventListener('submit',async event=>{"):source.index("document.addEventListener('input',event=>{")]
    script = r"""
const assert=require('node:assert/strict');
const state={projectId:'p',project:{sections:[
 {id:'s/1',title:'资格声明',legacy_title:'资格与商务响应（1）',content:'正文数字123及表格',status:'approved'},
 {id:'s2',title:'主体资料',legacy_title:'资格与商务响应（2）',content:'另一正文',status:'draft'},
 {id:'s3',title:'归档能力',legacy_title:'技术响应（1）',content:'档案正文',status:'draft'}],
 chapter_outline:{version:'chapter-outline-1',groups:[
 {id:'qualification',title:'资格与商务',sections:[{id:'s/1'},{id:'s2'}]},
 {id:'technical',title:'技术方案',sections:[{id:'s3'}]}]}},jobs:new Map(),selectedSection:'s/1',routeToken:1};
const dom={'#batch-all':{},'#batch-count':{},'.chapter-nav':{scrollTop:81},'#project-content':{innerHTML:'old editor'},'#section-instruction':{value:'只展开当前主题'}};
const input=(className,dataset={},checked=false,id='')=>({id,dataset,checked,classList:{contains:n=>n===className},setAttribute(k,v){this[k]=v;}});
let sectionInputs=state.project.sections.map(s=>input('batch-section',{id:s.id}));
let groupInputs=state.project.chapter_outline.groups.map(g=>input('batch-group',{group:g.id}));
let groupNodes=state.project.chapter_outline.groups.map(g=>({dataset:{outlineGroup:g.id},items:{hidden:false},button:{setAttribute(k,v){this[k]=v;}}}));
const $=(selector,root)=>root?selector==='.chapter-group-items'?root.items:root.button:dom[selector]||null;
const $$=(selector)=>selector==='.batch-section'?sectionInputs:selector==='.batch-group'?groupInputs:selector==='.chapter-group'?groupNodes:[];
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,action,attrs='')=>`<button data-action="${action}" ${attrs}>${label}</button>`;
const icon=()=>'',badge=x=>x,note=x=>x,fmt=x=>String(x||0),chapterRisk=()=>null;
const statusNames={approved:'已批准',draft:'草稿'};
const empty=(a,b)=>a+b;
let leaveAllowed=true,leaveCalls=0;const allowLeave=()=>{leaveCalls++;return leaveAllowed;};
const listeners={};const document={addEventListener:(name,callback)=>listeners[name]=callback};
class HTMLFormElement{};
class FormData{constructor(form){this.form=form;}[Symbol.iterator](){return Object.entries(this.form.values)[Symbol.iterator]();}}
const requests=[],dialogs=[],messages=[];
let mockedResponse={},finishedSaves=0;
const api=async(path,options)=>{requests.push({path,options});return typeof mockedResponse==='function'?mockedResponse(path,options):mockedResponse;};
const busy=async(button,callback)=>callback();
const toast=(...args)=>messages.push(args);
const openDialog=(...args)=>dialogs.push(args),closeDialog=()=>{};
const dialogIsCurrent=()=>true,trackJobs=()=>{},refreshProject=async()=>{};
const saveContext=form=>({form,projectId:state.projectId});
const finishProjectSave=async()=>{finishedSaves++;},saveIsCurrent=()=>true;
const crypto={randomUUID:()=> 'one-request-id'};
""" + helpers + product_actions + material + generation + submit + "\n(async()=>{\n" + body + "\n})().catch(e=>{console.error(e);process.exit(1)});"
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert result.returncode == 0, result.stdout + result.stderr


def test_hierarchy_is_backend_defined_and_leaf_titles_and_ids_are_not_guessed():
    run_js(r"""
const markup=renderSections();
assert.equal((markup.match(/class="chapter-group"/g)||[]).length,2);
assert.equal((markup.match(/class="chapter-selection"/g)||[]).length,3);
assert.ok(markup.includes('1. 资格与商务')&&markup.includes('2. 技术方案'));
assert.ok(markup.includes('id="section-group" readonly value="资格与商务"'));
assert.ok(markup.includes('id="section-title" name="title" required value="资格声明"'));
assert.ok(markup.includes('data-action="regenerate-section-preview" data-id="s/1"'));
assert.ok(markup.includes('id="section-form" data-id="s/1"'));
assert.ok(!markup.includes('资格与商务响应（1）')&&!markup.includes('技术响应（1）'));
assert.equal(state.project.sections[0].content,'正文数字123及表格');
assert.equal(state.project.sections[0].legacy_title,'资格与商务响应（1）');
state.project.chapter_outline.groups.reverse();
assert.equal(sectionOutlineGroups()[0].id,'technical');
""")


def test_category_and_global_selection_only_include_real_leaf_ids_and_mixed_state():
    run_js(r"""
state.batchSelections={p:['deleted-id']};
groupInputs[0].checked=true;updateSectionSelection(groupInputs[0]);
assert.deepEqual(state.batchSelections.p,['s/1','s2']);
assert.ok(groupInputs[0].checked&&!groupInputs[0].indeterminate);
assert.ok(!dom['#batch-all'].checked&&dom['#batch-all'].indeterminate);
sectionInputs[1].checked=false;updateSectionSelection(sectionInputs[1]);
assert.deepEqual(state.batchSelections.p,['s/1']);
assert.ok(groupInputs[0].indeterminate&&!groupInputs[0].checked);
updateSectionSelection(input('',{},true,'batch-all'));
assert.deepEqual(state.batchSelections.p,['s/1','s2','s3']);
assert.ok(dom['#batch-all'].checked&&!dom['#batch-all'].indeterminate);
groupInputs[0].checked=false;updateSectionSelection(groupInputs[0]);
assert.deepEqual(state.batchSelections.p,['s3']);
updateSectionSelection(input('',{},false,'batch-all'));
assert.deepEqual(state.batchSelections.p,[]);
assert.equal(dom['#batch-count'].textContent,'已选 0 节');
""")


def test_collapse_and_select_do_not_discard_dirty_edits_or_change_target():
    run_js(r"""
renderSections();state.dirty=true;
toggleChapterGroup('qualification');
assert.ok(groupNodes[0].items.hidden&&sectionOutlineState().collapsed.has('qualification'));
assert.equal(state.selectedSection,'s/1');assert.ok(state.dirty);
assert.equal(dom['#project-content'].innerHTML,'old editor');
leaveAllowed=false;selectSection('s2');
assert.equal(state.selectedSection,'s/1');assert.equal(dom['#project-content'].innerHTML,'old editor');
selectSection('s/1');assert.ok(state.dirty); // clicking the active leaf never resets the editor
selectSection('qualification');assert.equal(state.selectedSection,'s/1'); // a parent is not a section ID
leaveAllowed=true;selectSection('s2');
assert.equal(state.selectedSection,'s2');assert.ok(!state.dirty);
assert.ok(dom['#project-content'].innerHTML.includes('id="section-form" data-id="s2"'));
assert.ok(!sectionOutlineState().collapsed.has('qualification'));
assert.equal(dom['.chapter-nav'].scrollTop,81);
""")


def test_refresh_keeps_active_leaf_and_selection_and_renders_current_canonical_title():
    run_js(r"""
state.selectedSection='s2';state.batchSelections={p:['s2']};renderSections();
toggleChapterGroup('technical');
state.project=JSON.parse(JSON.stringify(state.project));
state.project.sections[1].title='企业主体与信用核查';state.project.sections[1].status='approved';
const markup=renderSections();syncSectionSelection();
assert.equal(state.selectedSection,'s2');assert.deepEqual(state.batchSelections.p,['s2']);
assert.ok(markup.includes('value="企业主体与信用核查"'));
assert.ok(sectionOutlineState().collapsed.has('technical'));
assert.ok(groupInputs[0].indeterminate);
state.projectId='other';assert.equal(sectionOutlineState().collapsed.size,0);
""")


def test_missing_or_partial_outline_never_invents_grouping_from_titles():
    run_js(r"""
const project=JSON.parse(JSON.stringify(state.project));delete project.chapter_outline;
project.sections[0].title='同名（1）';project.sections[1].title='同名（2）';
let groups=sectionOutlineGroups(project);assert.equal(groups.length,1);
assert.equal(groups[0].title,'章节目录');assert.equal(groups[0].sections[0].title,'同名（1）');
project.chapter_outline={groups:[{id:'custom',title:'后端指定',sections:[{id:'s2'},{id:'s2'},{id:'missing'}]}]};
groups=sectionOutlineGroups(project);assert.equal(groups[0].sections.length,1);
assert.deepEqual(groups[1].sections.map(s=>s.id),['s/1','s3']);
assert.equal(groups.flatMap(g=>g.sections).length,3);
""")


def test_single_leaf_regeneration_preview_and_confirmation_keep_outline_revision():
    run_js(r"""
mockedResponse={section_id:'s/1',title:'资格声明',requirement_count:2,notice:'现有依据',revision:'content-v1',outline_revision:'outline-v1'};
await previewSectionGeneration('s/1',{});
assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/sections/s%2F1/regeneration');
assert.ok(dialogs[0][1].includes('仅1节'));
mockedResponse={job:{id:'mock-job'}};
await confirmSectionGeneration({});
assert.equal(requests.length,2);assert.equal(requests[1].path,'/api/sections/s%2F1/regenerate');
assert.deepEqual(requests[1].options.body,{revision:'content-v1',outline_revision:'outline-v1',request_id:'one-request-id',instruction:'只展开当前主题',confirmed:true});
assert.equal(state.project.sections.length,3);
state.dirty=true;await previewSectionGeneration('s2',{});assert.equal(requests.length,2);
""")


@pytest.mark.parametrize("intent", ["draft", "approved"])
def test_save_and_approve_use_one_leaf_endpoint_not_group_or_batch(intent):
    run_js(r"""
const form=new HTMLFormElement();
form.id='section-form';form.dataset={id:'s2'};form.elements=[];
form.values={title:'主体材料',content:'当前节人工修改正文 123'};
await listeners.submit({target:form,preventDefault(){},submitter:{value:INTENT}});
assert.equal(requests.length,1);assert.equal(requests[0].path,'/api/sections/s2');
assert.equal(requests[0].options.method,'PATCH');
assert.deepEqual(requests[0].options.body,{...form.values,status:INTENT});
assert.equal(finishedSaves,1);assert.equal(state.project.sections[0].status,'approved');
""".replace("INTENT", json.dumps(intent)))


def test_titles_are_escaped_without_changing_the_business_content():
    run_js(r"""
state.project.chapter_outline.groups[0].title='<分类&>';state.project.sections[0].title='主题"<1>';
const html=renderSections();assert.ok(html.includes('&lt;分类&amp;&gt;'));
assert.ok(html.includes('value="主题&quot;&lt;1&gt;"'));
assert.equal(state.project.sections[0].title,'主题"<1>');
""")
