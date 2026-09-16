"""Synthetic SQLite only. No network, real model or production writes."""
import copy
import socket
import pytest
from fastapi.testclient import TestClient
from app import db, workflow, provider, section_generation as single
from app.main import app


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('MX_TESTING', '1')
    original_connect = socket.socket.connect
    def local_connect(sock, address):
        assert isinstance(address, tuple) and address[0] in ('127.0.0.1', '::1'), 'network prohibited'
        return original_connect(sock, address)
    monkeypatch.setattr(socket.socket, 'connect', local_connect)
    monkeypatch.setattr(provider, 'key_configured', lambda: True)
    monkeypatch.setattr(workflow.POOL, 'submit', lambda *a, **k: None)
    db.init()
    for id in ('p', 'other'):
        db.insert('projects', {'id':id,'name':'隔离项目','company_name':'测试企业','domain':'archive','created_at':db.now(),'updated_at':db.now()})
    db.insert('documents', {'id':'d','name':'产品资料','path':'synthetic.md','sha256':'1','status':'approved','scope':'archive','parse_status':'ready','created_at':db.now(),'updated_at':db.now()})
    for id, pid in [('r','p'),('r2','p'),('ro','other')]:
        db.insert('requirements', {'id':id,'project_id':pid,'title':'存储要求','text':'支持档案检索','quote':'支持档案检索','response':'原逐条响应','created_at':db.now(),'updated_at':db.now()})
    for id, pid, rid, ordinal in [('s','p','r',0),('s2','p','r2',1),('so','other','ro',0)]:
        db.insert('sections', {'id':id,'project_id':pid,'ordinal':ordinal,'title':'章节'+id,'content':'原人工正文 123 | 表格 |','status':'approved','user_edited':1,'requirement_ids':[rid],'created_at':db.now(),'updated_at':db.now()})
    db.update('projects','p',{'analysis_status':'complete','analysis_fingerprint':workflow.fingerprint('p')})
    db.insert('reviews',{'id':'audit','project_id':'p','fingerprint':'old','status':'passed','created_at':db.now()})
    db.insert('exports',{'id':'export','project_id':'p','name':'旧稿.md','path':'old.md','format':'md','created_at':db.now()})
    monkeypatch.setattr(workflow, '_prepare_generation_request', lambda p,u: ('本章要求',{},False))
    monkeypatch.setattr(workflow, '_generate_with_repairs', lambda *a, **k: {'content':'新的实质性章节正文，依据当前采购要求组织响应。','responses':[{'requirement_id':'r','response':'只作为生成中间结果，不修改逐条响应','evidence_ids':[],'gap':False}]})
    return TestClient(app)


def submit(client, **changes):
    preview = client.get('/api/sections/s/regeneration')
    assert preview.status_code == 200, preview.text
    body={'revision':preview.json()['revision'],'request_id':'a'*32,'instruction':'','confirmed':True,**changes}
    response=client.post('/api/sections/s/regenerate',json=body)
    return response, body


def execute(response):
    job=response.json()['job']
    result = single.run(job['id'],job)
    db.update('jobs',job['id'],{'status':'succeeded'})
    return result


def test_one_chapter_only_and_restore(sample):
    before={table:db.all('SELECT * FROM '+table+' ORDER BY id') for table in ('sections','requirements','reviews','exports','documents')}
    response, body=submit(sample)
    assert response.status_code == 200
    result=execute(response)
    after=db.one('SELECT * FROM sections WHERE id="s"')
    assert after['status']=='draft' and after['content'] != before['sections'][0]['content']
    assert after['title']==before['sections'][0]['title'] and after['ordinal']==0
    assert after['user_edited']==1
    for table in ('requirements','reviews','exports','documents'):
        assert db.all('SELECT * FROM '+table+' ORDER BY id') == before[table]
    assert db.one('SELECT * FROM sections WHERE id="s2"')==before['sections'][1]
    assert db.one('SELECT * FROM sections WHERE id="so"')==before['sections'][2]
    history=sample.get('/api/sections/s/generation-history').json()['snapshots']
    assert history[0]['content']==before['sections'][0]['content']
    assert sample.get('/api/projects/p/snapshots').json()['snapshots']==[]
    restored=sample.post('/api/section-generation/'+result['snapshot_id']+'/restore')
    assert restored.status_code==200, restored.text
    assert db.one('SELECT * FROM sections WHERE id="s"')['content']==before['sections'][0]['content']
    assert db.one('SELECT * FROM sections WHERE id="s"')['status']=='draft'


@pytest.mark.parametrize('change', ['section','knowledge','requirement'])
def test_preview_change_rejected(sample, change):
    plan=sample.get('/api/sections/s/regeneration').json()
    if change=='section':db.update('sections','s',{'content':'后来人工编辑'})
    if change=='knowledge':db.update('documents','d',{'status':'pending'})
    if change=='requirement':db.update('requirements','r',{'text':'新的需求'})
    response=sample.post('/api/sections/s/regenerate',json={'revision':plan['revision'],'request_id':'b'*32,'confirmed':True})
    assert response.status_code==400
    assert not db.all('SELECT * FROM jobs')


def test_confirm_and_idempotence(sample):
    response,_=submit(sample,confirmed=False)
    assert response.status_code==400
    response,body=submit(sample)
    repeated=sample.post('/api/sections/s/regenerate',json=body)
    assert repeated.json()['job']['id']==response.json()['job']['id']
    assert len(db.all('SELECT * FROM jobs'))==1
    assert sample.post('/api/sections/s/regenerate',json={**body,'instruction':'changed'}).status_code==400


@pytest.mark.parametrize('case', ['error','cancel','bad_response','changed','notes'])
def test_failed_generation_keeps_original(sample,monkeypatch,case):
    before=db.one('SELECT * FROM sections WHERE id="s"')
    response,_=submit(sample)
    def result(*a,**k):
        if case=='error':raise provider.ProviderError('模型失败')
        if case=='cancel':db.update('jobs',response.json()['job']['id'],{'cancel_requested':1})
        if case=='changed':db.update('sections','s',{'content':'并发新正文'})
        return {'content':'TODO: 核对所需材料' if case=='notes' else '仅本章的完整有效正文，用于隔离验收。','responses':[] if case=='bad_response' else [{'requirement_id':'r','response':'响应','evidence_ids':[]}]}
    monkeypatch.setattr(workflow,'_generate_with_repairs',result)
    with pytest.raises((ValueError,provider.ProviderError,provider.Cancelled)):execute(response)
    assert not db.all('SELECT * FROM project_snapshots')
    after=db.one('SELECT * FROM sections WHERE id="s"')
    if case=='changed':assert after['content']=='并发新正文'
    else:assert after==before


def test_restore_conflict_and_unknown_target(sample):
    response,_=submit(sample)
    result=execute(response)
    db.update('sections','s',{'content':'生成后人工修改'})
    assert sample.post('/api/section-generation/'+result['snapshot_id']+'/restore').status_code==400
    assert db.one('SELECT * FROM sections WHERE id="s"')['content']=='生成后人工修改'
    assert sample.get('/api/sections/missing/regeneration').status_code==400


def test_new_operation_changes_cache_identity(sample,monkeypatch):
    prompts=[]
    def generated(j,k,u,e,prompt,**kwargs):
        prompts.append(prompt)
        return {'content':'本章节的有效正文与响应，不涉及其他章节。','responses':[{'requirement_id':'r','response':'已响应','evidence_ids':[]}]}
    monkeypatch.setattr(workflow,'_generate_with_repairs',generated)
    response,_=submit(sample)
    execute(response)
    db.update('jobs',response.json()['job']['id'],{'status':'succeeded'})
    response,_=submit(sample,request_id='c'*32)
    execute(response)
    assert len(prompts)==2 and prompts[0]!=prompts[1]


def test_missing_requirement_and_busy_project(sample):
    db.update('sections','s',{'requirement_ids':[]})
    assert sample.get('/api/sections/s/regeneration').status_code==400
    db.update('sections','s',{'requirement_ids':['r']})
    db.insert('jobs',{'id':'busy','project_id':'p','mode':'review','created_at':db.now()})
    response,_=submit(sample)
    assert response.status_code==400


def test_worker_dispatch(sample):
    response,_=submit(sample)
    workflow._run(response.json()['job']['id'])
    job=db.one('SELECT * FROM jobs WHERE id=?',(response.json()['job']['id'],))
    assert job['status']=='succeeded',job['error']
    assert job['result']['sections']==1


def test_delivery_todos_survive_rewrite(sample):
    todo={'id':'delivery','kind':'delivery','origin':'generation','message':'附件签章尚未完成',
          'section_ids':['s'],'requirement_ids':['r'],'status':'open','blocks_content':False,'blocks_delivery':True}
    db.update('projects','p',{'metadata':{'content_todos':[todo]}})
    response,_=submit(sample)
    execute(response)
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']==[todo]


def test_restore_only_target_todo_association(sample):
    todo={'id':'shared','kind':'support_review','origin':'generation','message':'核对技术描述',
          'section_ids':['s','s2'],'requirement_ids':['r','r2'],'status':'open','blocks_content':True,'blocks_delivery':True}
    db.update('projects','p',{'metadata':{'content_todos':[todo]}})
    response,_=submit(sample)
    result=execute(response)
    remaining=db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']
    assert remaining[0]['section_ids']==['s2']
    # A separate later decision removes the second chapter association.
    db.update('projects','p',{'metadata':{'content_todos':[]}})
    restored=sample.post('/api/section-generation/'+result['snapshot_id']+'/restore')
    assert restored.status_code==200,restored.text
    todos=db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']
    assert len(todos)==1 and todos[0]['section_ids']==['s'] and todos[0]['requirement_ids']==['r']


def test_committed_job_retry_does_not_call_model(sample,monkeypatch):
    response,_=submit(sample)
    job_id=response.json()['job']['id']
    real_review=workflow.review_project
    monkeypatch.setattr(workflow,'review_project',lambda *a:(_ for _ in ()).throw(RuntimeError('after commit interruption')))
    workflow._run(job_id)
    job=db.one('SELECT * FROM jobs WHERE id=?',(job_id,))
    assert job['status']=='failed' and job['checkpoint']['single_saved']
    monkeypatch.setattr(workflow,'review_project',real_review)
    monkeypatch.setattr(workflow,'_generate_with_repairs',lambda *a,**k:(_ for _ in ()).throw(AssertionError('must not call model')))
    retry=sample.post('/api/jobs/'+job_id+'/retry')
    assert retry.status_code==200,retry.text
    workflow._run(retry.json()['job']['id'])
    assert db.one('SELECT * FROM jobs WHERE id=?',(retry.json()['job']['id'],))['status']=='succeeded'
    assert len(db.all('SELECT * FROM project_snapshots'))==1
