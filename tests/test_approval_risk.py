"""BUG-3 matrix and persistence on synthetic data; no paid model calls."""
import pytest
from fastapi.testclient import TestClient
from app import db, provider, workflow, content_review as cr, approval_risk
from app.main import app


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1')
    monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(provider,'key_configured',lambda:False)
    monkeypatch.setattr(provider,'chat_json',lambda *a,**k:pytest.fail('paid model forbidden'))
    db.init()
    db.insert('projects',{'id':'p','name':'风险测试','company_name':'合成企业','created_at':db.now(),'updated_at':db.now()})
    for ordinal,(id,text) in enumerate([('low','按采购程序完成本章节编排。'),('medium','系统支持档案检索。'),('high','')]):
        db.insert('sections',{'id':id,'project_id':'p','ordinal':ordinal,'title':id,'content':text,'created_at':db.now(),'updated_at':db.now()})
    workflow.refresh_project_basics('p')
    return TestClient(app)


@pytest.mark.parametrize('threshold,allowed',[
    ('low',[]),('medium',['low']),('high',['low','medium']),('ignore',['low','medium','high'])
])
def test_threshold_matrix_single_and_batch(local,threshold,allowed):
    assert local.patch('/api/settings',json={'section_approval_threshold':threshold}).status_code==200
    assert local.get('/api/settings').json()['section_approval_threshold']==threshold
    rows,_=cr.assessments('p')
    assert {r['section_id']:r['risk_level'] for r in rows}=={x:x for x in ('low','medium','high')}
    assert {r['section_id'] for r in rows if r['eligible']}==set(allowed)
    plan=cr.preview('p',['low','medium','high'],'approve')
    assert plan['summary']['eligible']==len(allowed)
    assert plan['summary']['risk_counts']=={'low':1,'medium':1,'high':1}
    # Each direct endpoint uses the exact same rule as the batch preview.
    for id in ('low','medium','high'):
        response=local.patch('/api/sections/'+id,json={'status':'approved'})
        assert response.status_code==(200 if id in allowed else 400),response.text
        if id in allowed:
            record=db.one('SELECT * FROM projects WHERE id="p"')['metadata']['section_approvals'][id]
            assert record['threshold']==threshold and record['risk_level']==id
        db.update('sections',id,{'status':'draft'})
    plan=cr.preview('p',['low','medium','high'],'approve')
    result=cr.execute('p',['low','medium','high'],'approve',plan['token'],True)
    assert result['approved']==len(allowed) and result['skipped']==3-len(allowed)
    assert {s['id'] for s in db.all('SELECT * FROM sections WHERE status="approved"')}==set(allowed)
    # Reload through the real API, not only the Python assessment function.
    data=local.get('/api/projects/p').json()
    assert {s['id'] for s in data['sections'] if s['status']=='approved'}==set(allowed)
    assert all(r['risk_label'] in ('低风险','中风险','高风险') for r in data['section_risks'])


def test_default_does_not_change_any_status(local):
    assert local.get('/api/settings').json()['section_approval_threshold']=='medium'
    before=db.all('SELECT * FROM sections ORDER BY id')
    assert local.patch('/api/settings',json={'section_approval_threshold':'ignore'}).status_code==200
    assert db.all('SELECT * FROM sections ORDER BY id')==before


def test_policy_change_invalidates_preview(local):
    plan=cr.preview('p',['high'],'approve')
    local.patch('/api/settings',json={'section_approval_threshold':'ignore'})
    with pytest.raises(ValueError,match='变化'):
        cr.execute('p',['high'],'approve',plan['token'],True)
    assert db.one('SELECT status FROM sections WHERE id="high"')['status']=='draft'


def test_policy_changed_during_preview_cannot_mix_result_and_token(local,monkeypatch):
    original=cr.assessments
    def change_after_assessment(*args,**kwargs):
        result=original(*args,**kwargs)
        db.set_setting('section_approval_threshold','ignore')
        return result
    monkeypatch.setattr(cr,'assessments',change_after_assessment)
    with pytest.raises(ValueError,match='预览期间'):
        cr.preview('p',['high'],'approve')
    assert db.one('SELECT status FROM sections WHERE id="high"')['status']=='draft'


def test_corrupt_nonstring_policy_fails_closed(local):
    db.set_setting('section_approval_threshold',{'bad':'ignore'})
    assert approval_risk.policy()=='low'
    assert not any(r['eligible'] for r in cr.assessments('p')[0])


def test_ignore_never_clears_findings_or_opens_delivery(local):
    todo={'id':'t','kind':'content_conflict','message':'实际参数冲突','section_ids':['high'],'requirement_ids':[],
          'blocks_content':True,'blocks_delivery':True,'status':'open','origin':'model_review'}
    db.update('projects','p',{'metadata':{'content_todos':[todo]}})
    db.insert('reviews',{'id':'review','project_id':'p','status':'complete','fingerprint':'old','findings':[{'reason':'历史冲突','verdict':'contradiction','section_id':'high'}],'created_at':db.now()})
    history=db.all('SELECT * FROM reviews')
    local.patch('/api/settings',json={'section_approval_threshold':'ignore'})
    assert local.patch('/api/sections/high',json={'status':'approved'}).status_code==200
    high=cr.approval(db.one('SELECT * FROM sections WHERE id="high"'))
    assert high['eligible'] and high['risk_level']=='high' and high['blockers']
    assert db.all('SELECT * FROM reviews')==history
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']==[todo]
    assert any(c['code']=='section_gap' and c['severity']=='error' for c in workflow.review_project('p'))
    with pytest.raises(ValueError,match='正式导出'):
        workflow.export_project('p','md',True)
    edited=local.patch('/api/sections/high',json={'content':'修改后的正式正文内容。'})
    assert edited.status_code==200 and edited.json()['section']['status']=='draft'


def test_invalid_setting_and_busy_workspace(local):
    assert local.patch('/api/settings',json={'section_approval_threshold':'typo'}).status_code==422
    assert local.patch('/api/settings',json={'section_approval_threshold':3}).status_code==422
    db.insert('jobs',{'id':'busy','project_id':'p','mode':'generate','created_at':db.now()})
    assert local.patch('/api/settings',json={'section_approval_threshold':'ignore'}).status_code==400
    assert local.get('/api/settings').json()['section_approval_threshold']=='medium'


def test_settings_only_changes_selected_key(local):
    old=db.get_settings()
    local.patch('/api/settings',json={'section_approval_threshold':'high'})
    current=db.get_settings()
    assert {k:v for k,v in old.items() if k!='section_approval_threshold'}=={k:v for k,v in current.items() if k!='section_approval_threshold'}


def test_already_approved_is_skipped_and_risk_record_survives(local):
    local.patch('/api/settings',json={'section_approval_threshold':'ignore'})
    plan=cr.preview('p',['high'],'approve')
    cr.execute('p',['high'],'approve',plan['token'],True)
    before=db.one('SELECT * FROM sections WHERE id="high"')
    plan=cr.preview('p',['high'],'approve')
    result=cr.execute('p',['high'],'approve',plan['token'],True)
    assert result['approved']==0 and result['skipped']==1
    assert db.one('SELECT * FROM sections WHERE id="high"')==before
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']['section_approvals']['high']['accepted_risk']
