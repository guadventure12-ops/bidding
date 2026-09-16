"""Small explicit numeric-role boundaries; no DB, model or network operation."""
import copy

from app.proposal_numeric_review import unsupported_numbers


def source(text, role=None, id='source'):
    row={'id':id,'text':text,'support_text':text}
    if role:row['reference_role']=role
    return row


def test_actual_product_number_requires_fact_source_and_exact_value_unit():
    fact=source('本产品支持32GB数据处理。')
    assert unsupported_numbers('本产品支持32GB数据处理。[E:source]',[fact])==[]
    issues=unsupported_numbers('本产品支持16GB数据处理。[E:source]',[fact])
    assert [r['value'] for r in issues]==['16GB']
    assert unsupported_numbers('本产品支持32GB数据处理。',[fact])==[]  # Exact approved source reuse.


def test_plan_and_achieved_capacity_in_same_paragraph_are_independent():
    evidence=[source('建议16核。','conditional_resource_recommendation')]
    text='建议16核。[E:source] 产品已经16核。[E:source]'
    issues=unsupported_numbers(text,evidence)
    assert len(issues)==1 and issues[0]['code']=='numeric_requires_enterprise_fact'
    assert '产品已经16核' in issues[0]['statement']
    assert text[issues[0]['start']:issues[0]['end']]=='16核'


def test_mixed_clauses_cannot_borrow_a_plan_tone_for_achieved_claim():
    evidence=[source('建议16核。','conditional_resource_recommendation')]
    issues=unsupported_numbers('建议16核，产品已经16核。[E:source]',evidence)
    assert len(issues)==1 and '产品已经' in issues[0]['statement']


def test_conditional_resource_numbers_keep_original_range_and_adjustment():
    evidence=[source('在100GB数据量以内，建议16核，按负载调整。','conditional_resource_recommendation')]
    assert unsupported_numbers('在100GB数据量以内，拟采用16核，并按负载调整。[E:source]',evidence)==[]
    issues=unsupported_numbers('在200GB数据量以内，拟采用16核，并按负载调整。[E:source]',evidence)
    assert any(r['value']=='16核' and r['code']=='numeric_source_condition_changed' for r in issues)
    missing=unsupported_numbers('建议16核。[E:source]',evidence)
    assert missing[0]['code']=='numeric_source_condition_changed'


def test_plan_headcount_needs_explicit_plan_language_and_sentence_citation():
    evidence=[source('计划投入5人。','same_tender_proposed_plan')]
    assert unsupported_numbers('拟投入5人。[E:source]',evidence)==[]
    assert unsupported_numbers('本项目配置5人。[E:source]',evidence)[0]['code']=='numeric_proposal_language_missing'
    assert unsupported_numbers('计划投入5人。',evidence)[0]['code']=='numeric_role_not_cited'
    assert unsupported_numbers('我方已投入5人。[E:source]',evidence)[0]['code']=='numeric_requires_enterprise_fact'


def test_numeric_substrings_do_not_match_and_unknown_roles_remain_unverified():
    assert unsupported_numbers('建议16核。[E:source]',[source('建议116核。','conditional_resource_recommendation')])
    issues=unsupported_numbers('建议16核。[E:source]',[source('建议16核。','customer_unclassified')])
    assert issues[0]['code']=='numeric_unknown_source_role'


def test_procurement_quote_is_only_a_constraint_not_a_completed_company_fact():
    quotes=['项目建设周期为6个月。']
    assert unsupported_numbers('采购要求：项目建设周期为6个月。',[],source_quotes=quotes)==[]
    assert unsupported_numbers('我方已经完成6个月建设，采购要求仅供参考。',[],source_quotes=quotes)
    # A numeric-source exemption is not recognition that a declaration is true,
    # signed, stamped or delivered; those tasks stay with the caller.
    assert unsupported_numbers('原声明模板：最近3年无重大违法记录。',[],source_quotes=['最近3年无重大违法记录。'])==[]
    assert unsupported_numbers('最近3年无重大违法记录。',[],source_quotes=['最近3年无重大违法记录。'])


def test_exact_procurement_quote_can_describe_required_capability_without_becoming_company_claim():
    quote='产品支持100并发用户。'
    assert unsupported_numbers('采购要求：产品支持100并发用户。',[],source_quotes=[quote])==[]
    assert unsupported_numbers('采购要求：产品支持100并发用户，我方已达到100并发用户。',[],source_quotes=[quote])


def test_reviewed_deliverable_date_only_supports_exact_field_not_other_claims():
    fields=['签署日期：2026年9月13日']
    assert unsupported_numbers(fields[0],[],deliverable_context=fields)==[]
    issues=unsupported_numbers('签署日期：2026年9月14日',[],deliverable_context=fields)
    assert issues[0]['value']=='2026年9月14日'
    assert unsupported_numbers('实测支持32GB数据处理。',[],deliverable_context=['实测支持32GB数据处理。'])


def test_same_year_different_month_does_not_pass_as_same_plan_date():
    evidence=[source('计划于2026年7月完成部署。','same_tender_proposed_plan')]
    assert unsupported_numbers('拟于2026年7月完成部署。[E:source]',evidence)==[]
    issues=unsupported_numbers('拟于2026年12月完成部署。[E:source]',evidence)
    assert [r['value'] for r in issues]==['2026年12月']
    assert unsupported_numbers('拟于2026-07-01部署。[E:source]',[source('计划2026年7月1日部署。','same_tender_proposed_plan')])==[]


def test_recommendation_or_negative_text_in_ordinary_source_is_not_actual_capacity():
    assert unsupported_numbers('产品现有16核。[E:source]',[source('建议16核。')])
    assert unsupported_numbers('产品支持32GB处理。[E:source]',[source('产品不支持32GB处理。')])


def test_only_current_sentences_citations_are_used_and_input_is_unchanged():
    evidence=[source('计划投入5人。','same_tender_proposed_plan')]
    before=copy.deepcopy(evidence)
    text='计划投入5人。[E:source]\n我方已投入5人。'
    issues=unsupported_numbers(text,evidence)
    assert len(issues)==1 and '已投入' in issues[0]['statement']
    assert evidence==before
    assert unsupported_numbers('[E:resource16GB] 详见 https://example.com/2026-09-13。',evidence)==[]
