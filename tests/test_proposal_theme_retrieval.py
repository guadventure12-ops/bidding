"""Topic retrieval uses existing approved scope; isolated DB, no model/network."""
import copy
import hashlib
import json
import socket

import pytest

from app import db, proposal_context, provider, workflow as w


def digest(text):return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def sample(monkeypatch,tmp_path):
    monkeypatch.setenv('MX_TESTING','1');monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(db,'DATA',tmp_path/'data');db.init()
    def blocked(*a,**k):raise AssertionError('No network or model')
    monkeypatch.setattr(provider,'chat_json',blocked);monkeypatch.setattr(socket.socket,'connect',blocked);monkeypatch.setattr(socket,'getaddrinfo',blocked)
    w._proposal_evidence_index.cache_clear()
    source_text='产品按角色控制操作权限，按业务实体和账套配置数据权限，实现数据隔离。'
    docids=['allowed','pending','expense','generated','not-selected']
    for id in docids:
        metadata={'source_kind':'generated'} if id=='generated' else {}
        if id=='allowed':metadata={'knowledge_approval':{'source_sha256':digest(id),'entries':[{'id':'allowed-e','sha256':digest(source_text),'text':source_text}]}}
        db.insert('documents',{'id':id,'name':id+'.md','path':str(tmp_path/(id+'.md')),'source_type':'knowledge','status':'pending' if id=='pending' else 'approved',
            'parse_status':'ready','scope':'expense' if id=='expense' else 'archive','sha256':digest(id),'metadata':metadata,'created_at':db.now(),'updated_at':db.now()})
        db.insert('chunks',{'id':id+'-e','document_id':id,'ordinal':0,'kind':'paragraph','locator':'正文第1段','text':source_text})
    db.insert('chunks',{'id':'unapproved-part','document_id':'allowed','ordinal':1,'kind':'paragraph','locator':'正文第2段','text':'系统额外支持全部数据权限及越权管理，未被认可的原文部分。'})
    p={'id':'p','name':'合成项目','company_name':'合成企业','domain':'archive','metadata':{'generation_profile':'technical_proposal',
       'proposal_source_policy':{'mode':'approved_facts_only','fact_document_ids':docids[:-1],'reference_document_ids':[]}},'created_at':db.now(),'updated_at':db.now()}
    db.insert('projects',p)
    # Empty original requirement leaves the added query's contribution explicit.
    unit={'id':'s','title':'投标方案','requirements':[],'_proposal_spec':{'purpose':'说明实施目标','suggested_subtopics':['工作安排']},
          '_instruction':'角色授权、操作权限、业务实体和账套的数据权限及数据隔离，不要引用未知ID forged-id'}
    return w.project('p'),unit


def test_purpose_subtopics_and_instruction_are_bounded_unique_queries():
    unit={'title':'主题','outline_group_title':'类别','_instruction':'  明确修正   要求 ',
          '_proposal_spec':{'purpose':'目的','suggested_subtopics':['目的','子题','子题',None]+[str(i) for i in range(30)]}}
    rows=w._proposal_topic_queries(unit)
    assert rows[0]=={'kind':'instruction','query':'明确修正 要求'}
    assert len(rows)==2 and len({r['query'] for r in rows})==2
    assert all(word in rows[1]['query'] for word in ('目的','子题'))
    assert len(w._proposal_topic_queries({'_instruction':'x'*5000})[0]['query'])==3000


def test_instruction_adds_only_existing_approved_fact_text(sample):
    p,unit=sample;before=copy.deepcopy(unit)
    prompt,evidence,_=w._prepare_generation_request(p,unit)
    assert set(evidence)=={'allowed-e'}
    assert evidence['allowed-e']['_proposal_fact_boundary']['document_id']=='allowed'
    assert 'forged-id' not in evidence and '未被认可的原文部分' not in prompt
    provided=json.loads(prompt.rsplit('\n证据数据：\n',1)[1])
    assert [row['id'] for row in provided]==['E01'] and '数据隔离' in provided[0]['text']
    assert '相关性不是事实支持结论' in prompt
    assert unit==before and db.all('SELECT * FROM sections')==[] and db.all('SELECT * FROM jobs')==[]


def test_new_topic_source_is_rechecked_after_approval_changes(sample):
    p,unit=sample
    assert 'allowed-e' in w._prepare_generation_request(p,unit)[1]
    db.update('documents','allowed',{'status':'pending','updated_at':db.now()})
    assert w._prepare_generation_request(p,unit)[1]=={}


def test_no_instruction_themes_still_retrieve_and_do_not_create_requirements(sample):
    p,unit=sample;unit.pop('_instruction')
    unit['_proposal_spec']={'purpose':'按角色授权及业务实体账套配置数据权限','suggested_subtopics':['数据隔离']}
    prompt,evidence,_=w._prepare_generation_request(p,unit)
    assert 'allowed-e' in evidence
    assert json.loads(prompt.rsplit('\n要求数据：\n',1)[1].split('\n证据数据：\n',1)[0])==[]


def test_reference_roles_are_passed_through_unchanged(sample,monkeypatch):
    p,unit=sample
    hit={'id':'reference-e','document_name':'同项目拟方案','locator':'段落1','text':'拟按组织分工设计权限。','reference_role':'same_tender_proposed_plan'}
    monkeypatch.setattr(proposal_context,'search_context',lambda *a,**k:{'enabled':True,'evidence':[hit],'prompt_context':[]})
    prompt,evidence,_=w._prepare_generation_request(p,unit)
    assert evidence['reference-e']['reference_role']=='same_tender_proposed_plan'
    rows=json.loads(prompt.rsplit('\n证据数据：\n',1)[1])
    assert any(r['text']==hit['text'] and r['reference_role']==hit['reference_role'] for r in rows)


def test_legacy_request_does_not_add_proposal_theme_queries(sample,monkeypatch):
    p,unit=sample;p['metadata']={};calls=[]
    monkeypatch.setattr(w,'search_evidence',lambda *a,**k:calls.append((a,k)) or [])
    _,evidence,_=w._prepare_generation_request(p,unit)
    assert calls==[] and evidence=={}


def test_explicit_query_limits_keep_instruction_priority(sample,monkeypatch):
    p,unit=sample;calls=[]
    def lookup(query,domain,limit,project_id):calls.append((query,limit,project_id));return []
    monkeypatch.setattr(w,'search_evidence',lookup)
    w._prepare_generation_request(p,unit)
    assert calls[0][1:]==(6,'p')
    assert all(row[1:]==(3,'p') for row in calls[1:])
    assert len(calls)<=4


@pytest.mark.parametrize('title',['需求理解与解决方案','整体设计方案','系统集成方案'])
def test_archive_topics_get_two_default_domain_queries_only(title):
    unit={'title':title,'_proposal_spec':{'purpose':'按采购要求编写方案','suggested_subtopics':['范围','设计']}}
    rows=w._proposal_topic_queries(unit,'archive')
    hints=[r for r in rows if r['kind']=='domain_topic']
    assert len(hints)==2 and len(rows)==3
    words=' '.join(r['query'] for r in hints)
    assert ('接口异常队列' in words and '人工补采' in words) if title=='系统集成方案' else ('操作权限' in words and '档案类型' in words)
    assert not any(r['kind']=='domain_topic' for r in w._proposal_topic_queries(unit,'expense'))


def test_generic_module_does_not_receive_unrelated_archive_domain_hints():
    assert not any(r['kind']=='domain_topic' for r in w._proposal_topic_queries({'title':'数据安全','_proposal_spec':{}},'archive'))
