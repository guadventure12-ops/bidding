"""Run actual product module UI handlers with Node and an isolated mock DOM/API."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="UI handler tests require Node.js on PATH")


def run_js(body):
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    modules = source[source.index("/* Product modules:"):source.index("/* End product modules. */")]
    action = source[source.index("function projectGenerationAction("):source.index("function setOutlineDraft(")]
    route = source[source.index("function routeInfo("):source.index("function allowLeave(")]
    script = r"""
const assert=require('node:assert/strict');
const state={route:'project',projectId:'p',routeToken:1,dialogToken:0,tab:'sections',dirty:false,jobs:new Map(),selectedSection:'s/1',project:{project:{id:'p',domain:'archive',metadata:{}},sections:[{id:'s/1',title:'归档技术',content:'原文123\n\n| 项 | 值 |\n| - | - |\n| A | 20 |',status:'approved'},{id:'s2',title:'其他主题',content:'不可改变'}],jobs:[],compilation_outline:{confirmed:true}}};
const nodes={'#main':{},'#product-module-list':{},'#product-module-error':{},'#product-picker-error':{},'#product-picker-list':{},'#product-picker-order':{},'[data-action="product-append-preview"]':{},'#product-append-error':{}};
const $=(selector)=>nodes[selector]||null,$$=()=>[];
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const btn=(label,action,attrs='')=>`<button data-action="${action}" ${attrs}>${label}</button>`;
const icon=()=>'',note=x=>x,fmt=x=>String(x||0),empty=(a,b,c)=>a+b+c;
const scopeNames={general:'通用',archive:'会计电子档案',expense:'费控'};
const requests=[],dialogs=[],messages=[];let closes=0,refreshes=0,mockedResponse={},refreshMock=null,finishResult=true;
const api=async(path,options)=>{requests.push({path,options});return typeof mockedResponse==='function'?mockedResponse(path,options):mockedResponse;};
const busy=async(button,fn)=>fn(),toast=(...args)=>messages.push(args);
const openDialog=(...args)=>{dialogs.push(args);state.dialogToken++;return state.dialogToken;},closeDialog=()=>{closes++;state.dialogToken++;};
const dialogIsCurrent=token=>token===state.dialogToken;
const refreshProject=async()=>{refreshes++;if(refreshMock)await refreshMock();};
const saveContext=form=>({form}),finishSave=()=>{if(finishResult)state.dialogDirty=false;return finishResult;};
const outlineNeedsPlanning=plan=>!plan?.confirmed;
const crypto={randomUUID:()=> 'request-1'},location={hash:'#knowledge/modules'};
const listeners={};const document={addEventListener:(name,fn)=>(listeners[name]??=[]).push(fn)};
const fire=(name,el)=>{el.closest??=()=>null;for(const fn of listeners[name]||[])fn({target:el});};
class FormData{constructor(form){this.form=form;}[Symbol.iterator](){return Object.entries(this.form.values)[Symbol.iterator]();}}
const sampleModules=()=>[
 {id:'m/1',title:'四性检测',scope:'archive',content:'### 检测\n正文123',version:2,revision:'r1'},
 {id:'m2',title:'平台能力',scope:'general',content:'| A | 20 |',version:1,revision:'r2'},
 {id:'m3',title:'报销',scope:'expense',content:'报销正文',version:1,revision:'r3'},
 {id:'removed',title:'已删除',scope:'general',content:'旧内容',version:1,deleted_at:'2026-01-01'}];
const picker=()=>({sectionId:'s/1',projectId:'p',domain:'archive',route:1,title:'归档技术',modules:sampleModules().slice(0,2),selected:['m/1','m2'],query:''});
const preview=()=>({section_id:'s/1',title:'归档技术',module_ids:['m/1','m2'],revision:'preview-r1',before:'原文123',after:'原文123\n\n### 检测\n正文123\n\n| A | 20 |',modules:sampleModules().slice(0,2).map(m=>({...m,status:'append',reason:'可追加'})),appended_count:2,skipped_count:0});
""" + modules + action + route + "\n(async()=>{\n" + body + "\n})().catch(error=>{console.error(error);process.exit(1)});"
    result = subprocess.run([NODE, "-"], input=script, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr


def test_knowledge_routes_and_tabs_keep_enterprise_documents_separate():
    run_js(r"""
assert.equal(routeInfo().knowledgeTab,'modules');location.hash='#knowledge';assert.equal(routeInfo().knowledgeTab,'documents');
assert.match(renderKnowledgeTabs('documents'),/href="#knowledge" aria-current="page"/);
assert.match(renderKnowledgeTabs('modules'),/href="#knowledge\/modules" aria-current="page"/);
""")


def test_module_list_filters_scope_and_searches_body_without_deleted_records():
    run_js(r"""
state.productModules={modules:sampleModules()};assert.equal(productModuleRows().length,3);
state.productModuleScope='archive';assert.deepEqual(productModuleRows().map(m=>m.id),['m/1']);
state.productModuleScope='all';state.productModuleQuery='20';assert.deepEqual(productModuleRows().map(m=>m.id),['m2']);
""")


def test_module_list_and_editor_escape_html_and_preserve_markdown():
    run_js(r"""
const m={...sampleModules()[0],title:'<script>x</script>',content:'<img src=x onerror=alert(1)>\n| A | 20 |'};
state.productModules={modules:[m]};const html=renderProductModuleList();assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script&gt;'));
productModuleForm(m);assert.ok(dialogs.at(-1)[2].includes('&lt;img'));assert.ok(dialogs.at(-1)[2].includes('| A | 20 |'));
assert.ok(dialogs.at(-1)[2].includes('maxlength="250000"'));assert.equal(requests.length,0);
""")


def test_empty_module_library_guides_manual_creation():
    run_js(r"""
state.productModules={modules:[]};renderProductModules();const html=nodes['#main'].innerHTML;
assert.ok(html.includes('手动新建'));assert.ok(html.includes('data-action="product-create"'));assert.ok(!html.includes('data-mode="generate"'));
""")


def test_create_saves_manual_content_then_reloads_library_without_model_request():
    run_js(r"""
const form={reportValidity:()=>true,values:{title:'手写标题',content:'正文\n| A | 2 |',scope:'general'}};state.productModuleEdit=null;
mockedResponse=(path,opt)=>opt?{module:{id:'new',version:1,revision:'new-r'}}:{modules:[]};await saveProductModule(form,{});
assert.deepEqual(requests[0],{path:'/api/product-modules',options:{method:'POST',body:form.values}});
assert.equal(requests[1].path,'/api/product-modules');assert.equal(requests.length,2);assert.equal(closes,1);assert.equal(state.productModuleBusy,false);
""")


def test_edit_uses_previewed_revision_and_preserves_inflight_new_edits():
    run_js(r"""
state.productModuleEdit=sampleModules()[0];state.dialogDirty=true;finishResult=false;
const form={reportValidity:()=>true,values:{title:'新标题',content:'新正文',scope:'archive'}};
mockedResponse=(path,opt)=>opt?{module:{id:'m/1',version:3,revision:'r3'}}:{modules:[]};await saveProductModule(form,{});
assert.equal(requests[0].path,'/api/product-modules/m%2F1');assert.equal(requests[0].options.method,'PATCH');assert.equal(requests[0].options.body.revision,'r1');
assert.equal(closes,0);assert.equal(state.dialogDirty,true);assert.equal(state.productModuleEdit.revision,'r3');
""")


def test_failed_edit_keeps_dirty_draft_and_displays_real_error():
    run_js(r"""
state.productModuleEdit=sampleModules()[0];state.dialogDirty=true;mockedResponse=()=>{throw Error('版本已改变');};
await saveProductModule({reportValidity:()=>true,values:{title:'未保存',content:'保留正文',scope:'archive'}},{});
assert.equal(closes,0);assert.equal(state.dialogDirty,true);assert.equal(nodes['#product-module-error'].textContent,'版本已改变');assert.equal(state.productModuleBusy,false);
""")


def test_delete_confirms_and_preserves_used_content_notice():
    run_js(r"""
mockedResponse=sampleModules()[0];await openProductModule('m/1','delete',{});
assert.ok(dialogs.at(-1)[2].includes('正文及当时使用的模块版本会保留'));assert.equal(requests.length,1);
mockedResponse={modules:[]};await handleProductModuleAction('product-delete-confirm',{dataset:{id:'m/1'}});
assert.equal(requests[1].options.method,'DELETE');assert.deepEqual(requests[1].options.body,{revision:'r1',confirmed:true});
""")


def test_picker_blocks_unsaved_body_before_any_request():
    run_js(r"""
state.dirty=true;await openProductPicker('s/1',{});assert.equal(requests.length,0);assert.match(messages[0][0],/先保存/);
""")


def test_picker_only_offers_general_and_project_product_scope():
    run_js(r"""
mockedResponse={modules:sampleModules()};await openProductPicker('s/1',{});
assert.deepEqual(state.productPicker.modules.map(m=>m.id),['m/1','m2']);assert.deepEqual(state.productPicker.selected,[]);
assert.equal(requests.length,1);assert.match(dialogs.at(-1)[1],/当前 1 节/);
""")


def test_picker_stale_response_does_not_open_over_other_section():
    run_js(r"""
mockedResponse=()=>{state.selectedSection='s2';return {modules:sampleModules()};};await openProductPicker('s/1',{});assert.equal(dialogs.length,0);
""")


def test_picker_selection_filter_order_remove_clear_and_no_hidden_auto_selection():
    run_js(r"""
state.productPicker=picker();state.productPicker.selected=[];
fire('change',{dataset:{productSelect:'m2'},checked:true});fire('change',{dataset:{productSelect:'m/1'},checked:true});
fire('change',{dataset:{productSelect:'m/1'},checked:true});assert.deepEqual(state.productPicker.selected,['m2','m/1']);
fire('input',{id:'product-picker-query',value:'检测'});assert.deepEqual(productPickerRows().map(m=>m.id),['m/1']);assert.deepEqual(state.productPicker.selected,['m2','m/1']);
await handleProductModuleAction('product-picker-move',{dataset:{id:'m/1',value:'-1'}});assert.deepEqual(state.productPicker.selected,['m/1','m2']);
await handleProductModuleAction('product-picker-remove',{dataset:{id:'m/1'}});assert.deepEqual(state.productPicker.selected,['m2']);
await handleProductModuleAction('product-picker-clear',{dataset:{}});assert.deepEqual(state.productPicker.selected,[]);assert.equal(nodes['[data-action="product-append-preview"]'].disabled,true);
""")


def test_preview_posts_ordered_ids_only_and_shows_before_after_without_modification():
    run_js(r"""
state.productPicker=picker();state.productPicker.selected.reverse();mockedResponse=preview();const original=JSON.stringify(state.project);await previewProductAppend({});
assert.deepEqual(requests[0],{path:'/api/sections/s%2F1/product-modules/preview',options:{method:'POST',body:{module_ids:['m2','m/1']}}});
assert.equal(JSON.stringify(state.project),original);assert.equal(requests.length,1);assert.ok(dialogs.at(-1)[2].includes('追加前'));assert.ok(dialogs.at(-1)[2].includes('追加后'));
assert.equal(state.productPreview.request_id,'request-1');assert.deepEqual(state.productPreview.module_ids,['m2','m/1']);
""")


def test_preview_zero_append_disables_confirmation_and_shows_skip_reasons():
    run_js(r"""
state.productPicker=picker();mockedResponse={...preview(),appended_count:0,skipped_count:2,modules:[{title:'相同版本',reason:'已经放入当前章节',version:2,scope:'archive'}]};await previewProductAppend({});
assert.match(dialogs.at(-1)[3],/product-append-confirm" disabled/);assert.ok(dialogs.at(-1)[2].includes('已经放入当前章节'));
""")


def test_changed_selection_during_preview_ignores_stale_response():
    run_js(r"""
state.productPicker=picker();mockedResponse=()=>{state.productPicker.selected.pop();return preview();};await previewProductAppend({});assert.equal(dialogs.length,0);assert.equal(state.productPreview,undefined);
""")


def test_confirm_appends_explicit_target_then_fetches_current_state_not_old_replay_section():
    run_js(r"""
state.productPicker=picker();state.productPreview={...preview(),projectId:'p',route:1,request_id:'one-op'};const other=JSON.stringify(state.project.sections[1]);
mockedResponse={section_id:'s/1',appended_count:2,skipped_count:0,snapshot_id:'op1',section:{id:'s/1',content:'stale replay body'},results:[],message:'完成'};
refreshMock=()=>{state.project.sections[0].content='最新服务器正文';};await applyProductAppend({});
assert.deepEqual(requests[0],{path:'/api/sections/s%2F1/product-modules/apply',options:{method:'POST',body:{module_ids:['m/1','m2'],revision:'preview-r1',request_id:'one-op',confirmed:true}}});
assert.equal(requests.length,1);assert.equal(refreshes,1);assert.equal(state.project.sections[0].content,'最新服务器正文');assert.equal(JSON.stringify(state.project.sections[1]),other);
assert.ok(dialogs.at(-1)[3].includes('撤销本次追加'));assert.ok(!dialogs.at(-1)[3].includes('regenerate'));assert.equal(state.productPreview,null);
""")


def test_apply_conflict_retains_preview_id_and_selected_materials():
    run_js(r"""
state.productPicker=picker();state.productPreview={...preview(),projectId:'p',route:1,request_id:'same-retry-id'};mockedResponse=()=>{throw Error('章节版本变化');};await applyProductAppend({});
assert.equal(state.productPreview.request_id,'same-retry-id');assert.deepEqual(state.productPicker.selected,['m/1','m2']);assert.equal(refreshes,0);assert.equal(closes,0);
assert.match(nodes['#product-append-error'].textContent,/原选择清单保留/);assert.equal(state.productModuleBusy,false);
""")


def test_apply_avoids_duplicate_concurrent_submission_and_dirty_overwrite():
    run_js(r"""
state.productPicker=picker();state.productPreview={...preview(),projectId:'p',route:1,request_id:'id'};state.productModuleBusy=true;await applyProductAppend({});assert.equal(requests.length,0);
state.productModuleBusy=false;state.dirty=true;await applyProductAppend({});assert.equal(requests.length,0);assert.match(messages[0][0],/重新预览/);
""")


def test_apply_success_with_refresh_failure_remains_explicit_success():
    run_js(r"""
state.productPicker=picker();state.productPreview={...preview(),projectId:'p',route:1,request_id:'id'};
mockedResponse={appended_count:2,skipped_count:0,snapshot_id:'op1'};refreshMock=()=>{throw Error('断开');};await applyProductAppend({});
assert.equal(dialogs.at(-1)[0],'模块追加结果');assert.match(dialogs.at(-1)[2],/章节已保存，页面刷新失败/);assert.equal(state.productPreview,null);
""")


def test_sources_show_frozen_version_and_undo_after_refresh():
    run_js(r"""
state.project.project.metadata.product_module_sources={'s/1':[{module_id:'m1',title:'已删除模块',version:2,scope:'archive',content:'旧版正文123',operation_id:'op1'},{module_id:'m2',title:'第二模块',version:1,scope:'general',content:'第二正文',operation_id:'op1'}]};
const html=renderProductSectionActions(state.project.sections[0]);assert.ok(html.includes('旧版正文123'));assert.ok(html.includes('v2'));
assert.equal((html.match(/data-action="product-undo-preview"/g)||[]).length,1);assert.ok(html.includes('data-id="op1"'));assert.equal(requests.length,0);
""")


def test_undo_uses_operation_id_and_reports_conflict_without_hiding_it():
    run_js(r"""
confirmProductUndo('operation/1','s/1');assert.equal(requests.length,0);assert.match(dialogs.at(-1)[2],/后续修改/);
mockedResponse={status:'conflict',message:'章节已编辑，未恢复'};await undoProductAppend({});
assert.deepEqual(requests[0],{path:'/api/product-module-operations/operation%2F1/undo',options:{method:'POST',body:{confirmed:true}}});
assert.equal(refreshes,1);assert.match(dialogs.at(-1)[2],/未恢复/);assert.equal(requests.length,1);
""")


def test_primary_project_flow_no_longer_runs_full_book_model():
    run_js(r"""
state.tab='requirements';assert.match(projectGenerationAction(),/data-value="sections"/);
state.tab='sections';assert.match(projectGenerationAction(),/product-picker-open/);assert.ok(!projectGenerationAction().includes('data-mode="generate"'));
state.selectedSection='different-project';assert.match(projectGenerationAction(),/data-id="s\/1"/);
state.project.compilation_outline.confirmed=false;assert.match(projectGenerationAction(),/data-value="outline"/);
""")


def test_brand_knowledge_approval_and_single_section_controls_remain_integrated():
    source = (ROOT / "static/app.js").read_text(encoding="utf-8")
    index = (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert '<title>招投标 · 投标工作台</title>' in index
    assert "target.knowledgeTab==='modules'" in source
    assert "renderKnowledgeTabs('documents')" in source
    assert "'knowledge-bulk-preview'" in source and "'approve-document'" in source
    assert '${renderProductSectionActions(s)}' in source
    assert 'id="section-form" data-id="${esc(s.id)}"' in source
    assert "'regenerate-section-preview'" in source and "'section-generation-history'" in source
    assert 'data-mode="generate"' not in source
    assert "if(form.id==='product-module-form'){await saveProductModule(form,button);return;}" in source
