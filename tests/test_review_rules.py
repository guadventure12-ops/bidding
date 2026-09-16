"""BUG-4 isolated rule toggles, documentation and current-check projection."""
import copy
import pytest
from fastapi.testclient import TestClient
from app import db, provider, workflow, content_review as cr, review_rules
from app.main import app


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1')
    monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(provider,'key_configured',lambda:False)
    monkeypatch.setattr(provider,'chat_json',lambda *a,**k:pytest.fail('no model allowed'))
    db.init()
    db.insert('projects',{'id':'p','name':'审核规则测试','company_name':'测试企业','created_at':db.now(),'updated_at':db.now()})
    db.insert('sections',{'id':'s','project_id':'p','title':'方案','ordinal':0,'content':'按采购流程完成本章内容编排。','created_at':db.now(),'updated_at':db.now()})
    workflow.refresh_project_basics('p')
    return TestClient(app)


def toggle(client, rule, value):
    current=client.get('/api/review-settings').json()
    response=client.patch('/api/review-settings',json={'revision':current['revision'],'enabled':{rule:value}})
    assert response.status_code==200,response.text
    return response.json()


def put_todo(kind,message,content=True):
    t=cr.todo(kind,message,db.one('SELECT * FROM sections WHERE id="s"'),content=content,origin='generation')
    meta = db.one('SELECT * FROM projects WHERE id="p"')['metadata']
    meta['content_todos'] = [t]
    db.update('projects','p',{'metadata':meta})
    return t


def test_catalog_covers_backend_checks_and_documented_defaults(local):
    result=local.get('/api/review-settings').json()
    assert len(result['rules'])>50 and all(result['enabled'].values())
    assert {'正文为空','内部包装文字','能力描述与资料不一致','报价字段缺失','附件／签章未完成'} <= {r['name'] for r in result['rules']}
    import ast
    from pathlib import Path
    tree = ast.parse(Path(workflow.__file__).read_text(encoding='utf-8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='review_project')
    calls=[n for n in ast.walk(function) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='add' and n.args and isinstance(n.args[0],ast.Constant)]
    # An explicit rule_id binds a check to an existing user-visible rule; older
    # checks without one must remain covered by the catalog's code mapping.
    for call in calls:
        explicit=next((k.value for k in call.keywords if k.arg=='rule_id'),None)
        if isinstance(explicit,ast.Constant):
            assert explicit.value in review_rules.IDS
        else:
            assert call.args[0].value in review_rules.CHECK_RULES
    assert len({r['id'] for r in result['rules']})==len(result['rules'])
    for rule in result['rules']:
        file,name=rule['source'].split(':',1)
        name=name.split(' / ')[0]
        source=ast.parse(Path(file).read_text(encoding='utf-8'))
        assert any(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name for n in ast.walk(source)),rule['source']
    for rule in result['rules']:
        assert all(rule[k] for k in ('description','triggers','exceptions','version','correct_example','false_positive_example'))
    assert local.get('/').status_code==200
    assert '审核设置' in local.get('/').text


@pytest.mark.parametrize('rule,body,todo',[
    ('empty_body','',None),
    ('internal_wrapper','正式章节说明。\n拟用文本：',None),
    ('capability_mismatch','系统支持档案检索。',None),
    ('quotation_missing','完整章节说明。',('content_gap','待补充：实际报价金额')),
])
def test_off_on_recomputes_risk_without_changing_body_or_pending(local,rule,body,todo):
    db.update('sections','s',{'content':body})
    if todo:put_todo(*todo)
    before=db.one('SELECT * FROM sections WHERE id="s"')
    before_meta=copy.deepcopy(db.one('SELECT * FROM projects WHERE id="p"')['metadata'])
    assert not cr.approval(before)['eligible']
    toggle(local,rule,False)
    after=cr.approval(before)
    assert after['eligible'] and after['risk_level']=='low',after
    assert local.get('/api/projects/p').json()['section_risks'][0]['risk_level']=='low'
    assert db.one('SELECT * FROM sections WHERE id="s"')==before
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']==before_meta
    toggle(local,rule,True)
    assert not cr.approval(before)['eligible']


def test_attachment_disabled_is_not_completed(local):
    original=put_todo('delivery','附件盖章未完成',False)
    on=workflow.review_project('p',persist=False)
    assert any(c['code']=='content_todo' and c['severity']=='error' for c in on)
    toggle(local,'attachments_incomplete',False)
    off=local.get('/api/projects/p').json()['checks']
    matched=[c for c in off if c['code']=='content_todo']
    assert matched and all(c['severity']=='info' and c['details']['rule_enabled'] is False for c in matched)
    assert db.one('SELECT * FROM projects WHERE id="p"')['metadata']['content_todos']==[original]
    items=cr.assessments('p')[1]
    assert items[0]['status']=='open' and items[0]['rule_enabled'] is False


def test_mixed_field_and_signature_cannot_be_hidden_by_attachment_switch(local):
    put_todo('content_gap','法定代表人姓名及签字信息缺失')
    toggle(local,'attachments_incomplete',False)
    assert not cr.approval(db.one('SELECT * FROM sections WHERE id="s"'))['eligible']


def test_ai_history_preserved_but_configured_capability_finding_inactive(local):
    db.insert('reviews',{'id':'review','project_id':'p','fingerprint':workflow.review_fingerprint('p'),
        'status':'complete','findings':[{'target_type':'section','section_id':'s','verdict':'contradiction','severity':'error','reason':'产品仅支持20并发，正文为100并发。'}],'created_at':db.now()})
    before=db.all('SELECT * FROM reviews')
    assert any(c['code']=='ai_evidence_review' and c['severity']=='error' for c in workflow.review_project('p',persist=False))
    toggle(local,'capability_mismatch',False)
    assert any(c['code']=='ai_evidence_review' and c['severity']=='info' for c in workflow.review_project('p',persist=False))
    assert db.all('SELECT * FROM reviews')==before


def test_quote_response_gap_and_number_rule_are_gated(local):
    db.insert('requirements',{'id':'r','project_id':'p','title':'报价','category':'pricing','text':'填写报价','response':'','status':'gap','created_at':db.now(),'updated_at':db.now()})
    assert any(c['code']=='response_gaps' and c['severity']=='error' for c in workflow.review_project('p',persist=False))
    toggle(local,'quotation_missing',False)
    checks=workflow.review_project('p',persist=False)
    assert not any(c['code']=='response_gaps' and c['severity']=='error' for c in checks)
    assert any(c['code']=='responses_unconfirmed' and c['severity']=='error' for c in checks)
    db.update('sections','s',{'content':'承诺100并发用户。'})
    assert any(c['code']=='unverified_numbers' and c['severity']=='error' for c in workflow.review_project('p',persist=False))
    toggle(local,'unverified_numbers',False)
    assert not any(c['code']=='unverified_numbers' and c['severity']=='error' for c in workflow.review_project('p',persist=False))


def test_single_batch_policy_and_revision_use_same_enabled_rules(local):
    db.update('sections','s',{'content':''})
    old=cr.preview('p',['s'],'approve')
    toggle(local,'empty_body',False)
    with pytest.raises(ValueError,match='变化'):cr.execute('p',['s'],'approve',old['token'],True)
    assert local.patch('/api/sections/s',json={'status':'approved'}).status_code==200
    db.update('sections','s',{'status':'draft'})
    plan=cr.preview('p',['s'],'approve')
    assert plan['summary']['eligible']==1
    assert cr.execute('p',['s'],'approve',plan['token'],True)['approved']==1


def test_stale_form_unknown_keys_and_nonboolean_rejected(local):
    old=local.get('/api/review-settings').json()
    toggle(local,'empty_body',False)
    assert local.patch('/api/review-settings',json={'revision':old['revision'],'enabled':{'empty_body':True}}).status_code==400
    now=local.get('/api/review-settings').json()
    for values in ({'all':False},{'empty_body':'false'},{'empty_body':0},{}, {'empty_body':None}):
        assert local.patch('/api/review-settings',json={'revision':now['revision'],'enabled':values}).status_code==422


def test_busy_write_rejected_and_only_rules_setting_changes(local):
    before=db.get_settings()
    toggle(local,'empty_body',False)
    after=db.get_settings()
    assert all(after[k]==v for k,v in before.items())
    db.insert('jobs',{'id':'busy','project_id':'p','mode':'review','created_at':db.now()})
    value=local.get('/api/review-settings').json()
    assert local.patch('/api/review-settings',json={'revision':value['revision'],'enabled':{'empty_body':True}}).status_code==400


def test_all_off_disables_checks_without_automatic_approval(local):
    before=db.one('SELECT * FROM sections WHERE id="s"')
    config=local.get('/api/review-settings').json()
    assert local.patch('/api/review-settings',json={'revision':config['revision'],'enabled':{id:False for id in review_rules.EDITABLE}}).status_code==200
    assert db.one('SELECT * FROM sections WHERE id="s"')==before
    db.update('sections','s',{'content':'正文说明 [E:nonexistent]','evidence_ids':[]})
    assert cr.approval(db.one('SELECT * FROM sections WHERE id="s"'))['eligible']
    assert not any(c['severity']=='error' for c in workflow.review_project('p',persist=False))
    assert db.one('SELECT status FROM sections WHERE id="s"')['status']=='draft'


def test_current_check_projection_does_not_write_cached_or_business_rows(local):
    workflow.review_project('p')
    before={t:db.all('SELECT * FROM '+t+' ORDER BY id') for t in ('checks','projects','sections','requirements','reviews','exports')}
    toggle(local,'empty_body',False)
    workflow.review_project('p',persist=False)
    assert before=={t:db.all('SELECT * FROM '+t+' ORDER BY id') for t in before}


def test_rule_changes_during_preview_rejected(local,monkeypatch):
    original=cr.assessments
    def changed(*a,**kw):
        result=original(*a,**kw)
        db.set_setting('review_rules',{'empty_body':False})
        return result
    monkeypatch.setattr(cr,'assessments',changed)
    with pytest.raises(ValueError,match='预览期间'):cr.preview('p',['s'],'approve')


@pytest.mark.parametrize('code',['no_tender','project_basics_missing','analysis_incomplete','no_requirements','sections_unapproved','model_review_missing'])
def test_each_new_backend_check_has_working_toggle(local,code):
    on=workflow.review_project('p',persist=False)
    assert any(c['code']==code and c['severity']=='error' for c in on)
    toggle(local,code,False)
    off=workflow.review_project('p',persist=False)
    assert any(c['code']==code and c['severity']=='info' and not c['details']['rule_enabled'] for c in off)


def test_quote_switch_does_not_hide_amount_recognition_capability(local):
    put_todo('support_review','核对系统支持金额识别与报价计算的资料依据')
    toggle(local,'quotation_missing',False)
    assessment=cr.approval(db.one('SELECT * FROM sections WHERE id="s"'))
    assert not assessment['eligible']
    assert any(t['rule_id']=='capability_mismatch' and t['rule_enabled'] for t in assessment['todos'])


def test_pure_legacy_template_is_controlled_by_wrapper_switch(local):
    db.update('sections','s',{'content':'完整方案正文。\n【待企业确认的承诺模板】'})
    assert not cr.approval(db.one('SELECT * FROM sections WHERE id="s"'))['eligible']
    toggle(local,'internal_wrapper',False)
    assert cr.approval(db.one('SELECT * FROM sections WHERE id="s"'))['eligible']


@pytest.mark.parametrize('reason,rule',[
    ('缺少营业执照复印件，尚未签章','attachments_incomplete'),
    ('缺少实际报价总金额','quotation_missing'),
    ('章节正文为空，无法核对','empty_body'),
    ('正文含内部包装和编制说明','internal_wrapper'),
    ('资料不能支持100并发能力','capability_mismatch'),
    ('缺少企业资料证明系统支持自动报价功能','capability_mismatch'),
    ('这是一条未知类型问题','ai_other_finding'),
])
def test_ai_findings_use_actual_issue_rule(reason,rule):
    assert review_rules.model_finding_rule({'verdict':'uncertain','reason':reason})==rule


def test_formerly_fixed_rules_are_now_configurable(local):
    data=local.get('/api/review-settings').json()
    fixed=next(r for r in data['rules'] if r['id']=='generation_coverage')
    assert fixed['editable'] and fixed['enabled']
    assert local.patch('/api/review-settings',json={'revision':data['revision'],'enabled':{'generation_coverage':False}}).status_code==200
