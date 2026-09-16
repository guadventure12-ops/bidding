"""Rule switches exercised with synthetic data and stubbed model responses only."""
import copy
import json
import pytest
from app import db, workflow as w, provider, review_rules as rr, bid_body


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DATA', tmp_path)
    monkeypatch.setenv('MX_TESTING','1')
    monkeypatch.setenv('LANGFUSE_ENABLED','false')
    monkeypatch.setattr(provider, 'chat_json', lambda *a, **k: (_ for _ in ()).throw(AssertionError('paid model prohibited')))
    db.init()
    w._evidence_index.cache_clear()


def request():
    section={'title':'示例章','requirements':[{'id':'r','category':'technical','text':'需要检索','quote':'需要检索','title':'检索','mandatory':0,'score':''}]}
    result={'content':'方案正文','responses':[{'requirement_id':'r','response':'提供检索方案','evidence_ids':[]}]}
    return section,result


def test_coverage_off_keeps_only_actual_known_rows():
    section,result=request();section['requirements'].append({**section['requirements'][0],'id':'r2'})
    with pytest.raises(provider.ProviderError):w._validate_generation_result(section,{},result)
    with rr.scope({'generation_coverage':False}):
        _, rows, _, _ = w._validate_generation_result(section,{},result)
    assert [row['requirement_id'] for row in rows]==['r']
    assert result['responses'][0]['response']=='提供检索方案'


def test_schema_off_discards_invalid_rows_without_inventing_them():
    section,result=request();result['responses'].append('invalid')
    with pytest.raises(provider.ProviderError):w._validate_generation_result(section,{},result)
    with rr.scope({'generation_schema':False}):
        _, rows, _, _ = w._validate_generation_result(section,{},result)
    assert len(rows)==1
    with rr.scope({'generation_schema':False,'generation_coverage':False,'generation_body':False}):
        with pytest.raises(provider.ProviderError):w._validate_generation_result(section,{}, {'content':None,'responses':[]})


def test_unknown_and_malformed_citations_switch():
    section,result=request();result['content']='正文[E:unknown] 与未闭合[E:missing'
    with pytest.raises(provider.ProviderError):w._validate_generation_result(section,{},result)
    with rr.scope({'generation_citations':False}):
        normalized=w._resolve_generation_aliases(result,{})
        content,_,_,cited=w._validate_generation_result(section,{},normalized)
    assert content==result['content'] and cited=={'unknown'}


def test_repair_support_and_empty_body_are_independent():
    with pytest.raises(provider.ProviderError):w._validate_repaired_citation_support({}, {}, {'E1'}, 'bad prompt')
    with rr.scope({'repair_support':False}):w._validate_repaired_citation_support({}, {}, {'E1'}, 'bad prompt')
    section,result=request();result['content']=''
    with pytest.raises(provider.ProviderError):w._validate_generation_result(section,{},result)
    with rr.scope({'generation_body':False}):assert w._validate_generation_result(section,{},result)[0]==''


def test_separation_off_does_not_disable_pure_detection():
    _,result=request();result['content']='正文\n【待补充：人员姓名】'
    assert '待补充' not in bid_body.normalize_result(result)['content']
    with rr.scope({'body_separation':False}):
        assert bid_body.normalize_result(result)==result
        assert bid_body.separate(result['content'])['items']


@pytest.mark.parametrize('rule', list(w.PROMPT_RULE_TEXT))
def test_disabled_prompt_rule_is_absent_from_both_prompt_layers(rule,monkeypatch):
    monkeypatch.setattr(w,'search_evidence',lambda *a,**k:[])
    monkeypatch.setattr(w,'_requirement_source_context',lambda *a:{})
    p={'id':'p','company_name':'测试企业','name':'项目','domain':'archive','project_number':'p-1','buyer':'客户','deadline':''}
    section,_=request()
    with rr.scope({rule:False}):
        prompt,_,_=w._prepare_generation_request(p,section)
        review=w._review_prompt(p,[])
        assert w.PROMPT_RULE_TEXT[rule] not in prompt
        assert w.PROMPT_RULE_TEXT[rule] not in review
        assert w.PROMPT_RULE_TEXT[rule] not in w._operation_system()


@pytest.mark.parametrize('error',[provider.OutputTruncatedError,provider.OutputInterruptedError])
def test_complete_received_json_may_be_used_without_rebilling(error):
    calls=[]
    def model():
        calls.append(1);raise error('{"content":"已收到正文","responses":[]}',{'tokens':1})
    with pytest.raises(error):w._model_call(model)
    with rr.scope({'model_output_complete':False}):assert w._model_call(model)['content']=='已收到正文'
    assert len(calls)==2
    def malformed():raise error('{"content":')
    with rr.scope({'model_output_complete':False}):
        with pytest.raises(provider.ProviderError):w._model_call(malformed)


def test_partial_review_does_not_create_missing_assessments():
    batch=[{'target_id':'t1','target_type':'section','section_id':'s','title':'章'}, {'target_id':'t2','target_type':'section','section_id':'s2','title':'章2'}]
    result={'assessments':[{'target_id':'t1','verdict':'supported','reason':'已找到原文'}]}
    with pytest.raises(provider.ProviderError):w._validate_review_result(batch,result)
    with rr.scope({'review_target_coverage':False}):assert w._validate_review_result(batch,result)==[]
    assert result['_accepted_target_ids']==['t1'] and len(result['assessments'])==1
    result['assessments'].append({'target_id':'t2','verdict':'bad','reason':''})
    with rr.scope({'review_result_shape':False}):w._validate_review_result(batch,result)
    assert result['_accepted_target_ids']==['t1']


def test_extraction_quote_switch_retains_real_source_identity():
    batch=[{'id':'c','text':'必须提供真实档案检索','document_id':'d'}]
    items=[{'chunk_id':'c','quote':'改写后的检索要求','text':'改写后的检索要求'}]
    assert w._extraction_problems(copy.deepcopy(items),batch)[0]
    with rr.scope({'extraction_quote':False}):assert not w._extraction_problems(copy.deepcopy(items),batch)[0]
    items[0]['chunk_id']='nonexistent'
    with rr.scope({'extraction_quote':False}):assert w._extraction_problems(items,batch)[0]


def test_index_cache_identity_changes_with_rules(monkeypatch):
    monkeypatch.setattr(w,'knowledge_fingerprint',lambda:'unchanged')
    calls=[]
    monkeypatch.setattr(w,'_candidate_evidence_rows',lambda *a,**k:calls.append(rr.active('knowledge_scope')) or [])
    with rr.scope({'knowledge_scope':True}):w.search_evidence('档案')
    with rr.scope({'knowledge_scope':False}):w.search_evidence('档案')
    assert calls==[True,False]


def test_aggregate_rule_and_child_must_both_be_enabled(monkeypatch):
    db.insert('projects',{'id':'p','name':'test','domain':'archive','created_at':db.now(),'updated_at':db.now()})
    db.insert('requirements',{'id':'r','project_id':'p','category':'technical','title':'响应','text':'检索','status':'gap','created_at':db.now(),'updated_at':db.now()})
    db.set_setting('review_rules',{'response_gaps':False})
    findings=w.review_project('p',persist=False)
    row=next(x for x in findings if x['code']=='response_gaps')
    assert row['severity']=='info' and row['details']['rule_enabled'] is False
    assert row['details']['aggregate_rule_id']=='response_gaps'


def test_business_conflict_switch_applies_to_workspace_reservations():
    db.insert('projects',{'id':'p','name':'test','created_at':db.now(),'updated_at':db.now()})
    db.insert('jobs',{'id':'j','project_id':'p','mode':'review','status':'running','created_at':db.now()})
    with pytest.raises(ValueError):
        with w.editing('p'):pass
    with rr.scope({'operation_conflict':False}):
        with w.editing('p'):pass
        with w.editing(None,whole_workspace=True):pass


def test_default_cache_inputs_retain_previous_identity():
    assert w._rule_cache_inputs()=={}
    with rr.scope({'claim_units':False}):assert w._rule_cache_inputs()['review_rules']['claim_units'] is False


def test_analysis_coverage_off_accepts_actual_empty_array(monkeypatch,tmp_path):
    batch=[{'id':'c','text':'必须提供档案检索','document_id':'d'}]
    monkeypatch.setattr(w,'_cached_extraction_response',lambda *a:({'requirements':[]},tmp_path/'cache.json',True))
    monkeypatch.setattr(w,'_recover_prior_provenance_repairs',lambda *a:a[-1])
    with pytest.raises(provider.ProviderError):w._extract_validated_batch('j',batch,'key')
    with rr.scope({'analysis_output_coverage':False}):items,usage=w._extract_validated_batch('j',batch,'key')
    assert items==[]


def test_quotation_issue_uses_current_rule_without_changing_history():
    quote={'confirmed':True,'total_including_tax':'100','issues':['明细小计之和与含税总价不一致']}
    saved=copy.deepcopy(quote)
    assert not w.has_confirmed_quotation({'quotation':quote})
    with rr.scope({'quote_arithmetic':False}):assert w.has_confirmed_quotation({'quotation':quote})
    assert quote==saved


def test_preview_revision_switch_retains_actual_section_requirement(monkeypatch):
    from app import section_generation as sg
    monkeypatch.setattr(sg,'inputs',lambda *a:({'id':'s','title':'章'}, {'id':'p'}, [], 'new'))
    with pytest.raises(ValueError):sg.admit('s','old','','request')
    with rr.scope({'operation_revision':False}):
        admitted=sg.admit('s','old','','request')
    assert admitted['section_id']=='s' and admitted['revision']=='old'


def test_ingest_size_rule_can_be_disabled_with_real_readable_input(tmp_path,monkeypatch):
    path=tmp_path/'source.txt';path.write_text('真实可读的测试资料',encoding='utf-8')
    monkeypatch.setattr(w,'MAX_FILE_BYTES',1)
    with pytest.raises(ValueError):w.ingest(path)
    with rr.scope({'file_parse_safety':False}):result=w.ingest(path)
    assert result['parse_status']=='ready'
