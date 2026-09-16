import copy
import json
import uuid
from app import db,workflow,provider,index_materials,index_pages,proposal_runtime,section_generation
from test_proposal_pipeline import pipeline,create_and_generate,execute_job
from test_index_materials import EMPTY


def target_and_empty(client):
    pid,detail,_,_=create_and_generate(client)
    index=next(s for s in detail['sections'] if s['outline_group_title']=='评审索引表')
    target=next(s for s in detail['sections'] if s['title']=='智能检索')
    db.update('sections',target['id'],{'content':'','status':'draft','updated_at':db.now()})
    return pid,index,target


def test_paginated_export_receipt_real_version_and_stale_after_edit(pipeline):
    client,model,_=pipeline;pid,detail,_,_=create_and_generate(client)
    before_calls=len(model.calls)
    before=client.get(f'/api/projects/{pid}/index-pages').json();assert not before['valid']
    job=execute_job(client,client.post(f'/api/projects/{pid}/index-pages',json={}))
    state=client.get(f'/api/projects/{pid}/index-pages').json()
    assert state['valid'] and all(r['page']==2 for r in state['items'] if r['ready'])
    assert len(model.calls)==before_calls
    exports=db.all('SELECT * FROM exports WHERE project_id=?',(pid,))
    workflow._run(job['id'])
    assert db.all('SELECT * FROM exports WHERE project_id=?',(pid,))==exports
    selected=detail['sections'][1]
    db.update('sections',selected['id'],{'content':selected['content']+'\n后续修改','updated_at':db.now()})
    changed=client.get(f'/api/projects/{pid}/index-pages').json()
    assert not changed['valid'] and '已失效' in changed['reason']


def test_complete_only_previewed_missing_section_then_paginate(pipeline):
    client,model,_=pipeline;pid,index,target=target_and_empty(client)
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json()
    plan=preview['index_materials'];assert target['id'] in plan['fillable_ids']
    before={s['id']:s for s in db.all('SELECT * FROM sections WHERE project_id=?',(pid,))};calls=len(model.calls)
    payload={'revision':preview['revision'],'outline_revision':preview['outline_revision'],'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,
             'complete_index_gaps':True,'completion_revision':plan['revision'],'completion_ids':[target['id']],'update_index_pages':True}
    response=client.post(f"/api/sections/{index['id']}/regenerate",json=payload)
    job=execute_job(client,response)
    assert len(model.calls)==calls+1
    assert job['result']['completion_results'][0]['material_ready']
    assert 'pagination_error' not in job['result']
    for s in db.all('SELECT * FROM sections WHERE project_id=?',(pid,)):
        if s['id'] not in (index['id'],target['id']):assert s==before[s['id']]
    assert client.get(f'/api/projects/{pid}/index-pages').json()['valid']
    duplicate=client.post(f"/api/sections/{index['id']}/regenerate",json=payload)
    assert duplicate.status_code==200 and duplicate.json()['job']['id']==job['id']
    exports=db.all('SELECT * FROM exports WHERE project_id=?',(pid,));workflow._run(job['id'])
    assert len(model.calls)==calls+1 and db.all('SELECT * FROM exports WHERE project_id=?',(pid,))==exports
    history=client.get(f"/api/sections/{target['id']}/generation-history").json()['snapshots']
    assert history and history[0]['content']==''


def test_update_pages_checkbox_does_not_bypass_preview_revision(pipeline):
    client,model,_=pipeline;pid,index,target=target_and_empty(client)
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json()
    response=client.post(f"/api/sections/{index['id']}/regenerate",json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,'update_index_pages':True})
    assert response.status_code==200
    updated=index['content']+'\n人工后续备注';db.update('sections',index['id'],{'content':updated,'updated_at':db.now()})
    calls=len(model.calls);workflow._run(response.json()['job']['id'])
    job=client.get('/api/jobs/'+response.json()['job']['id']).json()['job']
    assert job['status']=='failed' and db.one('SELECT * FROM sections WHERE id=?',(index['id'],))['content']==updated
    assert len(model.calls)==calls


def test_sources_change_after_admission_prevents_remaining_paid_work(pipeline):
    client,model,_=pipeline;pid,index,target=target_and_empty(client)
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json();plan=preview['index_materials']
    response=client.post(f"/api/sections/{index['id']}/regenerate",json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,'complete_index_gaps':True,
        'completion_revision':plan['revision'],'completion_ids':[target['id']]})
    assert response.status_code==200
    p=db.one('SELECT * FROM projects WHERE id=?',(pid,));p['metadata']['trusted_sources']={'changed':'later'};db.update('projects',pid,{'metadata':p['metadata']})
    calls=len(model.calls);workflow._run(response.json()['job']['id'])
    job=client.get('/api/jobs/'+response.json()['job']['id']).json()['job']
    assert job['status']=='failed' and '资料' in job['message'] and len(model.calls)==calls
    assert db.one('SELECT * FROM sections WHERE id=?',(target['id'],))['content']==''


def test_word_failure_keeps_saved_index_and_only_pagination_can_retry(pipeline,monkeypatch):
    client,model,_=pipeline;pid,detail,_,_=create_and_generate(client);index=detail['sections'][0]
    from app import proposal_export
    monkeypatch.setattr(proposal_export,'_paginate',lambda *a,**k:(_ for _ in ()).throw(ValueError('Word不可用')))
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json();calls=len(model.calls)
    job=execute_job(client,client.post(f"/api/sections/{index['id']}/regenerate",json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,'update_index_pages':True}))
    assert 'Word不可用' in job['result']['pagination_error'] and not db.all('SELECT * FROM exports WHERE project_id=?',(pid,))
    assert len(model.calls)==calls and db.one('SELECT * FROM sections WHERE id=?',(index['id'],))['status']=='draft'


def test_no_available_sources_keeps_gap_and_rejects_unpreviewed_fill(pipeline,monkeypatch):
    client,model,_=pipeline;pid,index,target=target_and_empty(client)
    from app import proposal_context
    monkeypatch.setattr(workflow,'search_evidence',lambda *a,**k:[])
    monkeypatch.setattr(proposal_context,'search_context',lambda *a,**k:{'evidence':[]})
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json();plan=preview['index_materials']
    assert target['id'] not in plan['fillable_ids']
    item=next(r for r in plan['items'] if r['section_id']==target['id'])
    assert not item['can_fill'] and '没有获准' in item['action']
    calls=len(model.calls)
    response=client.post(f"/api/sections/{index['id']}/regenerate",json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,'complete_index_gaps':True,
        'completion_revision':plan['revision'],'completion_ids':[target['id']]})
    assert response.status_code==400 and len(model.calls)==calls


def test_source_change_between_children_stops_without_repeating_first(pipeline,monkeypatch):
    client,model,_=pipeline;pid,index,target=target_and_empty(client)
    second=next(s for s,spec in index_materials.scoped_sections(workflow.project(pid))
                if s['id']!=target['id'] and spec.get('content_kind')=='narrative')
    db.update('sections',second['id'],{'content':'','updated_at':db.now()})
    preview=client.get(f"/api/sections/{index['id']}/regeneration").json();plan=preview['index_materials']
    response=client.post(f"/api/sections/{index['id']}/regenerate",json={'revision':preview['revision'],'outline_revision':preview['outline_revision'],
        'request_id':uuid.uuid4().hex,'instruction':'','confirmed':True,'complete_index_gaps':True,
        'completion_revision':plan['revision'],'completion_ids':[target['id'],second['id']]})
    assert response.status_code==200,response.text
    original=section_generation.run_one
    def change_after_save(job_id,job):
        result=original(job_id,job)
        p=db.one('SELECT * FROM projects WHERE id=?',(pid,));p['metadata']['trusted_sources']={'later':'changed'}
        db.update('projects',pid,{'metadata':p['metadata']})
        return result
    monkeypatch.setattr(section_generation,'run_one',change_after_save)
    calls=len(model.calls);workflow._run(response.json()['job']['id'])
    assert len(model.calls)==calls+1 and db.one('SELECT * FROM sections WHERE id=?',(second['id'],))['content']==''
    monkeypatch.setattr(section_generation,'run_one',original)
    workflow._run(response.json()['job']['id'])
    assert len(model.calls)==calls+1  # Remaining work uses no unpreviewed new source.
