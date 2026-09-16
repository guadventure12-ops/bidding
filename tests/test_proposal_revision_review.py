"""Version-bound review, safe planning recovery and exact-scope recipes; offline."""
import copy
import hashlib
import json

import pytest

from test_review_rules import local
from test_proposal_runtime import tender
from app import content_review as cr, db, model_jobs, proposal_runtime as pr, provider, review_rules, workflow


def planned():
    requirements=tender()
    db.execute('DELETE FROM sections')
    units=pr.prepare_plan('p',requirements)
    return units,workflow.project('p')


def current_response():
    units,p=planned()
    owner=next(u for u in units if 'r' in u['requirement_ids'])
    db.update('sections',owner['id'],{'content':'当前章节的实质正文。','updated_at':'2026-01-01T00:00:00+00:00'})
    section=db.one('SELECT * FROM sections WHERE id=?',(owner['id'],))
    metadata=pr.record_result(p['metadata'],section,{'responses':[{'requirement_id':'r','response':'当前响应正文。','evidence_ids':[],'gap':False}]})
    db.update('projects','p',{'metadata':metadata})
    db.update('requirements','r',{'response':'旧版响应999天。[E:old-unknown]','evidence_ids':['old-unknown'],'status':'gap','updated_at':'2025-01-01T00:00:00+00:00'})
    return section


def snapshot_business():
    return {table:db.all('SELECT * FROM '+table+' ORDER BY id') for table in ('projects','sections','requirements','project_snapshots','reviews','exports')}


def recipe(**updates):
    return {'id':'reviewed-input-v1','version':'1','tender_sha256s':['a'*64],
            'source_policy':{'mode':'approved_facts_only','fact_document_ids':[],'reference_document_ids':[]},**updates}


def write_recipes(rows):
    (db.DATA/'proposal-recipes.json').write_text(json.dumps({'recipes':rows}),encoding='utf-8')


def test_local_review_reads_current_projection_instead_of_old_raw_response(local):
    current_response()
    raw=db.one('SELECT * FROM requirements WHERE id="r"')
    checks=workflow.review_project('p',persist=False)
    encoded=json.dumps(checks,ensure_ascii=False)
    assert 'old-unknown' not in encoded and '999天' not in encoded
    assert not any(c['code']=='response_gaps' for c in checks)
    assert db.one('SELECT * FROM requirements WHERE id="r"')==raw
    ui=local.get('/api/projects/p').json()
    assert next(r for r in ui['requirements'] if r['id']=='r')['response']=='当前响应正文。'


def test_missing_response_revision_is_checked_as_current_gap_not_old_text(local):
    section=current_response()
    db.update('sections',section['id'],{'content':'后续人工修改当前章节。'})
    checks=workflow.review_project('p',persist=False)
    issue=next(c for c in checks if c['code']=='response_gaps' and 'response_version_issues' in c['details'])
    assert issue['details']['requirement_ids']==['r']
    assert issue['details']['response_version_issues'][0]['section_id']==section['id']
    assert 'old-unknown' not in json.dumps(checks)


def test_independent_ai_review_receives_same_current_responses_as_ui_and_export(local,monkeypatch):
    current_response();captured=[]
    monkeypatch.setattr(provider,'key_configured',lambda:True)
    def reviewed(job,key,batch,prompt,project_id,cancel=None):
        captured.extend(copy.deepcopy(batch))
        return {'assessments':[{'target_id':t['target_id'],'verdict':'supported' if t.get('response') or t.get('content') else 'gap','reason':'离线Mock核对当前输入'} for t in batch]}
    monkeypatch.setattr(workflow,'_review_with_repairs',reviewed)
    db.insert('jobs',{'id':'audit-current','project_id':'p','mode':'review','status':'running','created_at':db.now()})
    result=workflow.run_review('audit-current',db.one('SELECT * FROM jobs WHERE id="audit-current"'))
    assert result['ai_completed']
    target=next(t for t in captured if t['target_id']=='R:r')
    assert target['response']=='当前响应正文。' and target['evidence_ids']==[]
    assert '旧版响应' not in json.dumps(captured,ensure_ascii=False)
    project=workflow.project('p');sections=db.all('SELECT * FROM sections WHERE project_id="p"');reqs=db.all('SELECT * FROM requirements WHERE project_id="p"')
    projected,issues=pr.project_responses(project,sections,reqs)
    assert not issues and projected[0]['response']==target['response']


def test_review_fingerprint_tracks_manual_confirmation_precedence_timestamp(local):
    section=current_response()
    db.update('requirements','r',{'response':'后来确认的人工响应。','evidence_ids':[],'status':'confirmed','updated_at':'2025-01-01T00:00:00+00:00'})
    before=workflow.review_fingerprint('p')
    db.update('requirements','r',{'updated_at':'2027-01-01T00:00:00+00:00'})
    after=workflow.review_fingerprint('p')
    assert before!=after
    assert local.get('/api/projects/p').json()['requirements'][0]['response']=='后来确认的人工响应。'


def test_ui_risk_context_uses_current_fingerprint_and_preserves_historical_review(local):
    current_response()
    old={'id':'old-review','project_id':'p','fingerprint':'old-version','status':'complete',
         'findings':[{'section_id':'does-not-matter','reason':'旧版错误结论'}],'created_at':'2099-01-01'}
    db.insert('reviews',old)
    assert cr.context('p')[3]==[]
    current=workflow.review_fingerprint('p')
    findings=[{'reason':'本版核查记录'}]
    db.insert('reviews',{'id':'current-review','project_id':'p','fingerprint':current,'status':'complete','findings':findings,'created_at':'2026-01-01'})
    assert cr.context('p')[3]==findings
    assert db.one('SELECT * FROM reviews WHERE id="old-review"')['findings']==old['findings']


@pytest.mark.parametrize('change',['add','remove','text','quote','source_version','section_mapping'])
def test_existing_plan_fails_closed_on_source_or_requirement_mapping_changes(local,change):
    units,p=planned()
    if change=='add':
        db.insert('requirements',{'id':'extra','project_id':'p','title':'新要求','text':'新增采购要求','created_at':db.now(),'updated_at':db.now()})
    elif change=='remove':db.execute('DELETE FROM requirements WHERE id="r"')
    elif change=='source_version':db.update('documents','t',{'sha256':'b'*64})
    elif change=='section_mapping':
        owner=next(u for u in units if 'r' in u['requirement_ids']);db.update('sections',owner['id'],{'requirement_ids':[]})
    else:db.update('requirements','r',{change:'改动后的采购含义'})
    before=snapshot_business()
    with pytest.raises(ValueError,match='要求|版本|范围'):
        pr.prepare_plan('p',db.all('SELECT * FROM requirements WHERE project_id="p"'))
    assert snapshot_business()==before


def test_response_approval_and_activity_updates_do_not_invalidate_planning(local):
    units,_=planned()
    db.update('requirements','r',{'response':'人工确认响应','status':'confirmed','updated_at':db.now(),'evidence_ids':[]})
    owner=next(u for u in units if 'r' in u['requirement_ids'])
    db.update('sections',owner['id'],{'content':'人工编辑已批准的正文。','status':'approved','user_edited':1,'updated_at':db.now()})
    repeated=pr.prepare_plan('p',db.all('SELECT * FROM requirements WHERE project_id="p"'))
    kept=next(u for u in repeated if u['id']==owner['id'])
    assert kept['content']=='人工编辑已批准的正文。' and kept['status']=='approved'


def test_reparse_preserves_snapshot_and_cannot_rebuild_old_profile_implicitly(local,monkeypatch):
    units,p=planned()
    owner=next(u for u in units if 'r' in u['requirement_ids'])
    db.update('sections',owner['id'],{'content':'受保护的原正文。','status':'approved'})
    original=db.all('SELECT * FROM sections WHERE project_id="p" ORDER BY ordinal')
    from app import documents
    monkeypatch.setattr(documents,'parse_document',lambda *a,**k:{'blocks':[{'text':'重新解析后的采购文本','locator':'正文 / 段落 1'}],'warnings':[]})
    db.insert('jobs',{'id':'reparse-current','project_id':'p','mode':'reparse','status':'running','payload':{'document_id':'t'},'created_at':db.now()})
    result=workflow.run_reparse('reparse-current',db.one('SELECT * FROM jobs WHERE id="reparse-current"'))
    db.update('jobs','reparse-current',{'status':'succeeded'})
    saved=db.one('SELECT * FROM project_snapshots WHERE id=?',(result['snapshot_id'],))
    assert saved['payload']['sections']==original
    assert not db.all('SELECT * FROM sections WHERE project_id="p"')
    before=snapshot_business()
    with pytest.raises(ValueError,match='目录与当前章节不一致'):
        pr.prepare_plan('p',[])
    assert snapshot_business()==before
    workflow.restore_snapshot(result['snapshot_id'])
    restored=pr.prepare_plan('p',db.all('SELECT * FROM requirements WHERE project_id="p"'))
    assert {u['id'] for u in restored}=={u['id'] for u in original}
    assert next(u for u in restored if u['id']==owner['id'])['content']=='受保护的原正文。'


def test_recipe_requires_exact_complete_tender_sha_set_not_name_or_other_documents(local):
    tender();write_recipes([recipe()])
    assert pr.matching_recipe('p')['id']=='reviewed-input-v1'
    db.update('documents','t',{'sha256':'b'*64})
    assert pr.matching_recipe('p') is None
    db.update('documents','t',{'sha256':'a'*64})
    db.insert('documents',{'id':'extra-t','project_id':'p','name':'同名采购文件.docx','path':'unused','sha256':'b'*64,'source_type':'tender','parse_status':'ready','created_at':db.now(),'updated_at':db.now()})
    assert pr.matching_recipe('p') is None
    write_recipes([recipe(tender_sha256s=['a'*64,'b'*64])])
    assert pr.matching_recipe('p')
    db.update('documents','extra-t',{'parse_status':'error'})
    assert pr.matching_recipe('p') is None


@pytest.mark.parametrize('hashes',['a'*64,['a'*63],[12],[]])
def test_recipe_rejects_malformed_sha_configuration(local,hashes):
    tender();write_recipes([recipe(tender_sha256s=hashes)])
    with pytest.raises(ValueError,match='SHA256'):pr.matching_recipe('p')


def test_duplicate_matching_recipes_are_not_selected_arbitrarily(local):
    tender();write_recipes([recipe(),recipe(id='duplicate')])
    with pytest.raises(ValueError,match='多个参考资料选择'):pr.matching_recipe('p')


def test_recipe_never_changes_existing_legacy_sections_or_old_empty_content_history(local):
    reqs=tender();write_recipes([recipe()]);before=snapshot_business()
    assert pr.prepare_plan('p',reqs) is None
    assert snapshot_business()==before
    db.execute('DELETE FROM sections')
    db.insert('project_snapshots',{'id':'old-content','project_id':'p','label':'原正文快照','payload':{'sections':[{'id':'original','content':'历史正文'}]},'created_at':db.now()})
    before=snapshot_business()
    with pytest.raises(ValueError,match='已有正文或交付历史'):pr.prepare_plan('p',reqs)
    assert snapshot_business()==before


@pytest.mark.parametrize('kind',['missing','changed','outside'])
def test_recipe_asset_manifest_rejected_before_any_plan_write(local,kind):
    reqs=tender();db.execute('DELETE FROM sections')
    path=db.DATA/'assets'/'manifest.json';path.parent.mkdir();path.write_text('{"assets":[]}',encoding='utf-8')
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    data=recipe(asset_manifest_relative='assets/manifest.json',asset_manifest_sha256=sha)
    if kind=='missing':data['asset_manifest_relative']='assets/absent.json'
    if kind=='changed':data['asset_manifest_sha256']='b'*64
    if kind=='outside':data['asset_manifest_relative']='../outside.json'
    write_recipes([data]);before=snapshot_business()
    with pytest.raises(ValueError,match='图片'):pr.prepare_plan('p',reqs)
    assert snapshot_business()==before


def fact(id,text):
    db.insert('documents',{'id':id,'name':id+'产品资料','path':'unused.md','sha256':hashlib.sha256(text.encode()).hexdigest(),
                          'source_type':'knowledge','status':'approved','scope':'archive','parse_status':'ready','created_at':db.now(),'updated_at':db.now()})
    db.insert('chunks',{'id':id+'-chunk','document_id':id,'ordinal':0,'text':text,'locator':'第1行'})


def test_recipe_edits_are_not_hot_applied_to_existing_project_or_its_model_cache(local):
    reqs=tender();db.execute('DELETE FROM sections')
    fact('first','产品提供需求理解方案及档案检索功能。')
    fact('second','产品提供需求理解方案及档案查询功能。')
    chosen=recipe(source_policy={'mode':'approved_facts_only','fact_document_ids':['first'],'reference_document_ids':[]})
    write_recipes([chosen]);units=pr.prepare_plan('p',reqs);p=workflow.project('p')
    unit=next(u for u in units if u['requirement_ids'])
    prompt,evidence,_=workflow._prepare_generation_request(p,unit)
    assert {e['document_id'] for e in evidence.values()}=={'first'}
    key,_=model_jobs.request_identity('test-system',prompt,workflow._generation_inputs(unit,evidence))
    later=recipe(id='reviewed-input-v2',version='2',source_policy={'mode':'approved_facts_only','fact_document_ids':['second'],'reference_document_ids':[]})
    write_recipes([later])
    again=pr.prepare_plan('p',reqs);now=workflow.project('p')
    assert now['metadata']==p['metadata']
    prompt2,evidence2,_=workflow._prepare_generation_request(now,next(u for u in again if u['id']==unit['id']))
    key2,_=model_jobs.request_identity('test-system',prompt2,workflow._generation_inputs(unit,evidence2))
    assert key2==key
    # An explicit later selection is different input and cannot reuse the first
    # source's cache, even though all other section fields remain unchanged.
    updated=copy.deepcopy(now['metadata']);updated['proposal_source_policy']=later['source_policy']
    db.update('projects','p',{'metadata':updated})
    prompt3,evidence3,_=workflow._prepare_generation_request(workflow.project('p'),unit)
    assert {e['document_id'] for e in evidence3.values()}=={'second'}
    key3,_=model_jobs.request_identity('test-system',prompt3,workflow._generation_inputs(unit,evidence3))
    assert key3!=key
