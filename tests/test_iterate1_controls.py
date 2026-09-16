"""Isolated actual API checks; no production state or provider requests."""
import copy
from test_review_rules import local, toggle
from app import db, review_rules as rr, content_review as cr


def test_all_75_persist_and_do_not_mutate_business(local):
    before={t:db.all('SELECT * FROM '+t) for t in ('projects','sections','requirements','documents','reviews','exports')}
    config=local.get('/api/review-settings').json()
    assert len(config['rules'])==75 and all(r['editable'] for r in config['rules'])
    assert len(set(r['group'] for r in config['rules']))>=5
    r=local.patch('/api/review-settings',json={'revision':config['revision'],'enabled':{r['id']:False for r in config['rules']}})
    assert r.status_code==200
    assert not any(local.get('/api/review-settings').json()['enabled'].values())
    assert before=={t:db.all('SELECT * FROM '+t) for t in before}


def test_legacy_saved_flags_default_new_rules_on(local):
    db.set_setting('review_rules',{'empty_body':False})
    flags=rr.flags()
    assert len(flags)==75 and flags['empty_body'] is False
    assert all(v for k,v in flags.items() if k!='empty_body')


def test_threshold_off_removes_approval_gate_not_actual_risk(local):
    db.update('sections','s',{'content':''})
    toggle(local,'approval_threshold',False)
    row=cr.assessments('p')[0][0]
    assert row['eligible'] and row['risk_level']=='high'
    assert row['blockers'] and not row['approval_threshold_enabled']
    assert local.patch('/api/sections/s',json={'status':'approved'}).status_code==200
    saved=db.one('SELECT * FROM projects WHERE id="p"')['metadata']['section_approvals']['s']
    assert saved['threshold_enabled'] is False


def test_body_separation_off_preserves_input_and_on_moves_notes(local):
    text='交付正文。\n【待补充：实际报价金额】'
    toggle(local,'body_separation',False)
    assert local.patch('/api/sections/s',json={'content':text}).status_code==200
    assert db.one('SELECT content FROM sections WHERE id="s"')['content']==text
    toggle(local,'body_separation',True)
    assert local.patch('/api/sections/s',json={'content':text}).status_code==200
    assert '待补充' not in db.one('SELECT content FROM sections WHERE id="s"')['content']
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']


def test_response_confirmation_gate_independent_of_empty_body(local):
    db.insert('requirements',{'id':'r','project_id':'p','text':'采购要求','title':'要求','created_at':db.now(),'updated_at':db.now()})
    assert local.patch('/api/requirements/r',json={'status':'confirmed','response':''}).status_code==400
    toggle(local,'response_confirmation',False)
    assert local.patch('/api/requirements/r',json={'status':'confirmed','response':''}).status_code==200
    assert db.one('SELECT status FROM requirements WHERE id="r"')['status']=='confirmed'


def test_batch_preview_revision_gate_can_be_disabled(local):
    import pytest
    plan=cr.preview('p',['s'],'approve')
    db.update('sections','s',{'content':'依据采购步骤编排新的章节文字。'})
    with pytest.raises(ValueError,match='变化'):
        cr.execute('p',['s'],'approve',plan['token'],True)
    toggle(local,'operation_revision',False)
    assert cr.execute('p',['s'],'approve',plan['token'],True)['approved']==1
    assert db.one('SELECT content FROM sections WHERE id="s"')['content']=='依据采购步骤编排新的章节文字。'


def test_context_snapshot_nested_helpers_and_default(local):
    with rr.scope({'knowledge_origin':False}):
        assert cr.source_allowed({'document_name':'失败案例','text':''})
        with rr.scope():assert not rr.active('knowledge_origin')
    assert rr.active('knowledge_origin')


def test_relaxed_approval_reaches_retrieval_and_generation_context(local):
    from app import workflow, knowledge_review as kr
    db.insert('documents',{'id':'d','name':'合成档案产品.md','path':'synthetic.md','sha256':'original',
        'scope':'archive','source_type':'knowledge','parse_status':'error','created_at':db.now(),'updated_at':db.now()})
    db.insert('chunks',{'id':'c','document_id':'d','ordinal':0,'text':'系统支持电子档案检索和归档。','locator':'段落1'})
    assert kr.preview(['d'])['items'][0]['status']=='blocked'
    toggle(local,'knowledge_parse',False)
    plan=kr.preview(['d'])
    result=kr.apply(plan['ticket'])
    assert result['counts']['approved']==1
    rows=workflow.search_evidence('电子档案检索','archive',project_id='p')
    assert rows and rows[0]['id']=='c'
    with rr.scope():
        request={'title':'档案方案','requirements':[{'id':'r','text':'电子档案检索','category':'technical','title':'检索','quote':'电子档案检索','mandatory':0,'score':''}]}
        project=workflow.project('p')
        prompt,evidence,_=workflow._prepare_generation_request(project,request)
    assert 'c' in evidence
    assert '系统支持电子档案检索和归档。' in prompt
    toggle(local,'knowledge_parse',True)
    assert not workflow.search_evidence('电子档案检索','archive',project_id='p')
