"""Exact model-boundary IDs, isolated cache/DB, mock provider, no network."""
import copy
import json
import socket

import pytest

from app import db, model_jobs, provider, review_rules, workflow as w

GOOD='ac0903699c188cee21920ef7d4a2f254'
TYPO='ac0903699c188cee91920ef7d4a2f254'
OTHER='1234567890abcdef1234567890abcdef'


def test_repeated_evidence_namespace_resolves_only_a_provided_alias(isolated):
    raw=result([GOOD,OTHER],content='软件提供档案查询。[E:E:E01]',evidence_ids=('E:E01',))
    original=copy.deepcopy(raw)
    resolved=w._resolve_generation_aliases(raw,{'actual-source':{}})
    assert resolved['content']=='软件提供档案查询。[E:actual-source]'
    assert all(r['evidence_ids']==['actual-source'] for r in resolved['responses'])
    assert raw==original
    raw['responses'][0]['evidence_ids']=['E:E99']
    resolved=w._resolve_generation_aliases(raw,{'actual-source':{}})
    with pytest.raises(provider.ProviderError,match='不存在或未提供'):
        w._validate_generation_result(section(),{'actual-source':{}},resolved)


@pytest.fixture
def isolated(monkeypatch,tmp_path):
    monkeypatch.setattr(db,'DATA',tmp_path/'data')
    monkeypatch.setenv('MX_TESTING','1');monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    db.init()
    def blocked(*args,**kwargs):raise AssertionError('No network allowed')
    monkeypatch.setattr(socket.socket,'connect',blocked);monkeypatch.setattr(socket,'getaddrinfo',blocked)
    calls=[]
    def install(*responses):
        values=iter(responses)
        def model(system,prompt,cancel=None):
            calls.append(prompt);return copy.deepcopy(next(values))
        monkeypatch.setattr(provider,'chat_json',model)
    return calls,install


def section(ids=(GOOD,OTHER),proposal=True):
    value={'id':'section','title':'发票管理','requirements':[{'id':id,'title':'规则'+str(i),'text':'对应发票校验规则'+str(i),
        'quote':'采购规则'+str(i),'category':'technical','mandatory':False,'score':''} for i,id in enumerate(ids)]}
    if proposal:value['_proposal_spec']={'content_kind':'narrative'}
    return value


def prompt(target,evidence=None):
    return '生成本节\n要求数据：\n'+json.dumps(target['requirements'],ensure_ascii=False)+'\n证据数据：\n'+json.dumps(evidence or [],ensure_ascii=False)


def result(ids,content='依据发票管理流程组织校验与异常处理。',evidence_ids=()):
    return {'content':content,'responses':[{'requirement_id':id,'response':content,'evidence_ids':list(evidence_ids),'gap':False,'gap_reason':''} for id in ids]}


def test_model_gets_only_requirement_aliases_and_database_ids_are_restored(isolated):
    calls,install=isolated;target=section();before=copy.deepcopy(target)
    raw=result(['R01','R02']);install(raw)
    saved=w._generate_with_repairs('j','k',target,{},prompt(target))
    data=json.loads(calls[0].rsplit('要求数据：\n',1)[1].split('\n证据数据：\n',1)[0])
    assert [row['id'] for row in data]==['R01','R02']
    assert GOOD not in calls[0] and OTHER not in calls[0]
    assert [row['requirement_id'] for row in saved['responses']]==[GOOD,OTHER]
    assert target==before and raw['responses'][0]['requirement_id']=='R01'
    assert db.all('SELECT * FROM sections')==[] and db.all('SELECT * FROM requirements')==[]


def test_correct_canonical_cache_is_revalidated_without_provider_call(isolated):
    calls,install=isolated;target=section();old=prompt(target)
    install(result([GOOD,OTHER]))
    model_jobs.chat_json('earlier','generation','k',w._operation_system(),old,w._generation_inputs(target,{}))
    calls.clear();install()
    saved=w._generate_with_repairs('new','k',target,{},old)
    assert calls==[] and saved['_cached_response'] is True
    assert [r['requirement_id'] for r in saved['responses']]==[GOOD,OTHER]


def test_alias_response_cache_is_reused_for_new_job(isolated):
    calls,install=isolated;target=section();install(result(['R01','R02']))
    first=w._generate_with_repairs('first','k',target,{},prompt(target))
    second=w._generate_with_repairs('second','k',target,{},prompt(target))
    assert len(calls)==1 and second['_cached_response'] is True
    assert first['responses']==second['responses']


def test_one_character_typo_not_guessed_repair_lists_missing_and_unknown(isolated):
    calls,install=isolated;target=section();install(result([TYPO,OTHER]),result(['R01','R02']))
    saved=w._generate_with_repairs('j','k',target,{},prompt(target))
    assert len(calls)==2
    repair=calls[1];detail=json.loads(repair.split('覆盖校验明细：\n',1)[1].split('\n本次失败回复和原因',1)[0])
    assert detail['missing_requirement_ids']==['R01'] and detail['unknown_requirement_ids']==[TYPO]
    assert GOOD not in repair and TYPO in repair
    assert [r['requirement_id'] for r in saved['responses']]==[GOOD,OTHER]


@pytest.mark.parametrize('unknown',['R1','r01','R99',TYPO,None,[]])
def test_unknown_ids_remain_rejected_after_bounded_repairs(isolated,unknown):
    calls,install=isolated;target=section();bad=result([unknown,'R02'])
    install(bad,bad,bad)
    with pytest.raises(provider.ProviderError,match='最多2次修复'):
        w._generate_with_repairs('j','k',target,{},prompt(target))
    assert len(calls)==3 and db.all('SELECT * FROM sections')==[]


def test_duplicate_and_missing_aliases_are_reported_without_filling_rows(isolated):
    calls,install=isolated;target=section();bad=result(['R01','R01'])
    install(bad,bad,bad)
    with pytest.raises(provider.ProviderError,match='最多2次修复'):
        w._generate_with_repairs('j','k',target,{},prompt(target))
    detail=w._proposal_requirement_coverage(bad,w._proposal_requirement_aliases(target))
    assert detail['duplicate_requirement_ids']==['R01'] and detail['missing_requirement_ids']==['R02']


def test_mixed_correct_canonical_and_alias_preserve_order_and_evidence(isolated):
    _,install=isolated;target=section();eid='evidence-source-id'
    hit={'id':eid,'text':'产品提供发票查重规则。','document_name':'已认可产品资料','locator':'正文第1段'}
    raw=result([GOOD,'R02'],'产品提供发票查重规则。[E:E01]',['E01']);install(raw)
    saved=w._generate_with_repairs('j','k',target,{eid:hit},prompt(target,[{'id':'E01','text':hit['text']}]))
    assert [r['requirement_id'] for r in saved['responses']]==[GOOD,OTHER]
    assert all(r['evidence_ids']==[eid] for r in saved['responses'])
    assert saved['content'].endswith('[E:'+eid+']')


@pytest.mark.parametrize('with_support',[False,True])
def test_alias_repair_still_requires_exact_citation_support(isolated,with_support):
    calls,install=isolated;target=section();eid='evidence-source-id'
    hit={'id':eid,'text':'产品提供发票查重规则。','document_name':'已认可产品资料','locator':'正文第1段'}
    bad=result([TYPO,OTHER],'产品提供发票查重规则。[E:E01]',['E01'])
    repaired=result(['R01','R02'],'产品提供发票查重规则。[E:E01]',['E01'])
    if with_support:repaired['citation_support']=[{'evidence_id':'E01','quote':hit['text']}]
    install(bad,repaired,repaired)
    request=prompt(target,[{'id':'E01','text':hit['text']}])
    if with_support:
        saved=w._generate_with_repairs('j','k',target,{eid:hit},request)
        assert len(calls)==2 and saved['responses'][0]['evidence_ids']==[eid]
    else:
        with pytest.raises(provider.ProviderError,match='引用修复须提供'):
            w._generate_with_repairs('j','k',target,{eid:hit},request)
        assert len(calls)==3


def test_unknown_extra_alias_rejected_even_when_legacy_gates_off(isolated):
    calls,install=isolated;target=section(ids=());bad=result(['R99'])
    install(bad,bad,bad)
    with review_rules.scope({key:False for key in review_rules.IDS}),pytest.raises(provider.ProviderError,match='未知 requirement_id'):
        w._generate_with_repairs('j','k',target,{},prompt(target))
    assert len(calls)==3


def test_legacy_profile_keeps_original_prompt_and_cache_path(isolated,monkeypatch):
    calls,install=isolated;target=section(proposal=False);install(result([GOOD,OTHER]))
    def forbidden(*args,**kwargs):raise AssertionError('Legacy request must keep existing path')
    monkeypatch.setattr(model_jobs,'read_response_for_repair',forbidden)
    w._generate_with_repairs('j','k',target,{},prompt(target))
    assert calls==[prompt(target)]


def test_bad_request_mapping_rejected_before_model_or_cache(isolated):
    calls,install=isolated;target=section();install()
    with pytest.raises(provider.ProviderError,match='要求数据与本节范围不一致'):
        w._generate_with_repairs('j','k',target,{},prompt(section(ids=(OTHER,))))
    assert calls==[]
