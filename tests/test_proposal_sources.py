"""Pure synthetic evidence tests: no database, model, file or network writes."""
import copy
import hashlib
import json
from datetime import date, timedelta

import pytest

from app import evidence_retrieval as er, review_rules


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def project(ids=('product',), **metadata):
    return {'id': 'project-a', 'domain': 'archive', 'metadata': {
        'generation_profile': 'technical_proposal',
        'proposal_source_policy': {'mode': 'approved_facts_only', 'fact_document_ids': list(ids),
                                   'reference_document_ids': []}, **metadata}}


def row(id='chunk', document='product', text='支持档案检索。', **changes):
    return {'id': id, 'document_id': document, 'text': text, 'kind': 'paragraph', 'locator': '原文段落1',
            'document_name': '企业产品说明.md', 'source_type': 'knowledge', 'document_project_id': None,
            'document_status': 'approved', 'scope': 'archive', 'valid_until': '', 'parse_status': 'ready',
            'document_sha256': sha('source-file'), 'document_updated_at': '2026-09-13T00:00:00+00:00',
            'document_metadata': {}, 'metadata': {}, **changes}


def approve_range(r, body=None, mode='parsed'):
    r = copy.deepcopy(r)
    r['document_metadata']['knowledge_approval'] = {
        'mode': mode, 'source_sha256': r['document_sha256'], 'recognized_scope': '已核对的原文范围',
        'entries': [{'id': r['id'], 'sha256': sha(r['text']), 'text': body if body is not None else r['text'],
                     'locator': r['locator']}]}
    return r


@pytest.fixture(autouse=True)
def no_database_or_network(monkeypatch):
    from app import db
    def forbidden(*args, **kwargs):
        pytest.fail('Proposal source helpers must remain pure')
    monkeypatch.setattr(db, 'connect', forbidden)
    monkeypatch.setattr(db, 'get_settings', forbidden)
    monkeypatch.setattr(db, 'all', forbidden)


def filtered(rows, p=None):
    with review_rules.scope({key: False for key in review_rules.IDS}):
        accepted, diagnostics = er.filter_proposal_rows(rows, p or project())
        index = er.build_index(accepted)
        # The local strict boundary cannot mutate the surrounding switches.
        assert all(not review_rules.active(key) for key in review_rules.IDS)
    return index.rows, diagnostics


def test_all_review_rules_off_still_filters_identity_reference_and_status():
    p = project(['product', 'tender', 'reference', 'draft', 'pending'])
    p['metadata']['proposal_source_policy']['reference_document_ids'] = ['reference']
    rows = [approve_range(row()), row(document='tender', source_type='tender', document_project_id='project-a'),
            row(document='reference'), row(document='draft', document_metadata={'ai_generated': True}),
            row(document='pending', document_status='pending')]
    original = copy.deepcopy(rows)
    accepted, info = filtered(rows, p)
    assert [r['document_id'] for r in accepted] == ['product']
    assert info['input_rows'] == 5 and info['accepted_rows'] == 1 and info['rejected_rows'] == 4
    assert info['reason_counts'] == {'not_knowledge': 1, 'reference_only': 1,
                                     'excluded_origin_or_project': 1, 'not_approved': 1}
    assert rows == original


def test_newly_approved_readable_original_is_retrievable_with_source_hash_and_constraints():
    r = approve_range(row(text='支持星河档案索引。', document_metadata={
        'product_version': '4.0及以后', 'source_url': 'https://example.test/product',
        'scope_limit': '仅本地部署', 'project_ids': ['project-a'], 'limitations': ['需配置索引']}))
    accepted, info = filtered([r])
    hits = er.search(er.build_index(accepted), '星河档案索引')
    assert info['accepted_rows'] == 1 and len(hits) == 1
    assert hits[0]['text'] == hits[0]['support_text'] == r['text']
    assert hits[0]['source_constraints']['document_sha256'] == r['document_sha256']
    assert hits[0]['source_constraints']['source_text_sha256'] == sha(r['text'])
    assert hits[0]['source_constraints']['product_version'] == '4.0及以后'
    assert hits[0]['source_constraints']['project_ids'] == ['project-a']
    assert hits[0]['document_metadata'] == r['document_metadata']
    assert 'supported' not in hits[0]  # A candidate does not certify a chapter.


@pytest.mark.parametrize('changes,code', [
    ({'valid_until': '2000-01-01'}, 'expired'),
    ({'valid_until': 'not-a-date'}, 'invalid_expiry'),
    ({'document_status': 'expired'}, 'not_approved'),
    ({'scope': 'expense'}, 'wrong_scope'),
    ({'scope': 'historical'}, 'wrong_scope'),
    ({'parse_status': 'error'}, 'not_ready'),
    ({'document_metadata': {'project_ids': ['other-project']}}, 'excluded_origin_or_project'),
    ({'document_metadata': {'source_kind': 'customer_specific'}}, 'excluded_origin_or_project'),
    ({'document_metadata': {'source_kind': 'customer_case'}}, 'customer_case'),
    ({'metadata': {'ai_generated': True}}, 'excluded_origin_or_project'),
    ({'text': '甲方要求：支持量子检索。'}, 'excluded_statement'),
    ({'text': '客户A已开通独有扩展。'}, 'excluded_statement'),
    ({'document_sha256': ''}, 'missing_source_hash'),
])
def test_expiry_scope_parse_source_and_statement_fail_closed(changes, code):
    accepted, info = filtered([row(**changes)])
    assert accepted == []
    assert info['reason_counts'] == {code: 1}


@pytest.mark.parametrize('expiry', ['', date.today().isoformat(), (date.today()+timedelta(days=1)).isoformat()])
def test_unset_expiry_and_current_day_are_not_expired(expiry):
    accepted, info = filtered([row(valid_until=expiry, text='支持。', page_count=0)])
    assert len(accepted) == 1
    assert accepted[0]['valid_until'] == expiry


def test_partial_approval_does_not_expand_when_review_flags_off_or_index_called_again():
    original = '支持档案检索。\n未确认的宇宙迁移。'
    partial = approve_range(row(text=original), '支持档案检索。', mode='partial')
    extra = row(id='outside', text='支持超光速迁移。', document_metadata=partial['document_metadata'])
    accepted, info = filtered([partial, extra])
    assert [r['text'] for r in accepted] == ['支持档案检索。']
    assert info['reason_counts'] == {'outside_approved_range': 1}
    with review_rules.scope({k: False for k in review_rules.IDS}):
        index = er.build_index(accepted)
        assert er.search(index, '宇宙迁移') == []
        assert er.search(index, '超光速迁移') == []
        assert er.search(index, '档案检索')[0]['text'] == '支持档案检索。'
    tampered = copy.deepcopy(accepted)
    tampered[0]['text'] += '\n支持超光速迁移。'
    assert er.build_index(tampered).rows == []


@pytest.mark.parametrize('change', ['chunk', 'file', 'fabricated_approval'])
def test_approval_hash_and_literal_source_are_not_inferred_from_evidence_ids(change):
    r = approve_range(row())
    if change == 'chunk':
        r['text'] += '支持新增未审功能。'
    elif change == 'file':
        r['document_sha256'] = sha('changed source')
    else:
        r['document_metadata']['knowledge_approval']['entries'][0]['text'] = '支持自动造章。'
    accepted, info = filtered([r])
    assert accepted == [] and info['rejected_rows'] == 1


def test_mixed_approved_final_uses_only_explicit_chunk_intersection_not_filename_heuristic():
    p = project(['mixed-final'])
    p['metadata']['proposal_source_policy']['fact_chunk_ids_by_document'] = {'mixed-final': ['product-paragraph']}
    approved = approve_range(row('product-paragraph', 'mixed-final', document_name='示例 技术标_Final.docx'))
    personnel = row('personnel', 'mixed-final', text='项目经理为甲。', document_name=approved['document_name'])
    accepted, info = filtered([approved, personnel], p)
    assert [r['id'] for r in accepted] == ['product-paragraph']
    assert accepted[0]['document_name'] == '示例 技术标_Final.docx'
    assert info['reason_counts'] == {'outside_chunk_selection': 1}
    p['metadata']['proposal_source_policy']['fact_chunk_ids_by_document']['mixed-final'] = []
    assert filtered([approved], p)[0] == []
    p['metadata']['proposal_source_policy']['fact_chunk_ids_by_document']['mixed-final'] = ['product-paragraph']
    p['metadata']['proposal_source_policy']['reference_document_ids'] = ['mixed-final']
    assert filtered([approved], p)[1]['reason_counts'] == {'reference_only': 1}


def test_policy_selection_intersects_version_bound_trusted_sources():
    a,b = row(), row('b', 'second')
    p = project(['product', 'second'])
    p['metadata']['trusted_sources'] = {'product': {'sha256': a['document_sha256'], 'source_updated_at': a['document_updated_at']}}
    accepted, info = filtered([a,b], p)
    assert [r['document_id'] for r in accepted] == ['product']
    assert info['reason_counts'] == {'project_trust_changed': 1}
    p['metadata']['trusted_sources']['product']['source_updated_at'] = 'older'
    assert filtered([a], p)[0] == []
    p['metadata']['trusted_sources'] = {}
    assert filtered([a], p)[0] == []


def test_curated_scope_and_original_provenance_are_retained():
    quote='支持星河检索。'
    metadata={'evidence_kind': 'curated_extract', 'audited_excerpts': [
        {'quote': quote, 'sha256': sha(quote), 'source_block_id': 'original-block', 'locator': '原文第3段'}],
        'source_document_id': 'original-doc', 'source_date': '2026-01-01', 'authorization_scope': '用于草稿'}
    good=row(text='来源包装\n原文：\n'+quote, document_metadata=metadata)
    bad=row(id='bad', text='来源包装\n原文：\n支持超光速迁移。', document_metadata=metadata)
    accepted, info = filtered([good,bad])
    assert [r['text'] for r in accepted] == [quote]
    assert accepted[0]['source_excerpt']['source_block_id'] == 'original-block'
    assert accepted[0]['source_constraints']['authorization_scope'] == '用于草稿'
    assert info['reason_counts'] == {'outside_curated_range': 1}


def test_links_are_not_inferred_attachment_contents_and_metadata_map_works():
    p=project()
    raw=row(text='支持加密。\n附件下载：\n[白皮书](https://example.test/source.pdf)')
    linked=row(id='link', text='[白皮书](https://example.test/source.pdf)')
    with review_rules.scope({k:False for k in review_rules.IDS}):
        accepted,info=er.filter_proposal_rows([raw,linked],p,{'product':json.dumps({'product_version':'4.0'})})
        hits=er.search(er.build_index(accepted),'加密')
    assert hits[0]['text']=='支持加密。'
    assert hits[0]['source_constraints']['product_version']=='4.0'
    assert info['reason_counts']=={'no_readable_body':1}


@pytest.mark.parametrize('policy', [None, {}, {'mode':'approved_facts_only','fact_document_ids':'product'},
                                  {'mode':'approved_facts_only','fact_document_ids':['product'],'fact_chunk_ids_by_document':{'product':'chunk'}}])
def test_profile_with_missing_or_malformed_policy_does_not_fallback(policy):
    p=project(proposal_source_policy=policy)
    accepted,info=filtered([row()],p)
    assert accepted==[] and info['reason_counts']=={'invalid_policy':1}


def test_legacy_profile_passthrough_and_disabled_rule_behavior_are_unchanged():
    rows=[row(text='定位\n原文：\n支持检索。',document_metadata={'evidence_kind':'curated_extract'}),
          row('additional',text='尚未认可的扩展功能。')]
    with review_rules.scope({k:False for k in review_rules.IDS}):
        before=er.build_index(rows)
        accepted,info=er.filter_proposal_rows(rows,{'id':'legacy','domain':'archive','metadata':{}})
        after=er.build_index(accepted)
    assert accepted==rows and after.rows==before.rows and after.scoring_texts==before.scoring_texts
    assert info['active'] is False and info['rejected_rows']==0


def test_product_import_failure_reason_is_a_feature_and_original_text_is_unchanged():
    body='对于导入失败的数据，系统能够提示失败原因，方便管理员定位问题并修正后重新导入。'
    original=approve_range(row(text=body))
    accepted,info=filtered([original])
    assert info['rejected_rows']==0
    assert accepted[0]['text']==accepted[0]['support_text']==body
    assert accepted[0]['source_constraints']['source_text_sha256']==sha(body)


@pytest.mark.parametrize('body',[
    '项目失败原因是没有按期交付。',
    '客户失败案例：系统能显示导入失败原因。',
    '项目失败原因是接口改造延期，系统日志仍记录导入失败原因。',
])
def test_failure_cases_are_not_whitelisted_by_nearby_error_feature_words(body):
    accepted,info=filtered([approve_range(row(text=body))])
    assert accepted==[] and info['reason_counts']=={'excluded_statement':1}


@pytest.mark.parametrize('meta',[{'failure_case':True},{'source_kind':'postmortem'}, {'failure_review':True}])
def test_explicit_postmortem_source_remains_excluded_even_with_valid_product_wording(meta):
    original=approve_range(row(text='系统可查询导入失败原因，并支持重试。',document_metadata=meta))
    accepted,info=filtered([original])
    assert accepted==[] and info['rejected_rows']==1
