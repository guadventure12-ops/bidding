"""Synthetic, offline procurement H1/H2 extraction. No application data."""
import copy
import json
import re
import pytest

from app.outline_sources import recommend


def block(text, i=0, *, doc='d', table=None):
    item = {'id': f'{doc}-c{i}', 'document_id': doc, 'locator': f'正文 / 段落 {i}',
            'ordinal': i, 'kind': 'paragraph', 'text': text}
    if table:
        item.update(kind='table_row', locator=f'正文 / 表格 {table["index"]} / 第 {table["row"]} 行', metadata={'table': table})
    return item


def row(cells, i, *, spans=None, doc='d', index=3):
    table = {'index': index, 'row': i, 'cells': cells, 'spans': spans or [1]*len(cells)}
    return block(' | '.join(cells), 100+i, doc=doc, table=table)


def scoring(title='整体设计方案', content='（1）应用架构设计；\n（2）数据库设计；\n未提供得0分。'):
    return [block('四、评分细则', 99), row(['序号', '评审标准', '分值'], 1, spans=[1, 2, 1]),
            row(['技术部分（55分）'], 2, spans=[4]), row(['1', title, content, '5'], 3)]


def explicit(children=True, title='整体设计方案'):
    result = [block('商务、技术文件目录', 1), block('一、'+title, 2)]
    if children:
        result += [block('1.1 应用设计', 3), block('1.2 数据设计', 4)]
    return result + [block('附件1：格式表', 5)]


def titles(plan):
    return [x['title'] for g in plan['groups'] for x in g['children']]


def test_actual_style_merged_header_expands_logical_second_and_third_column():
    plan = recommend(scoring())
    assert plan['mode'] == 'scoring'
    assert [g['title'] for g in plan['groups']] == ['整体设计方案']
    assert titles(plan) == ['应用架构设计', '数据库设计']
    assert plan['groups'][0]['category'] == 'technical'
    assert plan['groups'][0]['source_refs'][0]['column'] == 2
    assert all(c['source_refs'][0]['column'] == 3 for c in plan['groups'][0]['children'])


def test_full_source_hierarchy_is_authoritative_and_preserves_spelling():
    plan = recommend(explicit() + scoring(content='（1）不同的架构术语'))
    assert plan['mode'] == 'explicit'
    assert titles(plan) == ['应用设计', '数据设计']
    assert all(x['origin'] == 'tender' for g in plan['groups'] for x in g['children'])


def test_partial_h1_only_takes_matching_scoring_children():
    plan = recommend(explicit(False) + scoring())
    assert plan['mode'] == 'partial'
    assert len(plan['groups']) == 1
    assert plan['groups'][0]['origin'] == 'tender'
    assert titles(plan) == ['应用架构设计', '数据库设计']
    assert plan['groups'][0]['supplemented_from_scoring']


def test_partial_with_prescribed_children_keeps_them_and_supplements_other_parent():
    sources = explicit()[:-1] + [block('二、系统集成方案', 6), block('附件1：格式表', 7)]
    plan = recommend(sources + scoring() + scoring('系统集成方案', '（1）接口协议；（2）异常处理'))
    # Duplicate table identity with different content is invalid source, so use
    # a second independent table for the independent criterion.
    another = scoring('系统集成方案', '（1）接口协议；（2）异常处理')
    for x in another:
        if x.get('metadata'):
            x['metadata']['table']['index'] = 4
            x['locator'] = x['locator'].replace('表格 3', '表格 4')
    plan = recommend(sources + scoring() + another)
    assert plan['mode'] == 'partial'
    assert [c['title'] for c in plan['groups'][0]['children']] == ['应用设计', '数据设计']
    assert [c['title'] for c in plan['groups'][1]['children']] == ['接口协议', '异常处理']


def test_partial_unmatched_score_name_is_not_fuzzy_attached():
    plan = recommend(explicit(False, '系统方案') + scoring('系统总体设计方案'))
    assert len(plan['groups']) == 2
    assert plan['groups'][0]['children'] == []
    assert plan['groups'][1]['origin'] == 'scoring'
    assert any(n['code'] == 'unmatched_scoring_parent' for n in plan['notices'])


def test_explicit_complete_wording_does_not_add_score_h2():
    sources = explicit(False)
    sources[0]['text'] = '技术文件目录（完整）'
    plan = recommend(sources + scoring())
    assert plan['mode'] == 'explicit'
    assert titles(plan) == []


def test_empty_sources_do_not_invent_generic_modules():
    result = recommend([block('系统需求及验收标准', 1), block('1.1 随意正文标题', 2)])
    assert result['mode'] == 'empty'
    assert not result['groups']


def test_multiline_numbered_directory_keeps_original_block_quote_and_locator():
    source = block('一、需求理解\n1.1 需求范围\n1.2 实施边界\n二、功能方案\n三、实施方案', 2)
    original = copy.deepcopy(source)
    result = recommend([block('## 技术文件目录', 1), source, block('## 评分细则（演示）', 3)])
    assert [group['title'] for group in result['groups']] == ['需求理解', '功能方案', '实施方案']
    assert [child['title'] for child in result['groups'][0]['children']] == ['需求范围', '实施边界']
    reference = result['groups'][0]['children'][1]['source_refs'][0]
    assert reference['chunk_id'] == source['id']
    assert reference['locator'] == source['locator']
    assert reference['quote'] == source['text']
    assert reference['excerpt'] == '1.2 实施边界'
    assert source == original
    assert recommend([block('## 技术文件目录', 1), source])['groups'] == result['groups']


def test_scoring_heading_ends_explicit_directory_before_plain_rubric_text():
    result = recommend([block('技术文件目录'), block('一、实施方案', 1),
                        block('## 评分细则（合成）', 2), block('供应商方案评分内容', 3)])
    assert [group['title'] for group in result['groups']] == ['实施方案']
    assert result['groups'][0]['children'] == []


def test_scoring_response_topic_is_not_mistaken_for_rubric_heading():
    result = recommend([block('技术文件目录'), block('一、实施方案', 1),
                        block('1.1 评分响应方案', 2)])
    assert titles(result) == ['评分响应方案']


def test_mixed_multiline_directory_is_not_split_into_guessed_headings():
    source = block('一、技术方案\n这里是无法判定归属的续行', 1)
    result = recommend([block('技术文件目录'), source])
    assert not result['groups']
    assert any(item['code'] == 'ambiguous_multiline_directory' for item in result['notices'])


def test_unrelated_document_heading_is_not_response_directory():
    result = recommend([block('目录', 1), block('第一章采购须知', 2), block('一、评分办法', 3)])
    assert result['mode'] == 'empty'


def test_explicit_scope_stops_at_attachment_and_document_boundary():
    result = recommend(explicit() + [block('附件之后的标题', 6), block('孤立第二文件标题', 1, doc='d2')])
    assert [g['title'] for g in result['groups']] == ['整体设计方案']


def test_rubric_ranges_and_missing_score_are_not_chapters():
    content = '根据供应商提供的方案进行综合评审，分值范围为(0-5]分，评审内容包括：\n（1）接口设计的完整性；\n优秀得5分；良好得3分；一般得1分；\n未提供或内容偏离得0分。'
    plan = recommend(scoring(content=content))
    assert titles(plan) == ['接口设计的完整性']


def test_formula_rows_do_not_become_chapters_and_pricing_is_explicit():
    sources = scoring('报价', '1.报价算术平均值下浮5%为评标基准值；\n2.与评标基准价对比；\na.等于基准价得35分；\n偏离值公式=35-偏离值*0.5；\n供应商不能证明其报价合理性的，将其作为无效磋商处理。')
    sources[2] = row(['价格部分（35分）'], 2, spans=[4])
    result = recommend(sources)
    assert result['groups'][0]['category'] == 'pricing'
    assert titles(result) == []
    assert any(n['code'] == 'no_actionable_scoring_children' for n in result['notices'])


def test_full_source_text_keeps_numbers_and_proof_without_claiming_facts():
    content = '（1）响应机制（7×24小时、2小时响应）；\n（2）数据库支持Oracle、MySQL；\n未提供得0分。'
    result = recommend(scoring(content=content))
    assert len(titles(result)) == 2
    assert titles(result)[0] == '响应机制（7×24小时、2小时响应）'
    for child in result['groups'][0]['children']:
        assert child['source_text'] == content
        assert child['source_refs'][0]['quote'] == content
        assert 'approved' not in child


def test_certification_grading_levels_do_not_pollute_title():
    result = recommend(scoring('认证证书', '供应商具有以下有效证书：\n（1）信息安全管理体系认证证书ISO27001，得1分；\n（2）CMMI三级及以上认证证书：三级得1分，四级及以上得2分。'))
    assert titles(result) == ['信息安全管理体系认证证书ISO27001', 'CMMI三级及以上认证证书']


def test_design_and_demo_features_remain_distinct_requirements():
    text = '（1）根据产品设计方案综合评审，分值范围为(0-8]分，评审内容包括：\n电档管理（预归档、四性检测）功能设计完整性；\n（2）根据演示视频综合评审，分值范围为(0-2]分，评审内容包括：\n电档管理（预归档、四性检测）的演示完整性。'
    result = recommend(scoring('功能设计及演示', text))
    assert titles(result) == ['电档管理（预归档、四性检测）功能设计完整性', '电档管理（预归档、四性检测）的演示完整性']


def test_material_delivery_requirement_is_kept_when_failure_clause_follows():
    content = '【证明材料：供应商须将演示录制成视频文件，存储于U盘并递交。逾期未递交或无法播放，不予计分。】'
    result = recommend(scoring('系统演示', content))
    assert titles(result) == ['供应商须将演示录制成视频文件，存储于U盘并递交']
    assert result['groups'][0]['children'][0]['source_text'] == content


@pytest.mark.parametrize('penalty', [
    '不提供或无法提供有效证明的不得分',
    '未提供有效证明得0分',
    '无法提供有效证明材料的得零分',
    '未提交证书复印件不予计分',
    '否则不得分',
])
def test_mixed_material_request_title_removes_only_trailing_scoring_penalty(penalty):
    excerpt = '须提供有效期内的证书复印件，' + penalty
    content = '【证明材料：' + excerpt + '。】'
    result = recommend(scoring('认证证书', content))
    child = result['groups'][0]['children'][0]
    assert child['title'] == '须提供有效期内的证书复印件'
    assert child['source_text'] == content
    assert child['source_refs'][0]['quote'] == content
    assert penalty in child['source_excerpt']
    assert child['source_refs'][0]['excerpt'] == child['source_excerpt']


@pytest.mark.parametrize('content', [
    '须提供异常处理方案，不得中断归档服务',
    '系统应提供得分明细查询功能，不提供个人敏感数据',
    '须提供证书复印件，未提供原件时允许现场核对',
    '须提供有效期内的证书复印件，不得分包维护服务',
])
def test_title_penalty_trim_preserves_negative_business_conditions(content):
    result = recommend(scoring('响应方案', content))
    assert titles(result) == [content]


def test_completeness_statement_does_not_certify_parsing_and_partial_wording_wins():
    source = explicit()
    source[0]['text'] = '技术文件目录（部分）'
    result = recommend(source)
    assert result['mode'] == 'partial'


def test_plain_word_entries_keep_required_spelling_and_remove_only_format_note():
    source = [block('商务、技术文件目录', 1), block('认证证书（根据评分细则提供资料，格式自拟）', 2),
              block('技术条款偏离表（格式见附件12）', 3), block('附件12：技术条款偏离表', 4)]
    result = recommend(source)
    assert [g['title'] for g in result['groups']] == ['认证证书', '技术条款偏离表']
    assert result['groups'][0]['source_refs'][0]['quote'] == source[1]['text']


def test_parentheses_do_not_split_on_inner_semicolon():
    result = recommend(scoring(content='（1）系统接口（订单；合同）的校验；（2）异常处理'))
    assert titles(result) == ['系统接口（订单；合同）的校验', '异常处理']


def test_duplicate_import_fragments_are_one_source_row():
    source = scoring()
    duplicate = copy.deepcopy(source[-1])
    duplicate['id'] += '-part2'
    duplicate['locator'] += '（字符 5001–7000）'
    result = recommend(source + [duplicate])
    assert result['scoring_count'] == 1
    assert len(titles(result)) == 2


def test_reparse_chunk_ids_do_not_change_source_ids():
    original = scoring()
    reparsed = copy.deepcopy(original)
    for i, item in enumerate(reparsed): item['id'] = f'reparsed-{i}'
    a, b = recommend(original), recommend(reparsed)
    assert a['groups'][0]['id'] == b['groups'][0]['id']
    assert [c['id'] for c in a['groups'][0]['children']] == [c['id'] for c in b['groups'][0]['children']]
    assert all(re.fullmatch('source-[a-f0-9]{24}', c['id']) for c in a['groups'][0]['children'])


def test_conflicting_import_fragments_do_not_guess_table_contents():
    sources = scoring()
    duplicate = copy.deepcopy(sources[-1])
    duplicate['metadata']['table']['cells'][2] = '不同版本'
    result = recommend(sources + [duplicate])
    assert result['mode'] == 'empty'
    assert any(n['code'] == 'conflicting_table_row' for n in result['notices'])


def test_invalid_span_rejected_without_column_shift():
    sources = scoring()
    sources[-1]['metadata']['table']['spans'] = [1, 2, 1]
    result = recommend(sources)
    assert result['mode'] == 'empty'
    assert any(n['code'] == 'invalid_scoring_row' for n in result['notices'])


def test_horizontally_merged_body_cannot_be_split_into_h1_h2():
    source = scoring()[:-1] + [row(['1', '名称与内容混合', '5'], 3, spans=[1, 2, 1])]
    result = recommend(source)
    assert not result['groups']
    assert any(n['code'] == 'merged_scoring_body_columns' for n in result['notices'])


def test_missing_vertical_merge_marker_is_not_assumed_to_mean_previous_title():
    source = scoring() + [row(['', '', '另一条未知归属内容', ''], 4)]
    result = recommend(source)
    assert titles(result) == ['应用架构设计', '数据库设计']
    assert any(n['code'] == 'unresolved_merged_scoring_title' for n in result['notices'])


def test_scoring_index_page_is_not_source_scoring_table():
    source = [row(['序号', '评审因素', '评分标准', '对应页码'], 1), row(['1', '测试项', '接口设计', '35'], 2)]
    assert recommend(source)['mode'] == 'empty'


def test_score_points_in_third_column_do_not_guess_other_column():
    source = [row(['序号', '评分因素', '分值', '评分标准'], 1), row(['1', '方案', '5', '接口设计'], 2)]
    result = recommend(source)
    assert not result['groups']
    assert any(n['code'] == 'unsupported_scoring_columns' for n in result['notices'])


def test_source_row_requirements_are_attached_to_specific_topic_only():
    source = scoring()
    target = source[-1]
    reqs = [{'id': 'architecture', 'chunk_id': target['id'], 'document_id': 'd', 'quote': '应用架构设计'},
            {'id': 'database', 'chunk_id': target['id'], 'document_id': 'd', 'quote': '数据库设计'},
            {'id': 'other-source', 'chunk_id': 'x', 'document_id': 'x', 'quote': '应用架构设计'},
            {'id': 'rubric', 'chunk_id': target['id'], 'quote': '未提供得0分'}]
    children = recommend(source, reqs)['groups'][0]['children']
    assert children[0]['requirement_ids'] == ['architecture']
    # The conservative six-character quote guard refuses extremely short text.
    assert children[1]['requirement_ids'] == []


def test_requirement_locator_fallback_preserves_reparse_link_without_fuzzy_content():
    source = scoring(content='（1）数据库总体设计；（2）系统接口设计')
    target = source[-1]
    reqs = [{'id': 'r', 'document_id': 'd', 'locator': target['locator'], 'chunk_id': 'old', 'quote': '数据库总体设计'}]
    children = recommend(source, reqs)['groups'][0]['children']
    assert children[0]['requirement_ids'] == ['r']
    assert children[1]['requirement_ids'] == []


def test_duplicate_topic_preserves_each_row_source():
    source = scoring() + [row(['2', '整体设计方案', '（1）应用架构设计', '1'], 4)]
    result = recommend(source)
    assert len(result['groups']) == 1
    assert len(titles(result)) == 2
    assert len(result['groups'][0]['children'][0]['source_refs']) == 2


def test_different_documents_same_table_index_not_confused():
    a = scoring()
    b = scoring('系统集成方案', '（1）接口清单设计')
    for item in b: item['document_id'] = 'd2'; item['id'] = 'd2-'+item['id']
    assert recommend(a+b)['scoring_count'] == 2


def test_string_json_metadata_supported_and_inputs_unchanged():
    source = scoring()
    for item in source:
        if 'metadata' in item: item['metadata'] = json.dumps(item['metadata'], ensure_ascii=False)
    before = copy.deepcopy(source)
    result = recommend(source)
    assert result['mode'] == 'scoring'
    assert source == before


def test_long_criterion_retains_original_full_text_and_never_changes_numbers():
    text = '（1）' + '系统接口与档案管理要求' * 15 + '，指标为12345且须提供完整证明材料'
    result = recommend(scoring(content=text))
    child = result['groups'][0]['children'][0]
    assert len(child['title']) == 100
    assert child['title'].endswith('…')
    assert child['source_text'] == text
    assert '12345' in child['source_excerpt']


def test_three_level_decimal_headings_do_not_create_repeated_h1():
    source = [block('技术文件目录', 1), block('一、方案', 2), block('1.1 系统设计', 3),
              block('1.1.1 内部小节', 4), block('附件1：结束', 5)]
    result = recommend(source)
    assert len(result['groups']) == 1
    assert titles(result) == ['系统设计']
    assert result['groups'][0]['children'][0]['children'][0]['title'] == '内部小节'
