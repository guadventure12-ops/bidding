import copy
import hashlib
import json
from pathlib import Path

import pytest

from app import db, proposal_context as pc, review_rules


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def sample(tmp_path, monkeypatch):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1');monkeypatch.setenv('LANGFUSE_ENABLED','false')
    db.init();(db.DATA/'assets').mkdir()
    p={'id':'p','name':'Sample','domain':'archive','project_number':'ZJZT-2026-14825','created_at':db.now(),'updated_at':db.now(),'metadata':{'generation_profile':'technical_proposal'}}
    db.insert('projects',p)
    def document(id,text,source_type='knowledge',project_id=None,approval=False):
        path=tmp_path/(id+'.txt');path.write_text(text,encoding='utf-8')
        doc={'id':id,'name':id+'.txt','path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'source_type':source_type,'project_id':project_id,
             'status':'approved','scope':'archive','parse_status':'ready','valid_until':'','created_at':db.now(),'updated_at':db.now(),'metadata':{}}
        if approval:doc['metadata']={'knowledge_approval':{'mode':'partial','source_sha256':doc['sha256'],'entries':[]}}
        db.insert('documents',doc);return doc
    tender=document('tender','original tender','tender','p')
    plan=document('plan','source plan bytes',approval=True)
    resource=document('resource','source resource bytes')
    rows=[('plan-1','plan','拟按角色组织管理员培训。\n未经认可的新增成绩。','拟按角色组织管理员培训。','same_tender_proposed_plan',['知识转移与培训','管理员培训']),
          ('plan-2','plan','拟开展故障分析与巡检。','拟开展故障分析与巡检。','same_tender_proposed_plan',['售后运维服务方案','巡检']),
          ('resource-1','resource','年凭证10万以下（按分录行量调整）','年凭证10万以下（按分录行量调整）','conditional_resource_recommendation',['资源与部署','虚拟化服务器']),
          ('resource-2','resource','节点 | 数量 | CPU | 备注\n主应用 | 1 | 4核 | 参考配置，可按负载扩展','节点 | 数量 | CPU | 备注\n主应用 | 1 | 4核 | 参考配置，可按负载扩展','conditional_resource_recommendation',['资源与部署','虚拟化服务器'])]
    entries=[]
    for i,(id,did,text,approved,role,topic) in enumerate(rows):
        db.insert('chunks',{'id':id,'document_id':did,'ordinal':i,'text':text,'kind':'paragraph','locator':f'段落{i+1}'})
        entries.append({'chunk_id':id,'document_id':did,'locator':f'段落{i+1}','chunk_sha256':sha(text),'text_sha256':sha(approved),'text':approved,'role':role,'topic_path':topic})
        if did=='plan':plan['metadata']['knowledge_approval']['entries'].append({'id':id,'sha256':sha(text),'text':approved})
    db.update('documents','plan',{'metadata':plan['metadata']})
    manifest={'version':pc.VERSION,'binding':{'project_number':p['project_number'],'tender_sha256s':[tender['sha256']]},
              'sources':[{'document_id':'plan','source_sha256':plan['sha256'],'scope':'archive','approval_basis':'parsed_entries'},
                         {'document_id':'resource','source_sha256':resource['sha256'],'scope':'archive','approval_basis':'approved_legacy_document'}],
              'entries':entries,'groups':[
                  {'id':'g1','role':'same_tender_proposed_plan','topic_path':['知识转移与培训','管理员培训'],'title':'管理员培训','conditions':['拟实施，人员未任命'],'chunk_ids':['plan-1']},
                  {'id':'g2','role':'same_tender_proposed_plan','topic_path':['售后运维服务方案','巡检'],'title':'故障巡检','conditions':['拟开展'],'chunk_ids':['plan-2']},
                  {'id':'g3','role':'conditional_resource_recommendation','topic_path':['资源与部署','虚拟化服务器'],'title':'按凭证量选择主应用节点资源部署','conditions':['参考配置，不是固定承诺'],'chunk_ids':['resource-1','resource-2']}]}
    def save(m=manifest):
        path=db.DATA/'assets'/'ref.json';encoded=json.dumps(m,ensure_ascii=False).encode();path.write_bytes(encoded)
        p['metadata']['proposal_reference_context']={'path':'assets/ref.json','sha256':hashlib.sha256(encoded).hexdigest()}
    save()
    return p,manifest,save


def test_profile_context_keeps_partial_approval_and_roles_with_every_rule_disabled(sample):
    p,m,save=sample
    with review_rules.scope({k:False for k in review_rules.IDS}):
        loaded=pc.load_context(p)
        found=pc.search_context(p,{'title':'管理员培训','outline_group_title':'知识转移与培训'},limit=1)
        assert all(not review_rules.active(k) for k in review_rules.IDS)
    assert loaded['diagnostics']['validated_entries']==4
    assert found['evidence'][0]['id']=='plan-1'
    assert found['evidence'][0]['text']=='拟按角色组织管理员培训。'
    assert found['evidence'][0]['reference_role']=='same_tender_proposed_plan'
    assert found['evidence'][0]['claim_status']=='proposed_not_executed_or_committed'
    assert '未经认可' not in str(found['prompt_context'])
    assert not found['evidence'][0]['source_constraints']['appointment_confirmed']


def test_resource_group_retains_condition_header_and_original_citation_ids(sample):
    p,m,save=sample
    result=pc.search_context(p,{'title':'服务器资源部署','outline_group_title':'整体设计方案'},limit=1)
    assert [e['id'] for e in result['evidence']]==['resource-1','resource-2']
    context=result['prompt_context'][0]
    assert context['role']=='conditional_resource_recommendation'
    assert '10万以下' in str(context) and 'CPU' in str(context) and '按负载扩展' in str(context)
    assert 'g3' not in str(context)
    limited=pc.search_context(p,{'title':'服务器部署'},limit=1,max_chars=2)
    assert not limited['evidence'] and limited['diagnostics']['skipped_groups_due_to_budget']
    resolved=pc.resolve_evidence(p,['resource-2','resource-2','missing'])
    assert len(resolved)==1 and resolved[0]['reference_role']=='conditional_resource_recommendation'


@pytest.mark.parametrize('kind',['project_number','tender_hash','new_tender','tender_file'])
def test_other_project_or_current_tender_changes_fail_closed(sample,kind):
    p,m,save=sample
    if kind=='project_number':p['project_number']='OTHER'
    elif kind=='tender_hash':db.update('documents','tender',{'sha256':'changed'})
    elif kind=='new_tender':
        d=db.one('SELECT * FROM documents WHERE id=?',('tender',));d.update(id='new',sha256='different');db.insert('documents',d)
    else:Path(db.one('SELECT * FROM documents WHERE id=?',('tender',))['path']).write_text('changed source bytes',encoding='utf-8')
    with pytest.raises(pc.ContextValidationError):pc.load_context(p)


@pytest.mark.parametrize('kind',['pending','parse','scope','expired','source_hash','source_file','chunk','locator','delete','approval_range','approval_hash','project_scope','trust'])
def test_changed_document_or_approval_range_fail_closed(sample,kind):
    p,m,save=sample
    doc=db.one('SELECT * FROM documents WHERE id=?',('plan',))
    if kind=='pending':db.update('documents','plan',{'status':'pending'})
    elif kind=='parse':db.update('documents','plan',{'parse_status':'error'})
    elif kind=='scope':db.update('documents','plan',{'scope':'expense'})
    elif kind=='expired':db.update('documents','plan',{'valid_until':'2000-01-01'})
    elif kind=='source_hash':db.update('documents','plan',{'sha256':'changed'})
    elif kind=='source_file':Path(doc['path']).write_text('new unparsed source version',encoding='utf-8')
    elif kind=='chunk':db.update('chunks','plan-1',{'text':'changed text'})
    elif kind=='locator':db.update('chunks','plan-1',{'locator':'different source position'})
    elif kind=='delete':db.execute('DELETE FROM chunks WHERE id=?',('plan-1',))
    elif kind=='trust':p['metadata']['trusted_sources']={}
    else:
        meta=doc['metadata']
        if kind=='approval_range':meta['knowledge_approval']['entries'][0]['text']='拟按角色组织管理员培训。\n未经认可的新增成绩。'
        elif kind=='approval_hash':meta['knowledge_approval']['source_sha256']='wrong'
        else:meta['project_ids']=['other-project']
        db.update('documents','plan',{'metadata':meta})
    with review_rules.scope({k:False for k in review_rules.IDS}):
        with pytest.raises(pc.ContextValidationError):pc.resolve_evidence(p,['plan-1'])


@pytest.mark.parametrize('kind',['hash','path','role','invented_text','group_missing_id'])
def test_manifest_tampering_or_unsafe_shape_rejected(sample,kind):
    p,m,save=sample
    if kind=='hash':(db.DATA/'assets'/'ref.json').write_text('{}')
    elif kind=='path':p['metadata']['proposal_reference_context']['path']='../outside.json'
    else:
        changed=copy.deepcopy(m)
        if kind=='role':changed['entries'][0]['role']='product_fact'
        elif kind=='invented_text':changed['entries'][0]['text']='已经完成项目培训'
        else:changed['groups'][0]['chunk_ids']=['invented']
        save(changed)
    with pytest.raises(pc.ContextValidationError):pc.load_context(p)


def test_non_profile_and_optional_context_do_not_change_legacy_behavior(sample):
    p,m,save=sample
    p['metadata']['generation_profile']='legacy'
    p['metadata']['proposal_reference_context']={'path':'does-not-exist','sha256':'x'}
    assert not pc.load_context(p)['enabled']
    assert pc.resolve_evidence(p,['plan-1'])==[]
    p['metadata']={'generation_profile':'technical_proposal'}
    assert not pc.search_context(p,{'title':'培训'})['enabled']


def test_operation_snapshot_reuse_does_not_repeat_loading_and_revalidates_before_commit(sample,monkeypatch):
    p,m,save=sample
    original=pc.load_context;calls=[]
    def counted(project):
        calls.append(project['id']);return original(project)
    monkeypatch.setattr(pc,'load_context',counted)
    with pc.context_scope(p) as context:
        for _ in range(800):assert pc.resolve_evidence(p,['plan-1'])[0]['id']=='plan-1'
        assert calls==['p']
        db.update('chunks','plan-1',{'text':'changed during the operation'})
        with pytest.raises(pc.ContextValidationError):pc.assert_context_current(p,context)
    assert len(calls)==2
    with pytest.raises(pc.ContextValidationError):pc.resolve_evidence(p,['plan-1'])


def test_preloaded_context_cannot_cross_project_or_profile_boundary(sample):
    p,m,save=sample;context=pc.load_context(p)
    other=copy.deepcopy(p);other['id']='other-project'
    with pytest.raises(pc.ContextValidationError):pc.resolve_evidence(other,['plan-1'],context=context)
    other=copy.deepcopy(p);other['metadata']['generation_profile']='legacy'
    with pytest.raises(pc.ContextValidationError):pc.search_context(other,{'title':'培训'},context=context)


def test_same_topic_plain_heading_does_not_displace_actual_short_plan(sample):
    p,m,save=sample
    body='产品培训：'
    db.update('chunks','plan-2',{'text':body})
    doc=db.one('SELECT * FROM documents WHERE id=?',('plan',))
    doc['metadata']['knowledge_approval']['entries'][1].update(text=body,sha256=sha(body))
    db.update('documents','plan',{'metadata':doc['metadata']})
    m['entries'][1].update(text=body,chunk_sha256=sha(body),text_sha256=sha(body),topic_path=['知识转移与培训','项目培训保障'])
    m['groups'][1].update(topic_path=['知识转移与培训','项目培训保障'],title='培训')
    save()
    result=pc.search_context(p,{'title':'培训','outline_group_title':'知识转移与培训'},limit=1)
    assert [e['id'] for e in result['evidence']]==['plan-1']
    assert pc.resolve_evidence(p,['plan-2'])[0]['text']==body  # Not erased or invalidated.
