"""Top-level model gaps are scoped internal work; all providers are mocked."""
import copy
import json
import socket
import uuid

import pytest

from app import content_review as cr, db, provider, workflow as w
from test_proposal_pipeline import pipeline, create_and_generate, execute_job


@pytest.fixture
def sample(monkeypatch,tmp_path):
    monkeypatch.setenv('MX_TESTING','1');monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(db,'DATA',tmp_path/'data');db.init()
    def no(*a,**k):raise AssertionError('No network/model')
    monkeypatch.setattr(socket.socket,'connect',no);monkeypatch.setattr(socket,'getaddrinfo',no);monkeypatch.setattr(provider,'chat_json',no)
    p={'id':'p','name':'样例','company_name':'样例公司','domain':'archive','metadata':{'generation_profile':'technical_proposal'},'created_at':db.now(),'updated_at':db.now()}
    db.insert('projects',p)
    for id in ('r1','r2'):db.insert('requirements',{'id':id,'project_id':'p','title':'要求','text':'要求内容','category':'technical','created_at':db.now(),'updated_at':db.now()})
    s={'id':'s','project_id':'p','ordinal':0,'title':'数据安全','content':'正文与表格保持原样。\n| 字段 | 数字 |\n| 值 | 100 |','status':'draft','requirement_ids':['r1','r2'],'evidence_ids':['body-evidence'], 'created_at':db.now(),'updated_at':db.now()}
    db.insert('sections',s)
    unit={**s,'requirements':[db.one('SELECT * FROM requirements WHERE id=?',(id,)) for id in ('r1','r2')],'_proposal_spec':{'content_kind':'narrative'}}
    return p,s,unit


def answer(s,gap):
    return {'content':s['content'],'responses':[{'requirement_id':id,'response':'正式响应','evidence_ids':[],'gap':False,'gap_reason':''} for id in s['requirement_ids']],
            'gap_reason':gap,'_request_diagnostic':'diagnostics/generation/job/request.json'}


@pytest.mark.parametrize('value',[None,'','  ','无','空','无。','N/A',[],{},False])
def test_empty_reason_does_not_create_tasks(value):
    assert cr.top_level_gap_notes({'id':'s','requirement_ids':[]},{'gap_reason':value})==[]


def test_top_only_gaps_persist_without_body_or_response_changes_and_dedupe(sample):
    p,s,unit=sample
    result=answer(s,'1. 【R02-字段待确认】请确认接口联系人。\n\n2. 【交付附件待办】演示视频尚未装入交付文件。')
    before=copy.deepcopy(result);result['_internal_notes']=cr.top_level_gap_notes(unit,result,{})
    meta=cr.generation_metadata(p,s,result);todos=meta['content_todos']
    assert len(todos)==2 and all(t['section_ids']==['s'] for t in todos)
    field=next(t for t in todos if t['kind']=='support_review')
    delivery=next(t for t in todos if t['kind']=='delivery')
    assert field['requirement_ids']==['r2'] and field['blocks_content']
    assert delivery['requirement_ids']==[] and not delivery['blocks_content'] and delivery['blocks_delivery']
    assert all(t['evidence_ids']==[] for t in todos)  # Not all section body citations.
    again=cr.generation_metadata({**p,'metadata':meta},s,result)
    assert again['content_todos']==todos
    assert result['content']==before['content'] and result['responses']==before['responses']
    assert db.one('SELECT * FROM sections WHERE id="s"')['content']==s['content']


def test_save_fallback_preserves_unknown_bindings_and_diagnostic(sample):
    p,s,_=sample;result=answer(s,'【R02-待确认】引用[E:E79]的条件尚未确认。')
    meta=cr.generation_metadata(p,s,result);t=meta['content_todos'][0]
    assert t['source_diagnostic']==result['_request_diagnostic']
    assert t['evidence_ids']==[] and t['requirement_ids']==[]
    assert {'type':'requirement','alias':'R02'} in t['unresolved_gap_aliases']
    assert t['raw']==result['gap_reason'] and 'E79' in t['message']


def test_exact_evidence_aliases_keep_reference_roles_without_promoting_body_evidence(sample):
    _,s,unit=sample;result=answer(s,'【R02-备份策略待确认】原拟方案[E:E01]与[E:E:E02]需澄清；未知[E:E99]不得猜测。')
    evidence={'aa':{'document_id':'d','reference_role':'same_tender_proposed_plan'},'bb':{'document_id':'d2','reference_role':'conditional_resource_recommendation'}}
    note=cr.top_level_gap_notes(unit,result,evidence)[0];t=cr.separated_todos(s,[note])[0]
    assert t['requirement_ids']==['r2'] and t['evidence_ids']==['aa','bb']
    assert t['gap_alias_bindings']['evidence']['E01']['reference_role']=='same_tender_proposed_plan'
    assert t['gap_alias_bindings']['evidence']['E:E02']['reference_role']=='conditional_resource_recommendation'
    assert any(x['alias']=='E99' for x in t['unresolved_gap_aliases'])
    assert s['evidence_ids']==['body-evidence'] and not result['responses'][0]['gap']


@pytest.mark.parametrize('value',[{'unknown':{'material':'归档证明需人工核实'}},[{'message':'签署日期待核对'},'需提供接口联系人'],123,True])
def test_nonstandard_structure_is_visible_and_lossless(sample,value):
    p,s,_=sample;result=answer(s,value)
    todos=cr.generation_metadata(p,s,result)['content_todos']
    assert todos and any(t['gap_structure_requires_review'] for t in todos)
    raw=' '.join(t['raw'] for t in todos)
    for word in ('归档证明','签署日期'):
        if word in json.dumps(value,ensure_ascii=False):assert word in raw
    assert all(t['blocks_content'] for t in todos)


def test_same_raw_note_never_attaches_to_another_chapter(sample):
    _,s,_=sample;result=answer(s,'请确认接口联系人。')
    one=cr.separated_todos(s,cr.top_level_gap_notes(s,result))
    two=cr.separated_todos({**s,'id':'other'},cr.top_level_gap_notes({**s,'id':'other'},result))
    merged=cr.merge_todos(one+two)
    assert len(merged)==2 and {tuple(t['section_ids']) for t in merged}=={('s',),('other',)}


def test_exact_intro_is_not_an_extra_gap_but_unknown_prefix_is_retained():
    text='以下为需项目确认的具体缺项，不逐段写入正文：\n\n1. 【A待确认】需要核对A。\n\n2. 【B待确认】需要核对B。'
    assert len(cr.top_level_gap_notes({'id':'s'},{'gap_reason':text}))==2
    assert len(cr.top_level_gap_notes({'id':'s'},{'gap_reason':'需先确认接口X的服务方。\n1. 另需核对A。'}))==2


def test_leading_decimal_is_not_mistaken_for_list_number():
    raw='99.9%检出率需确认是否作为本项目承诺。'
    section={'id':'s','requirement_ids':[],'evidence_ids':[]}
    notes=cr.top_level_gap_notes(section,{'gap_reason':raw})
    assert notes[0]['message']==raw and cr.separated_todos(section,notes)[0]['message']==raw


def test_inline_numbered_real_gaps_keep_separate_requirement_links():
    section={'id':'s','requirements':[{'id':'r1'},{'id':'r2'}],'requirement_ids':['r1','r2']}
    notes=cr.top_level_gap_notes(section,{'gap_reason':'1. 99.9%检出率需确认。关联R01。属于正文缺项。2. 需提供证书复印件。关联R02。属于交付附件。'})
    assert len(notes)==2 and notes[0]['message'].startswith('99.9%')
    assert notes[0]['requirement_ids']==['r1'] and notes[1]['requirement_ids']==['r2']
    assert notes[0]['blocks_content'] and not notes[1]['blocks_content']


def test_workflow_normalizes_before_validated_diagnostic(sample,monkeypatch):
    _,s,unit=sample;raw=answer(s,'【R02-日期待确认】验收日期需核对。')
    raw['content']='正式完整正文。'
    for row in raw['responses']:row['requirement_id']={'r1':'R01','r2':'R02'}[row['requirement_id']]
    monkeypatch.setattr(provider,'chat_json',lambda *a,**k:copy.deepcopy(raw))
    prompt='要求数据：\n'+json.dumps(unit['requirements'],ensure_ascii=False)+'\n证据数据：\n[]'
    result=w._generate_with_repairs('j','k',unit,{},prompt)
    assert result['_internal_notes'][0]['requirement_ids']==['r2']
    path=next((db.DATA/'diagnostics/generation/j').glob('*-validated.json'))
    saved=json.loads(path.read_text('utf-8'))['validated_response']
    assert saved['_internal_notes'] and saved['gap_reason']==raw['gap_reason']
    assert all(not r['gap'] for r in result['responses'])


def test_full_api_single_section_saves_top_gap_and_snapshot_without_rewriting_responses(pipeline,monkeypatch):
    client,model,_=pipeline
    original=provider.chat_json
    def mock_with_gap(system,prompt,cancel=None):
        result=original(system,prompt,cancel=cancel)
        if 'responses' in result:
            result['gap_reason']='【R01-接口联系人待确认】需核对本节接口联系人。'
            assert all(not r.get('gap') for r in result['responses'])
        return result
    monkeypatch.setattr(provider,'chat_json',mock_with_gap)
    pid,detail,_,_=create_and_generate(client)
    spec=next(s for s in detail['project']['metadata']['proposal_blueprint']['sections'] if s['title']=='智能检索');sid=spec['section_id']
    before={s['id']:s for s in detail['sections']}
    assert len([t for t in detail['project']['metadata']['content_todos'] if t.get('origin')=='generation_top_gap' and t['section_ids']==[sid]])==1
    preview=client.get('/api/sections/'+sid+'/regeneration').json()
    response=client.post('/api/sections/'+sid+'/regenerate',json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],'request_id':uuid.uuid4().hex,'instruction':'仅修改本节','confirmed':True})
    job=execute_job(client,response)
    after=client.get('/api/projects/'+pid).json()
    notes=[t for t in after['project']['metadata']['content_todos'] if t.get('origin')=='generation_top_gap' and t['section_ids']==[sid]]
    assert len(notes)==1 and notes[0]['requirement_ids']
    assert all('接口联系人待确认' not in s['content'] for s in after['sections'])
    assert all(s==before[s['id']] for s in after['sections'] if s['id']!=sid)
    record=after['project']['metadata']['proposal_section_results'][sid]
    assert all(not r['gap'] for r in record['responses'])
    snapshot=db.one('SELECT * FROM project_snapshots WHERE id=?',(job['result']['snapshot_id'],))
    assert any(t.get('origin')=='generation_top_gap' for t in snapshot['payload']['after_todos'])
    from app import section_generation
    calls=len(model.calls);section_generation.run(job['id'],job)
    assert len(model.calls)==calls
    again=client.get('/api/projects/'+pid).json()['project']['metadata']['content_todos']
    assert [t for t in again if t.get('origin')=='generation_top_gap' and t['section_ids']==[sid]]==notes
