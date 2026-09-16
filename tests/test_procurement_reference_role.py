"""Procurement references are citable constraints, never enterprise facts."""
import copy
import hashlib
import json

import pytest

from app import content_review as cr, db, proposal_context as pc, proposal_numeric_review as nr, provider, workflow as w
from test_proposal_context import sample

TEXT='系统支持XBRL转换和100并发。'


@pytest.fixture
def procurement(sample):
    p,manifest,save=sample
    entry=manifest['entries'][1];entry.update(role='procurement_requirement',text=TEXT,text_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),chunk_sha256=hashlib.sha256(TEXT.encode()).hexdigest())
    db.update('chunks','plan-2',{'text':TEXT})
    document=db.one('SELECT * FROM documents WHERE id="plan"');metadata=document['metadata']
    selected=next(x for x in metadata['knowledge_approval']['entries'] if x['id']=='plan-2');selected.update(text=TEXT,sha256=entry['chunk_sha256'])
    db.update('documents','plan',{'metadata':metadata})
    manifest['groups'][1].update(role='procurement_requirement',title='采购约束',conditions=['仅证明采购要求'])
    p['company_name']='样例企业';p['metadata']['proposal_source_policy']={'mode':'approved_facts_only','fact_document_ids':['plan'],
        'fact_chunk_ids_by_document':{'plan':['plan-2']},'procurement_reference_chunk_ids':['plan-2']}
    save();db.update('projects','p',{'metadata':p['metadata'],'company_name':p['company_name']})
    w._proposal_evidence_index.cache_clear()
    return p,manifest,save


def role_row(id='q',text=TEXT,role='procurement_requirement'):
    return {'id':id,'text':text,'support_text':text,'document_name':'原文资料','reference_role':role,
            'evidence_kind':'proposal_reference' if role!='enterprise_fact' else 'original'}


def test_manifest_supports_new_role_and_preserves_existing_roles(procurement):
    p,_,_=procurement;loaded=pc.load_context(p)
    q=loaded['entries']['plan-2'];assert q['reference_role']=='procurement_requirement'
    assert q['claim_status']=='procurement_constraint_not_enterprise_capability'
    assert '不证明企业' in q['source_constraints']['modality']
    assert loaded['entries']['plan-1']['reference_role']=='same_tender_proposed_plan'
    assert loaded['entries']['resource-1']['reference_role']=='conditional_resource_recommendation'


@pytest.mark.parametrize('change',['approval','body','binding','manifest_hash'])
def test_new_role_retains_all_existing_validation(procurement,change):
    p,manifest,save=procurement
    if change=='approval':db.update('documents','plan',{'status':'pending'})
    elif change=='body':db.update('chunks','plan-2',{'text':'changed'})
    elif change=='binding':manifest['binding']['project_number']='another';save()
    else:p['metadata']['proposal_reference_context']['sha256']='0'*64
    with pytest.raises(pc.ContextValidationError):pc.load_context(p)


def test_same_id_residual_fact_never_wins_typed_role(procurement):
    p,_,_=procurement
    residual=w._candidate_evidence_rows('archive','p',['plan-2']);assert residual
    found=w._valid_evidence(['plan-2'],'archive','p')
    assert found[0]['reference_role']=='procurement_requirement'
    assert '_proposal_fact_boundary' not in found[0]


def test_bad_manifest_cannot_fall_back_to_residual_fact(procurement):
    p,_,_=procurement;p['metadata']['proposal_reference_context']['sha256']='0'*64
    db.update('projects','p',{'metadata':p['metadata']})
    assert w._valid_evidence(['plan-2'],'archive','p')==[]


def test_generation_prompt_and_evidence_keep_procurement_role(procurement):
    p,_,_=procurement
    unit={'id':'s','title':'技术方案','requirements':[{'id':'r','text':'系统支持XBRL转换','quote':'系统支持XBRL转换','mandatory':True,'score':'','category':'technical'}],
          '_proposal_spec':{'purpose':'说明需求及方案','content_kind':'narrative'}}
    prompt,evidence,_=w._prepare_generation_request(p,unit)
    assert evidence['plan-2']['reference_role']=='procurement_requirement'
    assert '不能证明企业能力' in prompt
    rows=json.loads(prompt.rsplit('\n证据数据：\n',1)[1])
    assert any(row['text']==TEXT and row['reference_role']=='procurement_requirement' for row in rows)
    assert db.one('SELECT * FROM documents WHERE id="plan"')['status']=='approved'


def test_trusted_reuse_excludes_procurement_but_keeps_real_product_facts():
    assert cr.trusted_reuse(TEXT,[role_row()])==[]
    assert cr.trusted_reuse(TEXT,[role_row('f',role='enterprise_fact')])==['f']


@pytest.mark.parametrize('text',['系统支持100并发。[E:q]','拟配置100并发处理能力。[E:q]'])
def test_procurement_numbers_never_enter_fact_or_planned_exemptions(text):
    found=nr.unsupported_numbers(text,[role_row()])
    assert found and any(x['code']=='numeric_procurement_not_enterprise_fact' for x in found)


def test_exact_procurement_numeric_quote_is_allowed_and_following_claim_is_not():
    quote='采购要求：系统支持XBRL转换和100并发。[E:q]'
    assert nr.unsupported_numbers(quote,[role_row()])==[]
    assert nr.unsupported_numbers(quote+'我方已实现100并发。[E:q]',[role_row()])
    assert nr.unsupported_numbers('系统支持XBRL转换和100并发。[E:f]',[role_row('f',role='enterprise_fact')])==[]


def test_nonnumeric_procurement_quote_does_not_excuse_independent_capability():
    q=role_row(text='系统支持XBRL转换。')
    quote='采购要求：系统支持XBRL转换。[E:q]'
    assert pc.procurement_claim_issues(quote,[q])==[]
    issue=pc.procurement_claim_issues(quote+'我方已实现XBRL转换。[E:q]',[q])
    assert len(issue)==1 and '我方已实现' in issue[0]['statement']
    assert pc.procurement_claim_issues('系统支持XBRL转换。[E:q]',[q])


def test_product_fact_can_support_literal_reuse_but_unrelated_fact_cannot_launder_procurement():
    q=role_row(text='系统支持XBRL转换。');text='系统支持XBRL转换。[E:q][E:f]'
    assert pc.procurement_claim_issues(text,[q,role_row('f',q['text'],'enterprise_fact')])==[]
    assert pc.procurement_claim_issues(text,[q,role_row('f','系统支持发票字段筛选。','enterprise_fact')])


def test_existing_capability_review_reports_non_numeric_procurement_claim(procurement):
    p,_,_=procurement
    s={'id':'s','project_id':'p','ordinal':0,'title':'技术方案','content':'系统支持XBRL转换。[E:plan-2]',
       'status':'draft','requirement_ids':[],'evidence_ids':['plan-2'],'created_at':db.now(),'updated_at':db.now()}
    db.insert('sections',s)
    result=cr.inspect_section(s,p,requirements=[],findings=[])
    assert any('采购要求复述' in reason for reason in result['blockers'])
    assert db.one('SELECT * FROM sections WHERE id="s"')['content']==s['content']
